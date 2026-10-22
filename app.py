"""EventOS MVP — self-hosted FastAPI/SQLite application.

Development-first reference implementation. Review README deployment notes before exposure
on the public Internet. Demo data is enabled locally by default.
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import os
import re
import secrets
import sqlite3
import production_database
import production_mariadb
import production_launch
from platform_studio import install as install_platform_studio, render_landing, site_settings
from platform_analytics import install as install_platform_analytics
from account_support import install as install_account_support
from party_hub import install as install_party_hub
from diy_planner import install as install_diy_planner
from growth_center import install as install_growth_center
from email_notifications import install as install_email_notifications, enqueue_user as queue_email, workspace_members as email_workspace_members
from invoice_payments import install as install_invoice_payments
from monetization_center import install as install_monetization
from production_reconciliation import install as install_subscription_reconciliation
from agency_connect import install as install_agency_connect
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from rsvp_forms import install_rsvp_forms, public_form, save_guest_response
from command_center import build_command_center
from checkin import install_checkin
from branding_studio import install_branding, domain_root
from invitation_designs import install_invitation_designer, get_design
from invitation_assets import install_invitation_assets, event_media
from guest_crm import install_guest_crm
from event_operations import install_operations
from saas_access import migrate as migrate_saas, install_platform, require_code as saas_requires_code, eligible_code as saas_eligible_code, redeem as saas_redeem, enforce as saas_enforce, limits as saas_limits, LIMITS as SAAS_DEFAULTS, route_feature as saas_route_feature, public_route_feature as saas_public_feature, feature_enabled as saas_feature_enabled
from workspace_invites import install_invites, migrate as migrate_team_codes, has_team_code, redeem as join_team
from event_gallery import install_gallery
from gift_registry import install_registry
from event_websites import install_event_websites
from agency_portal import install_agency
from agency_advanced import install as install_agency_advanced
from guest_experience import install as install_guest_experience
import phase7_auth
from phase7_auth import install_auth, migrate as migrate_phase7_auth, verify_required, mail_enabled as phase7_mail_enabled, token_for, deliver_verification, issuance_limit
from phase7_billing import install_billing, migrate as migrate_phase7_billing

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get("EVENTOS_DB_PATH", str(ROOT / "eventos.sqlite3"))
SESSION_DAYS = 7
COOKIE_SECURE = os.environ.get("EVENTOS_SECURE_COOKIES", "0") == "1"
PUBLIC_DEMO = os.environ.get("EVENTOS_PUBLIC_DEMO", "0") == "1"
if production_database.postgres_enabled() and PUBLIC_DEMO:
    raise RuntimeError("PostgreSQL production mode is incompatible with public demo seeding")
GATE_PASSWORD = os.environ.get("EVENTOS_GATE_PASSWORD", "")
GATE_USERNAME = "eventos"
if PUBLIC_DEMO:
    if len(GATE_PASSWORD) < 12:
        raise RuntimeError("Public demo requires a 12+ character EVENTOS_GATE_PASSWORD")
    if not COOKIE_SECURE:
        raise RuntimeError("Public demo requires EVENTOS_SECURE_COOKIES=1")

NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

app = FastAPI(title="EventOS MVP", version="0.2.0", docs_url="/api/docs")


@app.middleware("http")
async def saas_security_headers(request: Request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('Origin','')
        if origin:
            parsed=urlparse(origin)
            if parsed.scheme not in ('http','https') or parsed.netloc.lower()!=request.headers.get('host','').lower():
                return JSONResponse({'detail':'Cross-origin request blocked'},status_code=403)
        if request.headers.get('Sec-Fetch-Site','')=='cross-site':
            return JSONResponse({'detail':'Cross-site request blocked'},status_code=403)
    response=await call_next(request)
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Permissions-Policy','camera=(self), microphone=()')
    if COOKIE_SECURE and request.headers.get('x-forwarded-proto',request.url.scheme)=='https':
        response.headers.setdefault('Strict-Transport-Security','max-age=31536000')
    return response



@app.middleware("http")
async def enforce_workspace_features(request:Request,call_next):
    """Platform feature flags cover both authenticated APIs and unlisted public portals.

    Internal app navigation hiding is cosmetic; this enforcement is server-side.
    """
    path=request.url.path
    found=saas_route_feature(path)
    with db() as con:
        try:
            if found:
                wid,feature=found
                if feature:
                    access(request,con,wid)
                    if not saas_feature_enabled(con,wid,feature):
                        return JSONResponse({'detail':'This feature is disabled for this workspace'},status_code=403)
            elif path.startswith(('/api/gallery/','/api/registry/','/api/event-site/','/api/client/',
                                  '/api/checkin/','/api/rsvp/','/checkin/',
                                  '/photos/','/gifts/','/event/','/client/')):
                found=saas_public_feature(con,path)
                if found and not saas_feature_enabled(con,*found):
                    return JSONResponse({'detail':'This feature is not available'},status_code=404)
        except HTTPException as e:
            return JSONResponse({'detail':e.detail},status_code=e.status_code)
    return await call_next(request)


@app.middleware("http")
async def demo_preview_access_gate(request: Request, call_next):
    """Restrict the public preview behind a separate, deployment-only password.

    This is an additional barrier for a temporary DEMO, not production authentication.
    Login/session permissions are still enforced by normal application routes.
    """
    if not PUBLIC_DEMO or request.url.path == "/api/health":
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    authorized = False
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            authorized = (secrets.compare_digest(username, GATE_USERNAME)
                          and secrets.compare_digest(password, GATE_PASSWORD))
        except (ValueError, UnicodeError, binascii.Error):
            pass
    if not authorized:
        return Response(
            "EventOS private preview: enter the demo site password to continue.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="EventOS private phone demo"',
                     "Cache-Control": "no-store"},
            media_type="text/plain",
        )

    # Same-origin defense-in-depth for browser form submissions.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin", "")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc.lower() != request.headers.get("host", "").lower():
                return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        if request.headers.get("Sec-Fetch-Site", "") == "cross-site":
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    return await call_next(request)


def uid() -> str:
    return str(uuid.uuid4())


@contextmanager
def db():
    if production_database.postgres_enabled():
        connection = production_database.connect()
    elif production_mariadb.enabled():
        connection = production_mariadb.connect()
    else:
        connection = sqlite3.connect(DB_PATH, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record(row):
    return dict(row) if row is not None else None


def records(rows):
    return [dict(r) for r in rows]


def initialize():
    if not (production_database.postgres_enabled() or production_mariadb.enabled()):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('personal','business')),
          currency TEXT NOT NULL DEFAULT 'CAD', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memberships (
          workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
          role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')), PRIMARY KEY(workspace_id,user_id));
        CREATE TABLE IF NOT EXISTS contacts (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
          email TEXT DEFAULT '', phone TEXT DEFAULT '', kind TEXT DEFAULT 'client', notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          parent_event_id TEXT REFERENCES events(id), client_contact_id TEXT REFERENCES contacts(id),
          name TEXT NOT NULL, type TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT,
          location TEXT DEFAULT '', expected_guests INTEGER NOT NULL DEFAULT 0, budget REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'Draft', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guests (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id), contact_id TEXT REFERENCES contacts(id),
          name TEXT NOT NULL, email TEXT DEFAULT '', party_size INTEGER NOT NULL DEFAULT 1,
          group_name TEXT DEFAULT '', rsvp TEXT NOT NULL DEFAULT 'Pending',
          invitation_token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, phone TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id), title TEXT NOT NULL, due_date TEXT DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'Medium', status TEXT NOT NULL DEFAULT 'Open', assigned_to TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS budget_items (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id), name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Other',
          estimated REAL NOT NULL DEFAULT 0, committed REAL NOT NULL DEFAULT 0,
          actual REAL NOT NULL DEFAULT 0, paid REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS vendors (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Other', email TEXT DEFAULT '',
          phone TEXT DEFAULT '', notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS event_vendors (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id), vendor_id TEXT NOT NULL REFERENCES vendors(id),
          scope TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Shortlisted', committed REAL NOT NULL DEFAULT 0,
          UNIQUE(event_id,vendor_id));
        CREATE TABLE IF NOT EXISTS leads (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          contact_id TEXT NOT NULL REFERENCES contacts(id), title TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT 'New', value REAL NOT NULL DEFAULT 0,
          expected_date TEXT DEFAULT '', event_id TEXT REFERENCES events(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_items (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id), title TEXT NOT NULL,
          start_at TEXT NOT NULL, end_at TEXT DEFAULT '', location TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_events_ws ON events(workspace_id,start_date);
        CREATE INDEX IF NOT EXISTS idx_guests_event ON guests(event_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_event ON tasks(event_id);
        CREATE INDEX IF NOT EXISTS idx_budget_event ON budget_items(event_id);
        CREATE INDEX IF NOT EXISTS idx_leads_ws ON leads(workspace_id,stage);
        CREATE INDEX IF NOT EXISTS idx_membership_user ON memberships(user_id);
        """)
        # Upgrade existing SQLite installations without resetting guest data.
        columns={column['name'] for column in con.execute('PRAGMA table_info(guests)')}
        if 'phone' not in columns:
            con.execute("ALTER TABLE guests ADD COLUMN phone TEXT DEFAULT ''")
        if 'category' not in columns:
            con.execute("ALTER TABLE guests ADD COLUMN category TEXT NOT NULL DEFAULT 'General'")
        if 'notes' not in columns:
            con.execute("ALTER TABLE guests ADD COLUMN notes TEXT NOT NULL DEFAULT ''")


def password_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def valid_password(password: str, stored: str) -> bool:
    try:
        salt, hashed = stored.split(":", 1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return secrets.compare_digest(actual, bytes.fromhex(hashed))
    except (ValueError, TypeError):
        return False


def create_workspace(con, user_id, name, kind, currency="CAD"):
    w = uid()
    con.execute("INSERT INTO workspaces(id,name,type,currency,created_at) VALUES (?,?,?,?,?)", (w,name,kind,currency,NOW()))
    con.execute("INSERT INTO memberships VALUES (?,?,?)", (w,user_id,'owner'))
    return w


def seed_demo():
    if os.environ.get("EVENTOS_DEMO_SEED", "0") != "1":
        return
    with db() as con:
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        u = uid()
        con.execute("INSERT INTO users(id,email,full_name,password_hash,created_at) VALUES (?,?,?,?,?)", (u,'demo@eventos.local','Alex Morgan',password_hash('EventOSdemo2026!'),NOW()))
        p = create_workspace(con,u,'My Personal Space','personal')
        b = create_workspace(con,u,'Northstar Events','business')
        pe, be1, be2 = uid(), uid(), uid()
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (pe,p,None,None,'Maya’s Birthday','Birthday','2026-11-20',None,'The Garden Loft',70,6000,'Planning',NOW()),
            (be1,b,None,None,'Smith Wedding','Wedding','2026-12-12',None,'Riverstone Hall',180,32000,'Confirmed',NOW()),
            (be2,b,None,None,'Annual Leadership Summit','Conference','2027-01-18',None,'Convention Centre',450,75000,'Planning',NOW())
        ])
        for e,people in [(pe,[('Jordan Lee','jordan@example.com','Confirmed',2),('Taylor Kim','taylor@example.com','Pending',1),('Riley Chen','riley@example.com','Confirmed',2)]),(be1,[('Sophie Taylor','sophie@example.com','Confirmed',2),('Daniel Lee','daniel@example.com','Pending',1),('Harper Jones','harper@example.com','Declined',1)])]:
            for n,email,rsvp,seats in people:
                con.execute("INSERT INTO guests (id,workspace_id,event_id,contact_id,name,email,party_size,group_name,rsvp,invitation_token,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (uid(),p if e==pe else b,e,None,n,email,seats,'Friends & family',rsvp,secrets.token_urlsafe(24),NOW()))
        for e,w,t,d,priority,status in [(pe,p,'Confirm catering menu','2026-11-02','High','Open'),(pe,p,'Send invitations','2026-11-01','Medium','Done'),(be1,b,'Finalize floor plan','2026-11-30','High','Open'),(be1,b,'Confirm lighting supplier','2026-12-03','Medium','Open')]:
            con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", (uid(),w,e,t,d,priority,status,'Alex Morgan'))
        for e,w,n,c,est,com,actual,paid in [(pe,p,'Catering','Food',1800,1650,500,500),(pe,p,'Decorations','Decor',650,600,250,250),(be1,b,'Venue reservation','Venue',8500,8500,8500,3000),(be1,b,'Catering package','Food',12000,11500,0,2000)]:
            con.execute("INSERT INTO budget_items VALUES (?,?,?,?,?,?,?,?,?)", (uid(),w,e,n,c,est,com,actual,paid))
        for w,n,c,email in [(p,'Bloom Florals','Florist','hello@bloom.example'),(b,'Northstar Catering','Catering','contact@northstar.example'),(b,'Bright Audio','AV','team@bright.example')]:
            con.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?)", (uid(),w,n,c,email,'',''))
        if True:
            for name,mail,title,value,stage in [('Evergreen Co','hello@evergreen.example','Evergreen Holiday Party',16000,'Qualified'),('Meridian Group','contact@meridian.example','Executive Retreat',28500,'Proposal'),('Lakehouse Foundation','info@lakehouse.example','Charity Gala',21000,'New')]:
                c=uid(); con.execute("INSERT INTO contacts VALUES (?,?,?,?,?,?,?)", (c,b,name,mail,'','client',''))
                con.execute("INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?)", (uid(),b,c,title,stage,value,'2027-02-15',None,NOW()))


production_launch.production_preflight()
initialize()
migrate_saas(db)
migrate_phase7_auth(db)
migrate_phase7_billing(db)
migrate_team_codes(db)
seed_demo()


class Credentials(BaseModel):
    email: str
    password: str


class Signup(Credentials):
    full_name: str
    workspace_name: str
    workspace_type: str = 'personal'
    access_code: str = ''


class Data(BaseModel):
    model_config = {"extra":"forbid"}


class WorkspaceIn(Data):
    access_code: str = ''
    name: str = Field(min_length=2, max_length=100)
    type: str = 'personal'
    currency: str = 'CAD'


class EventIn(Data):
    name: str = Field(min_length=1, max_length=160)
    type: str = 'Other'
    start_date: str
    end_date: str | None = None
    location: str = ''
    expected_guests: int = Field(default=0, ge=0)
    budget: float = Field(default=0, ge=0)
    status: str = 'Draft'
    parent_event_id: str | None = None
    client_contact_id: str | None = None


class GuestIn(Data):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(default='', max_length=255)
    phone: str = Field(default='', max_length=80)
    party_size: int = Field(default=1, ge=1, le=10000)
    group_name: str = Field(default='', max_length=160)
    category: str = Field(default='General', max_length=80)
    notes: str = Field(default='', max_length=2000)
    rsvp: str = 'Pending'


class TaskIn(Data):
    title: str = Field(min_length=1)
    due_date: str = ''
    priority: str = 'Medium'
    status: str = 'Open'
    assigned_to: str = ''


class BudgetIn(Data):
    name: str = Field(min_length=1)
    category: str = 'Other'
    estimated: float = Field(default=0, ge=0)
    committed: float = Field(default=0, ge=0)
    actual: float = Field(default=0, ge=0)
    paid: float = Field(default=0, ge=0)


class VendorIn(Data):
    name: str = Field(min_length=1)
    category: str = 'Other'
    email: str = ''
    phone: str = ''
    notes: str = ''


class AssignmentIn(Data):
    vendor_id: str
    scope: str = ''
    status: str = 'Shortlisted'
    committed: float = Field(default=0, ge=0)


class ContactIn(Data):
    name: str = Field(min_length=1)
    email: str = ''
    phone: str = ''
    kind: str = 'client'
    notes: str = ''


class LeadIn(Data):
    title: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    client_email: str = ''
    value: float = Field(default=0, ge=0)
    expected_date: str = ''
    stage: str = 'New'


class ScheduleIn(Data):
    title: str = Field(min_length=1)
    start_at: str
    end_at: str = ''
    location: str = ''


class PatchIn(BaseModel):
    model_config = {"extra":"allow"}


def get_user(request: Request, con):
    token = request.cookies.get('eventos_session')
    if not token:
        raise HTTPException(401,'Please sign in')
    row = con.execute("SELECT users.id, users.email, users.full_name, users.platform_role, users.is_active,users.email_verified FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(),NOW())).fetchone()
    if not row or not row['is_active']:
        raise HTTPException(401,'Session expired or account disabled. Please sign in again.')
    if not row['email_verified']:
        raise HTTPException(403,'Verify your email before signing in.')
    return record(row)


def access(request, con, wid, write=False):
    user=get_user(request,con)
    row=con.execute("SELECT m.role,w.name,w.type,w.currency FROM memberships m JOIN workspaces w ON m.workspace_id=w.id WHERE m.workspace_id=? AND m.user_id=?", (wid,user['id'])).fetchone()
    if not row:
        raise HTTPException(404,'Workspace not found')
    saas_limits(con,wid)
    if write and row['role'] not in ('owner','editor'):
        raise HTTPException(403,'Read-only workspace role')
    return record(row)


def entity(con,table,wid,item_id):
    if table not in ('events','guests','tasks','budget_items','vendors','event_vendors','contacts','leads','schedule_items'):
        raise AssertionError('Invalid table')
    row=con.execute(f"SELECT * FROM {table} WHERE id=? AND workspace_id=?", (item_id,wid)).fetchone()
    if not row:
        raise HTTPException(404,'Record not found')
    return record(row)


def check_event(con,wid,event_id):
    return entity(con,'events',wid,event_id)


def validate_parent_event(con, wid, event_id, parent_id):
    """Prevent an event from becoming its own ancestor (and enforce workspace boundaries)."""
    seen = {event_id}
    current = parent_id
    while current:
        if current in seen:
            raise HTTPException(422, 'Circular event hierarchy is not allowed')
        seen.add(current)
        current = check_event(con, wid, current)['parent_event_id']


def fields_update(con, table, wid, item_id, patch, allowed):
    data={k:v for k,v in patch.items() if k in allowed}
    if not data:
        raise HTTPException(422,'No editable fields supplied')
    entity(con,table,wid,item_id)
    sql=", ".join(f"{k}=?" for k in data)
    con.execute(f"UPDATE {table} SET {sql} WHERE id=? AND workspace_id=?", [*data.values(),item_id,wid])
    return entity(con,table,wid,item_id)


def require_choice(value, options, label):
    if value not in options:
        raise HTTPException(422,f"Invalid {label}. Allowed values: {', '.join(options)}")


def valid_date(date):
    try: datetime.fromisoformat(date.replace('Z','+00:00'))
    except (ValueError,AttributeError): raise HTTPException(422,'Enter a valid ISO date')


def valid_budget(data):
    if any(data.get(k,0)<0 for k in ('estimated','committed','actual','paid')):
        raise HTTPException(422,'Budget amounts cannot be negative')
    if data.get('paid',0)>max(data.get('committed',0),data.get('actual',0)):
        raise HTTPException(422,'Paid amount cannot exceed both committed and actual amounts')


def set_session(response, con, user_id):
    token=secrets.token_urlsafe(40)
    expire=(datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    con.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(),user_id,expire))
    response.set_cookie('eventos_session',token,httponly=True,samesite='lax',secure=COOKIE_SECURE,max_age=SESSION_DAYS*86400,path='/')


@app.post('/api/auth/login')
def login(payload:Credentials,response:Response,request:Request):
    with db() as con:
        row=con.execute("SELECT * FROM users WHERE email=?",(payload.email.lower().strip(),)).fetchone()
        ip=request.client.host if request.client else 'unknown'
        valid=bool(row and row['is_active'] and valid_password(payload.password,row['password_hash']))
        blocked=False
        if not valid:
            blocked=not issuance_limit(con,'login|'+ip,payload.email.lower().strip(),15)
        elif row['email_verified']:
            set_session(response,con,row['id'])
    if not valid:
        if blocked:raise HTTPException(429,'Too many sign-in attempts. Try again later.')
        raise HTTPException(401,'Incorrect email or password')
    if not row['email_verified']:
        raise HTTPException(403,'Please verify your email. Use the verification link sent during signup.')
    return {'ok':True}


@app.post('/api/auth/signup')
def signup(payload:Signup,response:Response):
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",payload.email.strip()): raise HTTPException(422,'Enter a valid email')
    if len(payload.password)<10: raise HTTPException(422,'Password must have at least 10 characters')
    if payload.workspace_type not in ('personal','business'): raise HTTPException(422,'Invalid workspace type')
    if not payload.full_name.strip(): raise HTTPException(422,'Enter a name')
    if not payload.workspace_name.strip() and not payload.access_code:raise HTTPException(422,'Enter a workspace name or team access code')
    requires_verification=verify_required()
    if requires_verification and (not phase7_mail_enabled() or not os.environ.get('EVENTOS_PUBLIC_BASE_URL','').startswith('https://')):
        raise HTTPException(503,'Verified-email registration is not configured')
    with db() as con:
        team_join=has_team_code(con,payload.access_code)
        if (saas_requires_code(con) or payload.access_code) and not team_join:
            saas_eligible_code(con,payload.access_code,payload.workspace_type)
        if not team_join and not payload.workspace_name.strip():
            raise HTTPException(422,'Enter a workspace name')
        u=uid()
        try:
            con.execute("INSERT INTO users(id,email,full_name,password_hash,created_at) VALUES (?,?,?,?,?)",(u,payload.email.lower().strip(),payload.full_name.strip(),password_hash(payload.password),NOW()))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'An account with that email already exists')
        if team_join:
            join_team(con,payload.access_code,u)
        else:
            w=create_workspace(con,u,payload.workspace_name.strip(),payload.workspace_type)
            defaults=SAAS_DEFAULTS[payload.workspace_type]
            con.execute('UPDATE workspaces SET max_events=?,max_guests=?,max_members=? WHERE id=?',(*defaults,w))
            if payload.access_code:saas_redeem(con,payload.access_code,payload.workspace_type,u,w)
        if requires_verification:
            con.execute('UPDATE users SET email_verified=0 WHERE id=?',(u,))
            verification=token_for(con,u,'verify')
        else:
            set_session(response,con,u)
        queue_email(con,u,'security','Welcome to EventOS','Your EventOS account was created successfully. Sign in to start planning.','/app',dedupe='welcome:'+u)
    if requires_verification:
        try:
            phase7_auth.deliver_verification(payload.email.strip().lower(),verification)
        except Exception:
            raise HTTPException(503,'Account created, but verification email could not be sent. Use Resend verification.')
        return {'ok':True,'verification_required':True}
    return {'ok':True,'verification_required':False}


@app.post('/api/auth/logout')
def logout(request:Request,response:Response):
    token=request.cookies.get('eventos_session')
    if token:
        with db() as con:
            con.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('eventos_session',path='/')
    return {'ok':True}


@app.get('/api/me')
def me(request:Request):
    with db() as con:
        u=get_user(request,con)
        ws=records(con.execute("SELECT w.*,m.role FROM workspaces w JOIN memberships m ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.type,w.name",(u['id'],)))
    return {'user':u,'workspaces':ws}


@app.get('/api/workspaces')
def workspaces(request:Request):
    return me(request)['workspaces']


@app.post('/api/workspaces')
def workspace_new(request:Request,data:WorkspaceIn):
    require_choice(data.type,('personal','business'),'workspace type')
    if not re.fullmatch('[A-Z]{3}',data.currency): raise HTTPException(422,'Currency must be a 3-letter ISO code')
    with db() as con:
        u=get_user(request,con)
        if saas_requires_code(con) or data.access_code:
            saas_eligible_code(con,data.access_code,data.type)
        wid=create_workspace(con,u['id'],data.name,data.type,data.currency)
        defaults=SAAS_DEFAULTS[data.type]
        con.execute('UPDATE workspaces SET max_events=?,max_guests=?,max_members=? WHERE id=?',(*defaults,wid))
        if data.access_code:saas_redeem(con,data.access_code,data.type,u['id'],wid)
        return record(con.execute("SELECT * FROM workspaces WHERE id=?",(wid,)).fetchone())


@app.get('/api/w/{wid}/events')
def events(request:Request,wid:str):
    with db() as con:
        access(request,con,wid)
        return records(con.execute("SELECT e.*, (SELECT COUNT(*) FROM guests g WHERE g.event_id=e.id) invitees, (SELECT COALESCE(SUM(COALESCE((SELECT json_extract(rr.party_json,'$.total') FROM rsvp_responses rr WHERE rr.guest_id=g.id),g.party_size)),0) FROM guests g WHERE g.event_id=e.id AND g.rsvp='Confirmed') confirmed_seats FROM events e WHERE workspace_id=? ORDER BY start_date",(wid,)))


@app.post('/api/w/{wid}/events')
def event_new(request:Request,wid:str,data:EventIn):
    valid_date(data.start_date)
    if data.end_date: valid_date(data.end_date)
    if data.end_date and data.end_date<data.start_date: raise HTTPException(422,'End must be after start')
    require_choice(data.status,('Draft','Planning','Confirmed','Completed','Cancelled'),'event status')
    with db() as con:
        access(request,con,wid,True)
        if data.parent_event_id: validate_parent_event(con,wid,'new-event',data.parent_event_id)
        if data.client_contact_id: entity(con,'contacts',wid,data.client_contact_id)
        saas_enforce(con,wid,'events')
        e=uid(); con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(e,wid,data.parent_event_id,data.client_contact_id,data.name,data.type,data.start_date,data.end_date,data.location,data.expected_guests,data.budget,data.status,NOW()))
        email_workspace_members(con,wid,'events','New event created','An event has been added to your EventOS workspace.','/app',dedupe_prefix='event-created:'+e)
        return entity(con,'events',wid,e)


@app.get('/api/w/{wid}/events/{eid}')
def event_one(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        return check_event(con,wid,eid)


@app.patch('/api/w/{wid}/events/{eid}')
def event_edit(request:Request,wid:str,eid:str,patch:dict[str,Any]):
    with db() as con:
        access(request,con,wid,True)
        old=check_event(con,wid,eid)
        data={**old,**{k:v for k,v in patch.items() if k in {'name','type','start_date','end_date','location','expected_guests','budget','status','client_contact_id','parent_event_id'}}}
        EventIn(**{k:data[k] for k in EventIn.model_fields})
        valid_date(data['start_date'])
        if data['end_date'] and data['end_date']<data['start_date']: raise HTTPException(422,'End must be after start')
        require_choice(data['status'],('Draft','Planning','Confirmed','Completed','Cancelled'),'event status')
        if data['parent_event_id']:
            validate_parent_event(con,wid,eid,data['parent_event_id'])
        if data['client_contact_id']:entity(con,'contacts',wid,data['client_contact_id'])
        result=fields_update(con,'events',wid,eid,patch,{'name','type','start_date','end_date','location','expected_guests','budget','status','client_contact_id','parent_event_id'})
        if 'status' in patch and old['status']!=patch['status']:
            email_workspace_members(con,wid,'events','Event status changed','An event in your EventOS workspace has changed to '+data['status']+'.','/app',dedupe_prefix='event-status:'+eid+':'+data['status']+':'+NOW())
        return result


@app.get('/api/w/{wid}/events/{eid}/summary')
def event_summary(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        e=check_event(con,wid,eid)
        g=record(con.execute("SELECT COUNT(*) invitees,COALESCE(SUM(CASE WHEN rsvp='Confirmed' THEN COALESCE((SELECT json_extract(rr.party_json,'$.total') FROM rsvp_responses rr WHERE rr.guest_id=guests.id),party_size) ELSE 0 END),0) confirmed_seats, SUM(CASE WHEN rsvp='Pending' THEN 1 ELSE 0 END) pending, SUM(CASE WHEN rsvp='Declined' THEN 1 ELSE 0 END) declined FROM guests WHERE event_id=?",(eid,)).fetchone())
        t=record(con.execute("SELECT COUNT(*) total, SUM(CASE WHEN status='Done' THEN 1 ELSE 0 END) done, SUM(CASE WHEN status!='Done' THEN 1 ELSE 0 END) open FROM tasks WHERE event_id=?",(eid,)).fetchone())
        money=record(con.execute("SELECT COALESCE(SUM(estimated),0) estimated,COALESCE(SUM(committed),0) committed,COALESCE(SUM(actual),0) actual,COALESCE(SUM(paid),0) paid FROM budget_items WHERE event_id=?",(eid,)).fetchone())
        return {'event':e,'guests':g,'tasks':t,'budget':money,'vendor_count':con.execute('SELECT COUNT(*) FROM event_vendors WHERE event_id=?',(eid,)).fetchone()[0]}


@app.get('/api/w/{wid}/events/{eid}/command-center')
def event_command_center(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        event = check_event(con,wid,eid)
        return build_command_center(con,wid,eid,event)


@app.get('/api/w/{wid}/summary')
def workspace_summary(request:Request,wid:str):
    with db() as con:
        w=access(request,con,wid)
        e=con.execute("SELECT COUNT(*) FROM events WHERE workspace_id=? AND status NOT IN ('Cancelled','Completed')",(wid,)).fetchone()[0]
        tasks=con.execute("SELECT COUNT(*) FROM tasks WHERE workspace_id=? AND status!='Done'",(wid,)).fetchone()[0]
        leads=con.execute("SELECT COUNT(*),COALESCE(SUM(value),0) FROM leads WHERE workspace_id=? AND stage NOT IN ('Won','Lost')",(wid,)).fetchone()
        upcoming=records(con.execute("SELECT * FROM events WHERE workspace_id=? AND status NOT IN ('Cancelled','Completed') ORDER BY start_date LIMIT 5",(wid,)))
        return {'workspace':w,'active_events':e,'open_tasks':tasks,'open_leads':leads[0],'pipeline_value':leads[1],'upcoming':upcoming}


def list_child(request,wid,eid,table,extra=''):
    with db() as con:
        access(request,con,wid)
        check_event(con,wid,eid)
        return records(con.execute(f"SELECT * FROM {table} WHERE workspace_id=? AND event_id=? {extra}",(wid,eid)))


def add_child(request,wid,eid,table,data,columns,values):
    with db() as con:
        access(request,con,wid,True)
        check_event(con,wid,eid)
        if table=='guests':saas_enforce(con,wid,'guests')
        id=uid(); cols=['id','workspace_id','event_id']+columns
        con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",(id,wid,eid,*values))
        return entity(con,table,wid,id)


@app.get('/api/w/{wid}/events/{eid}/guests')
def guests(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        check_event(con,wid,eid)
        return records(con.execute("""SELECT g.*,
          r.submitted_at AS rsvp_submitted_at,
          CAST(json_extract(r.party_json,'$.total') AS INTEGER) AS reported_attending,
          CAST(json_extract(r.party_json,'$.adults') AS INTEGER) AS reported_adults,
          CAST(json_extract(r.party_json,'$.kids') AS INTEGER) AS reported_kids,
          COALESCE(json_extract(r.party_json,'$.plus_one'),0) AS reported_plus_one
          FROM guests g LEFT JOIN rsvp_responses r ON r.guest_id=g.id
          WHERE g.workspace_id=? AND g.event_id=? ORDER BY g.name COLLATE NOCASE""",(wid,eid)))


@app.post('/api/w/{wid}/events/{eid}/guests')
def guest_new(request:Request,wid:str,eid:str,data:GuestIn):
    require_choice(data.rsvp,('Pending','Confirmed','Declined','Maybe'),'RSVP')
    return add_child(request,wid,eid,'guests',data,['name','email','party_size','group_name','rsvp','invitation_token','created_at','phone','category','notes'],[data.name,data.email,data.party_size,data.group_name,data.rsvp,secrets.token_urlsafe(32),NOW(),data.phone,data.category,data.notes])


@app.patch('/api/w/{wid}/guests/{gid}')
def guest_edit(request:Request,wid:str,gid:str,patch:dict[str,Any]):
    with db() as con:
        access(request,con,wid,True)
        old=entity(con,'guests',wid,gid)
        d={**old,**patch}
        GuestIn(**{k:d[k] for k in GuestIn.model_fields})
        require_choice(d['rsvp'],('Pending','Confirmed','Declined','Maybe'),'RSVP')
        return fields_update(con,'guests',wid,gid,patch,{'name','email','phone','party_size','group_name','category','notes','rsvp'})


@app.delete('/api/w/{wid}/guests/{gid}')
def guest_delete(request:Request,wid:str,gid:str):
    with db() as con:
        access(request,con,wid,True)
        entity(con,'guests',wid,gid)
        con.execute('DELETE FROM guests WHERE id=? AND workspace_id=?',(gid,wid))
        return {'ok':True}


@app.post('/api/w/{wid}/events/{eid}/guests/import')
def guests_import(request:Request,wid:str,eid:str,data:list[GuestIn]):
    if len(data)>1000: raise HTTPException(422,'Maximum 1,000 records per batch')
    with db() as con:
        access(request,con,wid,True)
        check_event(con,wid,eid)
        if data:saas_enforce(con,wid,'guests',len(data))
        for g in data:
            require_choice(g.rsvp,('Pending','Confirmed','Declined','Maybe'),'RSVP')
            con.execute("INSERT INTO guests (id,workspace_id,event_id,name,email,party_size,group_name,rsvp,invitation_token,created_at,phone,category,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(uid(),wid,eid,g.name,g.email,g.party_size,g.group_name,g.rsvp,secrets.token_urlsafe(32),NOW(),g.phone,g.category,g.notes))
        return {'imported':len(data)}


@app.get('/api/w/{wid}/events/{eid}/guests/export.csv')
def guests_export(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        check_event(con,wid,eid)
        guest_rows=records(con.execute('SELECT name,email,phone,party_size,group_name,category,notes,rsvp FROM guests WHERE workspace_id=? AND event_id=? ORDER BY name',(wid,eid)))
    out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=['name','email','phone','party_size','group_name','category','notes','rsvp'])
    writer.writeheader()
    # CSV may be opened in Excel; prevent guest-supplied text from becoming formulas.
    def safe_csv_cell(value):
        return ("'" + value) if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
    writer.writerows([{key:safe_csv_cell(value) for key,value in row.items()} for row in guest_rows])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="eventos-guests.csv"'})


@app.get('/api/rsvp/{token}')
def rsvp_view(token:str):
    with db() as con:
        row=con.execute("SELECT g.id,g.workspace_id,g.event_id,g.name,g.party_size,g.rsvp,e.name event_name,e.start_date,e.location,e.status FROM guests g JOIN events e ON e.id=g.event_id WHERE g.invitation_token=?",(token,)).fetchone()
        if not row:raise HTTPException(404,'Invitation not found')
        saas_limits(con,row['workspace_id'])
        gallery=con.execute('SELECT access_token FROM event_galleries WHERE event_id=? AND enabled=1',(row['event_id'],)).fetchone()
        registry=con.execute('SELECT access_token FROM gift_registries WHERE event_id=? AND enabled=1',(row['event_id'],)).fetchone()
        website=con.execute('SELECT slug,access_token FROM event_websites WHERE event_id=? AND published=1',(row['event_id'],)).fetchone()
        return {**record(row), **public_form(con, row['event_id'], row['event_name'], row['id']),
                'design': get_design(con, row['event_id']), 'media':event_media(con,row['event_id']),
                'event_extras':{'gallery':'/photos/'+gallery['access_token'] if gallery and saas_feature_enabled(con,row['workspace_id'],'gallery') else '',
                                'registry':'/gifts/'+registry['access_token'] if registry and saas_feature_enabled(con,row['workspace_id'],'registry') else '',
                                'website':'/event/'+website['slug']+'/'+website['access_token'] if website and saas_feature_enabled(con,row['workspace_id'],'websites') else '',
                                'guest_hub':'/guest/'+token if saas_feature_enabled(con,row['workspace_id'],'operations') else ''}}


@app.post('/api/rsvp/{token}')
def rsvp_submit(token:str,payload:dict[str,Any]):
    status=payload.get('rsvp','')
    require_choice(status,('Confirmed','Declined','Maybe'),'RSVP')
    with db() as con:
        row=con.execute('SELECT g.id,g.workspace_id,g.event_id,g.party_size,e.name event_name,e.status FROM guests g JOIN events e ON e.id=g.event_id WHERE g.invitation_token=?',(token,)).fetchone()
        if not row:raise HTTPException(404,'Invitation not found')
        saas_limits(con,row['workspace_id'])
        if row['status'] in ('Cancelled','Completed'):raise HTTPException(409,'This event is not accepting responses')
        answers = save_guest_response(con, row['id'], row['event_id'], row['event_name'],
                                      payload.get('answers', {}), payload.get('party'), status, row['party_size'])
        con.execute('UPDATE guests SET rsvp=? WHERE id=?',(status,row['id']))
        email_workspace_members(con,row['workspace_id'],'rsvp','New RSVP response','A guest responded '+status+' to your event. Sign in to view the guest details.','/app')
    return {'ok':True,'rsvp':status,'answers':answers}


@app.get('/api/w/{wid}/events/{eid}/tasks')
def tasks(request:Request,wid:str,eid:str):
    return list_child(request,wid,eid,'tasks','ORDER BY CASE WHEN status=\'Done\' THEN 1 ELSE 0 END, due_date')


@app.post('/api/w/{wid}/events/{eid}/tasks')
def task_new(request:Request,wid:str,eid:str,data:TaskIn):
    require_choice(data.priority,('Low','Medium','High'),'priority')
    require_choice(data.status,('Open','In Progress','Done'),'task status')
    return add_child(request,wid,eid,'tasks',data,['title','due_date','priority','status','assigned_to'],[data.title,data.due_date,data.priority,data.status,data.assigned_to])


@app.patch('/api/w/{wid}/tasks/{id}')
def task_edit(request:Request,wid:str,id:str,patch:dict[str,Any]):
    with db() as con:
        access(request,con,wid,True)
        old=entity(con,'tasks',wid,id); d={**old,**patch}
        TaskIn(**{k:d[k] for k in TaskIn.model_fields})
        require_choice(d['priority'],('Low','Medium','High'),'priority')
        require_choice(d['status'],('Open','In Progress','Done'),'task status')
        return fields_update(con,'tasks',wid,id,patch,{'title','due_date','priority','status','assigned_to'})


@app.get('/api/w/{wid}/events/{eid}/budget')
def budgets(request:Request,wid:str,eid:str):
    return list_child(request,wid,eid,'budget_items','ORDER BY category,name')


@app.post('/api/w/{wid}/events/{eid}/budget')
def budget_new(request:Request,wid:str,eid:str,data:BudgetIn):
    valid_budget(data.model_dump())
    return add_child(request,wid,eid,'budget_items',data,['name','category','estimated','committed','actual','paid'],[data.name,data.category,data.estimated,data.committed,data.actual,data.paid])


@app.patch('/api/w/{wid}/budget/{id}')
def budget_edit(request:Request,wid:str,id:str,patch:dict[str,Any]):
    with db() as con:
        access(request,con,wid,True)
        old=entity(con,'budget_items',wid,id); d={**old,**patch}
        BudgetIn(**{k:d[k] for k in BudgetIn.model_fields})
        valid_budget(d)
        return fields_update(con,'budget_items',wid,id,patch,{'name','category','estimated','committed','actual','paid'})


@app.get('/api/w/{wid}/vendors')
def vendors(request:Request,wid:str):
    with db() as con:
        access(request,con,wid)
        return records(con.execute('SELECT * FROM vendors WHERE workspace_id=? ORDER BY name',(wid,)))


@app.post('/api/w/{wid}/vendors')
def vendor_new(request:Request,wid:str,data:VendorIn):
    with db() as con:
        access(request,con,wid,True)
        i=uid();con.execute('INSERT INTO vendors VALUES (?,?,?,?,?,?,?)',(i,wid,data.name,data.category,data.email,data.phone,data.notes))
        return entity(con,'vendors',wid,i)


@app.get('/api/w/{wid}/events/{eid}/vendors')
def assigned_vendors(request:Request,wid:str,eid:str):
    with db() as con:
        access(request,con,wid)
        check_event(con,wid,eid)
        return records(con.execute("SELECT ev.*,v.name,v.category,v.email FROM event_vendors ev JOIN vendors v ON v.id=ev.vendor_id WHERE ev.workspace_id=? AND ev.event_id=? ORDER BY v.name",(wid,eid)))


@app.post('/api/w/{wid}/events/{eid}/vendors')
def vendor_assign(request:Request,wid:str,eid:str,data:AssignmentIn):
    require_choice(data.status,('Shortlisted','Quote requested','Booked','Cancelled'),'vendor status')
    with db() as con:
        access(request,con,wid,True)
        check_event(con,wid,eid)
        entity(con,'vendors',wid,data.vendor_id)
        i=uid()
        try:
            con.execute('INSERT INTO event_vendors VALUES (?,?,?,?,?,?,?)',(i,wid,eid,data.vendor_id,data.scope,data.status,data.committed))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Vendor is already assigned to this event')
        return entity(con,'event_vendors',wid,i)


@app.get('/api/w/{wid}/contacts')
def contacts(request:Request,wid:str):
    with db() as con:
        access(request,con,wid)
        return records(con.execute('SELECT * FROM contacts WHERE workspace_id=? ORDER BY name',(wid,)))


@app.post('/api/w/{wid}/contacts')
def contact_new(request:Request,wid:str,data:ContactIn):
    with db() as con:
        access(request,con,wid,True)
        i=uid();con.execute('INSERT INTO contacts VALUES (?,?,?,?,?,?,?)',(i,wid,data.name,data.email,data.phone,data.kind,data.notes))
        return entity(con,'contacts',wid,i)


@app.get('/api/w/{wid}/leads')
def leads(request:Request,wid:str):
    with db() as con:
        access(request,con,wid)
        if con.execute('SELECT type FROM workspaces WHERE id=?',(wid,)).fetchone()[0]!='business':raise HTTPException(403,'Pipeline is available in business workspaces')
        return records(con.execute("SELECT l.*,c.name client_name,c.email client_email FROM leads l JOIN contacts c ON c.id=l.contact_id WHERE l.workspace_id=? ORDER BY l.created_at DESC",(wid,)))


@app.post('/api/w/{wid}/leads')
def lead_new(request:Request,wid:str,data:LeadIn):
    require_choice(data.stage,('New','Qualified','Proposal','Negotiation','Won','Lost'),'lead stage')
    with db() as con:
        w=access(request,con,wid,True)
        if w['type']!='business':raise HTTPException(403,'Pipeline is for business workspaces')
        c=uid();l=uid()
        con.execute('INSERT INTO contacts VALUES (?,?,?,?,?,?,?)',(c,wid,data.client_name,data.client_email,'','client',''))
        con.execute('INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?)',(l,wid,c,data.title,data.stage,data.value,data.expected_date,None,NOW()))
        return entity(con,'leads',wid,l)


@app.patch('/api/w/{wid}/leads/{id}')
def lead_edit(request:Request,wid:str,id:str,patch:dict[str,Any]):
    with db() as con:
        w=access(request,con,wid,True)
        if w['type']!='business':raise HTTPException(403,'Pipeline is for business workspaces')
        old=entity(con,'leads',wid,id)
        if 'stage' in patch:require_choice(patch['stage'],('New','Qualified','Proposal','Negotiation','Won','Lost'),'lead stage')
        if 'value' in patch and (not isinstance(patch['value'],(int,float)) or patch['value']<0):raise HTTPException(422,'Invalid lead value')
        return fields_update(con,'leads',wid,id,patch,{'title','stage','value','expected_date'})


@app.post('/api/w/{wid}/leads/{id}/convert')
def lead_convert(request:Request,wid:str,id:str):
    with db() as con:
        w=access(request,con,wid,True)
        if w['type']!='business':raise HTTPException(403,'Pipeline is for business workspaces')
        l=entity(con,'leads',wid,id)
        if l['event_id']:return check_event(con,wid,l['event_id'])
        start=l['expected_date'] if l['expected_date'] else datetime.now(timezone.utc).date().isoformat()
        valid_date(start)
        saas_enforce(con,wid,'events')
        e=uid()
        con.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(e,wid,None,l['contact_id'],l['title'],'Client event',start,None,'',0,l['value'],'Planning',NOW()))
        con.execute("UPDATE leads SET stage='Won',event_id=? WHERE id=?",(e,id))
        return entity(con,'events',wid,e)


@app.get('/api/w/{wid}/events/{eid}/schedule')
def schedule(request:Request,wid:str,eid:str):
    return list_child(request,wid,eid,'schedule_items','ORDER BY start_at')


@app.post('/api/w/{wid}/events/{eid}/schedule')
def schedule_new(request:Request,wid:str,eid:str,data:ScheduleIn):
    valid_date(data.start_at)
    if data.end_at:
        valid_date(data.end_at)
        if data.end_at<data.start_at:raise HTTPException(422,'End must be after start')
    return add_child(request,wid,eid,'schedule_items',data,['title','start_at','end_at','location'],[data.title,data.start_at,data.end_at,data.location])


install_rsvp_forms(app, db, access, check_event)
install_guest_crm(app, db, access, check_event, GuestIn, uid, NOW)
install_operations(app, db, access, check_event, get_user, uid)
install_checkin(app, db, access, check_event, get_user, NOW, uid)
install_guest_experience(app,db,access,check_event)
install_platform(app,db,get_user,access)
install_invites(app,db,get_user,access)
install_gallery(app,db,access,check_event)
install_registry(app,db,access,check_event)
install_event_websites(app,db,access,check_event)
install_agency(app,db,access,check_event,get_user)
install_agency_advanced(app,db,access,check_event,get_user)
install_auth(app,db,password_hash)
install_billing(app,db,access,get_user)
install_invitation_assets(app, db, access, check_event)
app.state.get_user = get_user
install_invitation_designer(app, db, access, check_event)
install_branding(app, db, access, check_event, get_user, uid, NOW)
production_launch.install(app,db,access,get_user)
install_agency_connect(app,db,access,get_user)
install_subscription_reconciliation(app,db)
from platform_media import install as install_platform_media
install_platform_media(app,db,get_user,production_mariadb.enabled())
install_platform_studio(app,db,get_user)
install_platform_analytics(app,db,get_user)
install_account_support(app,db,get_user,password_hash,valid_password,set_session,COOKIE_SECURE,production_mariadb.enabled())
install_invoice_payments(app,db)
install_party_hub(app, db, get_user, access, check_event, production_mariadb.enabled())
install_monetization(app, db, get_user, access, production_mariadb.enabled())
install_diy_planner(app, db, access, check_event, production_mariadb.enabled())
install_growth_center(app, db, get_user, production_mariadb.enabled())
install_email_notifications(app, db, get_user, production_mariadb.enabled())
from content_controls import install as install_content_controls
install_content_controls(app, db, get_user, access, check_event)


@app.get('/api/config')
def config():
    with db() as con:
        enabled=saas_requires_code(con)
    return {'demo_enabled': os.environ.get('EVENTOS_DEMO_SEED', '0') == '1', 'public_demo': PUBLIC_DEMO, 'signup_access_code_required':enabled, 'email_verification_required':verify_required(), 'password_recovery_enabled':phase7_mail_enabled() and bool(os.environ.get('EVENTOS_PUBLIC_BASE_URL','').startswith('https://'))}


@app.get('/api/health')
def health():
    return {'status':'ok','version':'0.2.0','database_engine':'postgres' if production_database.postgres_enabled() else 'mariadb' if production_mariadb.enabled() else 'sqlite'}


@app.get('/rsvp/{token}')
def rsvp_page(token:str):
    return FileResponse(ROOT/'static'/'rsvp.html', headers={'Cache-Control':'no-store, max-age=0'})


# Explicitly disable browser and proxy caching of the SPA code while the
# invitation editor is under active development. Safari can otherwise keep an
# older JavaScript file after a successful Render deployment.
class FreshStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Avoid conditional 304 responses from previously cached script URLs.
        response = await super().get_response(path, scope)
        if path.lower().endswith(('.js', '.css', '.html')):
            response.headers['Cache-Control'] = 'no-store, no-cache, max-age=0, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            if 'etag' in response.headers:
                del response.headers['etag']
            if 'last-modified' in response.headers:
                del response.headers['last-modified']
        return response

    def is_not_modified(self, response_headers, request_headers):
        return False

app.mount('/static',FreshStaticFiles(directory=str(ROOT/'static')),name='static')


@app.get('/')
def marketing_landing(request:Request):
    from public_seo import enhance_marketing
    branded=domain_root(db,request,ROOT/'static'/'landing.html')
    if type(branded) is FileResponse:
        with db() as con:
            settings=site_settings(con)
            landing=render_landing(con,ROOT/'static'/'landing.html',os.environ.get('EVENTOS_PUBLIC_BASE_URL',''))
        raw=landing.body.decode('utf-8')
        return enhance_marketing(raw,request,settings,'/',settings['seo_title'],settings['seo_description'],ldtype='WebPage')
    return branded


@app.get('/news')
def news_page(request:Request):
    from public_seo import enhance_marketing, esc
    raw=(ROOT/'static'/'growth_news.html').read_text(encoding='utf-8')
    with db() as con:
        site=site_settings(con)
        rows=con.execute("SELECT title,slug,summary,category FROM growth_articles WHERE status='published' ORDER BY published_at DESC LIMIT 30").fetchall()
    # Server-render links so Google can find published stories without running JS.
    cards=''.join('<article><h2><a href="/news/'+esc(row['slug'])+'">'+esc(row['title'])+'</a></h2><p>'+esc(row['summary'])+'</p><small>'+esc(row['category'])+'</small></article>' for row in rows)
    raw=raw.replace('<section id="list" class="newsgrid" aria-live="polite">Loading...</section>',
                    '<section id="list" class="newsgrid" aria-live="polite">'+(cards or '<p>Planning guides are coming soon.</p>')+'</section>')
    return enhance_marketing(raw,request,site,'/news','Alberta Party Planning Tips, Wedding & Birthday Guides | Honestly Zen',
            'Alberta party planning tips, wedding checklists, birthday themes, vendor advice and budget ideas from Honestly Zen Event Planning.')


@app.get('/news/{slug}')
def public_news_article(request:Request,slug:str):
    from public_seo import blog_post
    with db() as con:
        return blog_post(con,request,site_settings(con),slug)


@app.get('/discover/vendors')
def vendor_discovery_page(request:Request):
    from public_seo import enhance_marketing, esc
    raw=(ROOT/'static'/'party_discover.html').read_text(encoding='utf-8')
    with db() as con:
        site=site_settings(con)
        rows=con.execute("SELECT id,business_name,category,city,other_community,description FROM market_listings WHERE publication_status='published' ORDER BY created_at DESC LIMIT 30").fetchall()
    # Only approved listings, with real crawlable links to public detail pages.
    cards=''.join('<article class="card"><div class="body"><span class="tag">'+esc(row['category'])+'</span><h3><a href="/vendors/'+esc(row['id'])+'">'+esc(row['business_name'])+'</a></h3><p>'+esc(row['description'][:160])+'</p><p>'+esc(row['other_community'] or row['city'])+', Alberta</p></div></article>' for row in rows)
    raw=raw.replace('<div id="list" class="cards" aria-live="polite"><p>Loading listings...</p></div>',
                    '<div id="list" class="cards" aria-live="polite">'+(cards or '<p>New vendor listings are being reviewed.</p>')+'</div>')
    return enhance_marketing(raw,request,site,'/discover/vendors',
        'Alberta Party Vendors & Wedding Services | Honestly Zen Event Planning',
        'Browse Alberta venues, photographers, catering, DJs, florists and rentals. Compare service options, save vendors and request custom quotes for your celebration.')


@app.get('/vendors/{ident}')
def public_vendor_page(request:Request,ident:str):
    from public_seo import vendor_page
    with db() as con:
        return vendor_page(con,request,site_settings(con),ident)


@app.get('/for-vendors')
def public_vendor_benefits(request:Request):
    from public_seo import enhance_marketing
    raw=(ROOT/'static'/'vendor_benefits.html').read_text(encoding='utf-8')
    with db() as con:settings=site_settings(con)
    return enhance_marketing(raw,request,settings,'/for-vendors',
         'List Your Alberta Party Business & Manage Quotes | Honestly Zen',
         'Reach Alberta party hosts. Create a vendor listing, manage inquiries, request verification, negotiate quotes and organize bookings using Honestly Zen Event Planning.')

@app.get('/app')
@app.get('/app/')
def index():
    return FileResponse(ROOT/'static'/'index.html', headers={'Cache-Control':'no-store, max-age=0', 'X-Robots-Tag':'noindex, nofollow'})


@app.get('/{fallback:path}')
def spa(fallback:str):
    raise HTTPException(404,'Page not found')

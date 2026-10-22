"""EventOS SaaS tenant controls and one-time access-code licensing.

The platform administrator is distinct from workspace owner/editor/viewer roles.
No default administrator is created. First admin must authenticate and provide a
private bootstrap token configured by the server operator. Codes are stored hashed.
"""
import hashlib
import os
import re
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request

LIMITS = {'personal': (3,120,2), 'business': (30,5000,15)}
CODE_PATTERN = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(code):
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def migrate(db):
    with db() as con:
        for table,column,ddl in [
            ('users','platform_role',"TEXT NOT NULL DEFAULT 'user'"),
            ('users','is_active',"INTEGER NOT NULL DEFAULT 1"),
            ('workspaces','is_active',"INTEGER NOT NULL DEFAULT 1"),
            ('workspaces','max_events',"INTEGER NOT NULL DEFAULT 30"),
            ('workspaces','max_guests',"INTEGER NOT NULL DEFAULT 5000"),
            ('workspaces','max_members',"INTEGER NOT NULL DEFAULT 15"),
            ('workspaces','expires_at',"TEXT NOT NULL DEFAULT ''"),
        ]:
            if column not in {x['name'] for x in con.execute('PRAGMA table_info('+table+')')}:
                con.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+ddl)
        # Existing personal workspaces retain their existing records, while
        # applying limits only to new workspaces and workspaces explicitly edited
        # by the platform admin avoids silently locking current users.
        con.executescript("""
            CREATE TABLE IF NOT EXISTS saas_codes (
              id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL, workspace_type TEXT NOT NULL,
              max_events INTEGER NOT NULL, max_guests INTEGER NOT NULL, max_members INTEGER NOT NULL,
              max_uses INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
              expires_at TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1,
              created_by TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS saas_redemptions (
              id TEXT PRIMARY KEY, code_id TEXT NOT NULL REFERENCES saas_codes(id),
              user_id TEXT NOT NULL REFERENCES users(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
              created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS saas_settings (
              `key` VARCHAR(191) PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO saas_settings(`key`,value) VALUES ('require_access_code','0');
            CREATE TABLE IF NOT EXISTS saas_feature_settings(
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                feature TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                changed_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,feature));
        """)


# All existing workspace functionality remains available by default.
FEATURES = {
    'gallery': 'Photo galleries',
    'registry': 'Gift registries',
    'websites': 'Event websites',
    'agency': 'Agency client portals',
    'operations': 'Seating, operations & check-in',
}

def feature_enabled(con,wid,feature):
    if feature not in FEATURES:
        raise ValueError('Unknown feature')
    row=con.execute('SELECT enabled FROM saas_feature_settings WHERE workspace_id=? AND feature=?',
                    (wid,feature)).fetchone()
    return bool(row['enabled']) if row else True

def feature_settings(con,wid):
    return {f:feature_enabled(con,wid,f) for f in FEATURES}

def route_feature(path):
    parts=path.strip('/').split('/')
    if len(parts)>=4 and parts[0]=='api' and parts[1]=='w':
        if parts[3]=='agency':return parts[2],'agency'
        if parts[3]=='operations':return parts[2],'operations'
        if len(parts)>=6 and parts[3]=='events':
            kind={'gallery':'gallery','registry':'registry','website':'websites',
                'agency':'agency','operations':'operations','seating':'operations',
                'messages':'operations','checkin':'operations','checkins':'operations','guest-experience':'operations',
                'tables':'operations','report.csv':'operations'}.get(parts[5])
            if parts[5]=='guests' and len(parts)>=8 and parts[7]=='checkin-qr.png':
                kind='operations'
            return (parts[2],kind) if kind else None
    return None

def public_route_feature(con,path):
    p=path.strip('/').split('/')
    if len(p)<2:return None
    if p[0]=='api' and len(p)>=3 and p[1] in ('gallery','registry','event-site','client'):
        feat={'gallery':('event_galleries','gallery'),'registry':('gift_registries','registry'),
              'event-site':('event_websites','websites'),'client':('agency_clients','agency')}.get(p[1])
        if not feat:return None
        table,feature=feat
        token=p[3] if p[1]=='event-site' and len(p)>=4 else p[2]
    elif p[0]=='api' and len(p)>=3 and p[1] in ('checkin','rsvp'):
        if p[1]=='checkin':
            row=con.execute('''SELECT g.workspace_id FROM event_checkin_codes x
                JOIN guests g ON g.id=x.guest_id WHERE x.code=?''',(p[2],)).fetchone()
        elif len(p)>=4 and p[3]=='checkin-qr.png':
            row=con.execute('SELECT workspace_id FROM guests WHERE invitation_token=?',(p[2],)).fetchone()
        else:
            return None
        return (row['workspace_id'],'operations') if row else None
    elif p[0]=='checkin' and len(p)>=2:
        row=con.execute('''SELECT g.workspace_id FROM event_checkin_codes x
            JOIN guests g ON g.id=x.guest_id WHERE x.code=?''',(p[1],)).fetchone()
        return (row['workspace_id'],'operations') if row else None
    elif p[0] in ('photos','gifts','event','client'):
        feat={'photos':('event_galleries','gallery'),'gifts':('gift_registries','registry'),
              'event':('event_websites','websites'),'client':('agency_clients','agency')}[p[0]]
        table,feature=feat
        token=p[2] if feature=='websites' and len(p)>=3 else p[1]
    else:return None
    if not isinstance(token,str) or len(token)>150:return None
    row=con.execute('SELECT workspace_id FROM '+table+' WHERE access_token=?',(token,)).fetchone()
    return (row['workspace_id'],feature) if row else None


def require_code(con):
    row=con.execute("SELECT value FROM saas_settings WHERE `key`='require_access_code'").fetchone()
    return row is not None and row[0]=='1'


def eligible_code(con, code, kind):
    if not isinstance(code,str) or not CODE_PATTERN.fullmatch(code):
        raise HTTPException(422,'Enter a valid access code')
    row=con.execute('SELECT * FROM saas_codes WHERE code_hash=?',(digest(code),)).fetchone()
    if not row or not row['is_active'] or row['uses']>=row['max_uses'] or (
        row['expires_at'] and row['expires_at']<now()):
        raise HTTPException(403,'Access code is invalid, expired, or fully redeemed')
    if row['workspace_type']!=kind:
        raise HTTPException(422,'Access code is for a different workspace type')
    return dict(row)


def redeem(con, code, kind, user_id, workspace_id):
    row=eligible_code(con,code,kind)
    changed=con.execute('UPDATE saas_codes SET uses=uses+1 WHERE id=? AND is_active=1 AND uses<max_uses',(row['id'],))
    if changed.rowcount!=1:
        raise HTTPException(409,'This access code has been used')
    # A code's redemption deadline does not expire the customer's workspace.
    # Subscription expiry is managed independently by the platform admin.
    con.execute('''UPDATE workspaces SET max_events=?,max_guests=?,max_members=?
                   WHERE id=?''',(row['max_events'],row['max_guests'],row['max_members'],workspace_id))
    con.execute('INSERT INTO saas_redemptions VALUES (?,?,?,?,?)',(
        secrets.token_hex(16),row['id'],user_id,workspace_id,now()))


def limits(con,wid):
    row=con.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
    if not row:raise HTTPException(404,'Workspace not found')
    if not row['is_active'] or (row['expires_at'] and row['expires_at']<now()):
        raise HTTPException(403,'Workspace subscription is inactive or expired')
    return dict(row)


def enforce(con,wid,resource,increment=1):
    row=limits(con,wid)
    mapping={'events':('max_events','events'),'guests':('max_guests','guests'),'members':('max_members','memberships')}
    if resource not in mapping:raise AssertionError('Invalid resource')
    field,table=mapping[resource]
    value=row[field]
    if resource=='members':
        existing=con.execute('SELECT COUNT(*) FROM memberships WHERE workspace_id=?',(wid,)).fetchone()[0]
    elif resource=='events':
        existing=con.execute('SELECT COUNT(*) FROM events WHERE workspace_id=?',(wid,)).fetchone()[0]
    else:
        existing=con.execute('SELECT COUNT(*) FROM guests WHERE workspace_id=?',(wid,)).fetchone()[0]
    if existing+increment>value:
        raise HTTPException(409,f'Workspace {resource} limit reached ({value}). Ask your platform administrator to increase it.')


def is_admin(con,user_id):
    row=con.execute('SELECT platform_role,is_active FROM users WHERE id=?',(user_id,)).fetchone()
    return bool(row and row['platform_role']=='admin' and row['is_active'])


def install_platform(app,db,get_user,access):
    def admin(request,con):
        user=get_user(request,con)
        if not is_admin(con,user['id']):
            raise HTTPException(403,'Platform administrator permission required')
        return user

    @app.post('/api/platform/bootstrap')
    def bootstrap(request:Request,payload:dict):
        key=os.getenv('EVENTOS_ADMIN_BOOTSTRAP_TOKEN','')
        supplied=payload.get('token') if isinstance(payload,dict) else None
        if not key or len(key)<24 or not isinstance(supplied,str) or not secrets.compare_digest(key,supplied):
            raise HTTPException(403,'Admin bootstrap not configured or token invalid')
        with db() as con:
            user=get_user(request,con)
            if con.execute("SELECT 1 FROM users WHERE platform_role='admin'").fetchone():
                raise HTTPException(409,'A platform administrator already exists')
            con.execute("UPDATE users SET platform_role='admin' WHERE id=?",(user['id'],))
        return {'ok':True}

    @app.get('/api/platform/overview')
    def admin_overview(request:Request):
        with db() as con:
            admin(request,con)
            setting=require_code(con)
            codes=[dict(x) for x in con.execute("""SELECT id,label,workspace_type,max_events,max_guests,max_members,
                uses,max_uses,expires_at,is_active,created_at FROM saas_codes ORDER BY created_at DESC LIMIT 300""")]
            spaces=[dict(x) for x in con.execute("""SELECT w.id,w.name,w.type,w.is_active,w.max_events,w.max_guests,w.max_members,w.expires_at,w.billing_plan,w.billing_status,
                 (SELECT COUNT(*) FROM events e WHERE e.workspace_id=w.id) event_count,
                 (SELECT COUNT(*) FROM guests g WHERE g.workspace_id=w.id) guest_count,
                 (SELECT COUNT(*) FROM memberships m WHERE m.workspace_id=w.id) member_count
                 FROM workspaces w ORDER BY w.created_at DESC LIMIT 500""")]
            users=[dict(x) for x in con.execute("""SELECT id,email,full_name,platform_role,is_active,created_at
                 FROM users ORDER BY created_at DESC LIMIT 500""")]
            for w in spaces:
                w['features']=feature_settings(con,w['id'])
            metrics={
                'accounts':con.execute('SELECT COUNT(*) FROM users').fetchone()[0],
                'workspaces':con.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0],
                'active_workspaces':con.execute('SELECT COUNT(*) FROM workspaces WHERE is_active=1').fetchone()[0],
                'paid_or_manually_granted':con.execute("""SELECT COUNT(*) FROM workspaces
                    WHERE billing_plan IN ('pro','business') AND billing_status IN ('active','trialing','manual')""").fetchone()[0],
                'events':con.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                'guests':con.execute('SELECT COUNT(*) FROM guests').fetchone()[0]}
            return {'require_access_code':setting,'codes':codes,'workspaces':spaces,'users':users,
                    'metrics':metrics,'available_features':FEATURES}

    @app.post('/api/platform/codes')
    def make_code(request:Request,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{
            'label','workspace_type','max_events','max_guests','max_members','max_uses','expires_at'}:
            raise HTTPException(422,'Invalid code settings')
        kind=payload.get('workspace_type')
        if kind not in LIMITS:raise HTTPException(422,'Choose personal or business')
        m=LIMITS[kind]
        values=[payload.get('max_events',m[0]),payload.get('max_guests',m[1]),payload.get('max_members',m[2]),payload.get('max_uses',1)]
        if any(type(v) is not int or v<1 or v>maximum for v,maximum in zip(values,(10000,1000000,1000,100))):
            raise HTTPException(422,'Invalid access code limits')
        label=payload.get('label','New access code')
        if not isinstance(label,str) or not 1<=len(label.strip())<=100:
            raise HTTPException(422,'Provide code label (1–100 characters)')
        expiry=payload.get('expires_at','')
        if not isinstance(expiry,str) or (expiry and (len(expiry)>35 or not valid_expiry(expiry))):
            raise HTTPException(422,'Use an ISO UTC expiry date-time')
        with db() as con:
            user=admin(request,con)
            if con.execute('SELECT COUNT(*) FROM saas_codes').fetchone()[0]>=10000:
                raise HTTPException(409,'Maximum access codes reached')
            code=secrets.token_urlsafe(24)
            con.execute('INSERT INTO saas_codes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                secrets.token_hex(16),digest(code),label.strip(),kind,*values[:3],values[3],0,
                expiry,1,user['id'],now()))
        return {'access_code':code,'label':label.strip(),'workspace_type':kind,
                'max_events':values[0],'max_guests':values[1],'max_members':values[2],'max_uses':values[3]}

    @app.patch('/api/platform/codes/{cid}')
    def update_code(request:Request,cid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'is_active'} or type(payload['is_active']) is not bool:
            raise HTTPException(422,'Only code activation may change')
        with db() as con:
            admin(request,con)
            n=con.execute('UPDATE saas_codes SET is_active=? WHERE id=?',(int(payload['is_active']),cid)).rowcount
            if not n:raise HTTPException(404,'Code not found')
        return {'ok':True}

    @app.put('/api/platform/settings')
    def settings(request:Request,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'require_access_code'} or type(payload['require_access_code']) is not bool:
            raise HTTPException(422,'Invalid platform signup setting')
        with db() as con:
            admin(request,con)
            con.execute("UPDATE saas_settings SET value=? WHERE `key`='require_access_code'",
                        ('1' if payload['require_access_code'] else '0',))
        return {'ok':True}

    @app.patch('/api/platform/workspaces/{wid}')
    def update_workspace(request:Request,wid:str,payload:dict):
        allowed={'is_active','max_events','max_guests','max_members','expires_at'}
        if not isinstance(payload,dict) or not payload or set(payload)-allowed:
            raise HTTPException(422,'Invalid workspace settings')
        clean={}
        for key,value in payload.items():
            if key=='is_active':
                if type(value) is not bool:raise HTTPException(422,'Invalid status')
                clean[key]=int(value)
            elif key=='expires_at':
                if not isinstance(value,str) or value and not valid_expiry(value):
                    raise HTTPException(422,'Invalid expiration date')
                clean[key]=value
            else:
                if type(value) is not int or not 1<=value<=1000000:
                    raise HTTPException(422,'Invalid workspace limit')
                clean[key]=value
        with db() as con:
            actor=admin(request,con)
            sql=', '.join(key+'=?' for key in clean)
            count=con.execute(f'UPDATE workspaces SET {sql} WHERE id=?',(*clean.values(),wid)).rowcount
            if not count:raise HTTPException(404,'Workspace not found')
            from phase7_billing import audit
            audit(con,actor['id'],'workspace.settings','workspace',wid)
        return {'ok':True}


    @app.get('/api/w/{wid}/features')
    def workspace_features(request:Request,wid:str):
        with db() as con:
            access(request,con,wid)
            return {'features':feature_settings(con,wid)}

    @app.patch('/api/platform/workspaces/{wid}/features')
    def platform_features(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'feature','enabled'}:
            raise HTTPException(422,'Choose one feature and enabled flag')
        feature=payload['feature'];enabled=payload['enabled']
        if not isinstance(feature,str) or feature not in FEATURES or type(enabled) is not bool:
            raise HTTPException(422,'Invalid feature flag')
        with db() as con:
            user=admin(request,con)
            if not con.execute('SELECT 1 FROM workspaces WHERE id=?',(wid,)).fetchone():
                raise HTTPException(404,'Workspace not found')
            con.execute("""INSERT INTO saas_feature_settings(workspace_id,feature,enabled,changed_at)
                  VALUES(?,?,?,?) ON CONFLICT(workspace_id,feature) DO UPDATE
                  SET enabled=excluded.enabled,changed_at=excluded.changed_at""",
                  (wid,feature,int(enabled),now()))
            from phase7_billing import audit
            audit(con,user['id'],'feature.'+('enabled' if enabled else 'disabled')+'.'+feature,'workspace',wid)
            return {'features':feature_settings(con,wid)}

    @app.patch('/api/platform/users/{user_id}')
    def update_user(request:Request,user_id:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'is_active'} or type(payload['is_active']) is not bool:
            raise HTTPException(422,'Invalid user status')
        with db() as con:
            current=admin(request,con)
            if current['id']==user_id and not payload['is_active']:
                raise HTTPException(409,'Cannot disable your own administrator account')
            target=con.execute('SELECT platform_role FROM users WHERE id=?',(user_id,)).fetchone()
            if not target:raise HTTPException(404,'User not found')
            if target['platform_role']=='admin' and not payload['is_active']:
                raise HTTPException(409,'Platform administrators cannot be disabled from this panel')
            con.execute('UPDATE users SET is_active=? WHERE id=?',(int(payload['is_active']),user_id))
            from phase7_billing import audit
            audit(con,current['id'],'user.status','user',user_id)
        return {'ok':True}


def valid_expiry(value):
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00',value):return False
    try:return datetime.fromisoformat(value)>datetime.now(timezone.utc)
    except ValueError:return False

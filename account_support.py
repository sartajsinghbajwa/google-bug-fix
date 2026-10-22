"""Self-service accounts and tenant-private support tickets for EventOS.

Four additive tables. No password, session token or email-verification token is
returned to the browser. The customer and operator queues are authorization scoped.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def schema_statements():
    return [
        """CREATE TABLE IF NOT EXISTS account_profiles (
             user_id VARCHAR(191) PRIMARY KEY,
             phone VARCHAR(40) NOT NULL DEFAULT '',
             company VARCHAR(140) NOT NULL DEFAULT '',
             timezone VARCHAR(80) NOT NULL DEFAULT 'UTC',
             support_email_updates INTEGER NOT NULL DEFAULT 1,
             updated_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS account_email_changes (
             token_hash VARCHAR(64) PRIMARY KEY,
             user_id VARCHAR(191) NOT NULL,
             new_email VARCHAR(255) NOT NULL,
             expires_at VARCHAR(40) NOT NULL,
             requested_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS support_tickets (
             id VARCHAR(64) PRIMARY KEY,
             ticket_number VARCHAR(24) NOT NULL UNIQUE,
             user_id VARCHAR(191) NOT NULL,
             workspace_id VARCHAR(191) NOT NULL DEFAULT '',
             subject VARCHAR(160) NOT NULL,
             category VARCHAR(32) NOT NULL,
             priority VARCHAR(16) NOT NULL DEFAULT 'normal',
             status VARCHAR(32) NOT NULL DEFAULT 'open',
             created_at VARCHAR(40) NOT NULL,
             updated_at VARCHAR(40) NOT NULL,
             closed_at VARCHAR(40) NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS support_ticket_messages (
             id VARCHAR(64) PRIMARY KEY,
             ticket_id VARCHAR(64) NOT NULL,
             author_id VARCHAR(191) NOT NULL,
             author_role VARCHAR(16) NOT NULL,
             body TEXT NOT NULL,
             created_at VARCHAR(40) NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_account_email_user ON account_email_changes(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_support_owner_recent ON support_tickets(user_id,created_at)",
        "CREATE INDEX IF NOT EXISTS idx_support_status_recent ON support_tickets(status,updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_support_thread ON support_ticket_messages(ticket_id,created_at)",
    ]


def migrate(db):
    with db() as con:
        for statement in schema_statements():
            con.execute(statement)


def require_string(payload, key, min_len, max_len):
    value = payload.get(key)
    if not isinstance(value, str) or not min_len <= len(value.strip()) <= max_len:
        raise HTTPException(422, f'{key.replace("_", " ").capitalize()} must contain {min_len}–{max_len} characters')
    return value.strip()


def check_payload(payload, keys, required=()):
    if not isinstance(payload,dict) or set(payload)-set(keys) or not set(required).issubset(payload):
        raise HTTPException(422,'Invalid fields submitted')


def send_optional(email, subject, message):
    from event_operations import mail_enabled, send_email
    if not mail_enabled():
        return
    try:
        send_email(email, subject, message)
    except Exception:
        # Do not roll back a persisted ticket merely because SMTP fails.
        pass


def record_ticket(con, ticket_id, user):
    row=con.execute('''SELECT t.*,u.full_name AS customer_name,u.email AS customer_email,
               w.name AS workspace_name FROM support_tickets t
               JOIN users u ON u.id=t.user_id
               LEFT JOIN workspaces w ON w.id=t.workspace_id
               WHERE t.id=?''',(ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404,'Support ticket not found')
    if user['platform_role']!='admin' and row['user_id']!=user['id']:
        raise HTTPException(404,'Support ticket not found')
    return dict(row)


def install(app, db, get_user, password_hash, valid_password, set_session, cookie_secure=False, mariadb=False):
    # On existing WHC MariaDB, run the bundled additive schema preflight before
    # restarting the application. Local SQLite testing/installs initialize here.
    if not mariadb:
        migrate(db)

    @app.get('/api/account/overview')
    def overview(request:Request):
        with db() as con:
            u=get_user(request,con)
            info=con.execute('SELECT created_at FROM users WHERE id=?',(u['id'],)).fetchone()
            profile=con.execute('SELECT phone,company,timezone,support_email_updates FROM account_profiles WHERE user_id=?',(u['id'],)).fetchone()
            workspaces=[dict(r) for r in con.execute('''SELECT w.id,w.name,w.type,w.currency,w.billing_plan,w.billing_status,
                w.max_events,w.max_guests,w.max_members,w.expires_at,m.role,
                (SELECT COUNT(*) FROM events WHERE workspace_id=w.id) AS events,
                (SELECT COUNT(*) FROM guests WHERE workspace_id=w.id) AS guests,
                (SELECT COUNT(*) FROM memberships WHERE workspace_id=w.id) AS members,
                CASE WHEN b.stripe_customer_id IS NOT NULL AND b.stripe_customer_id<>'' THEN 1 ELSE 0 END AS has_stripe_customer
                FROM workspaces w JOIN memberships m ON m.workspace_id=w.id
                LEFT JOIN saas_billing_accounts b ON b.workspace_id=w.id
                WHERE m.user_id=? ORDER BY w.name''',(u['id'],))]
            stats={r['status']:r['total'] for r in con.execute('SELECT status,COUNT(*) AS total FROM support_tickets WHERE user_id=? GROUP BY status',(u['id'],))}
            return {'user':{**u,'created_at':info['created_at']},
                'profile':dict(profile) if profile else {'phone':'','company':'','timezone':'UTC','support_email_updates':1},
                'workspaces':workspaces,'ticket_counts':stats}

    @app.patch('/api/account/profile')
    def update_profile(request:Request,payload:dict):
        check_payload(payload,{'full_name','phone','company','timezone','support_email_updates'})
        if not payload:
            raise HTTPException(422,'Enter profile changes')
        data={}
        for key,length in [('full_name',120),('phone',40),('company',140),('timezone',80)]:
            if key in payload:
                if not isinstance(payload[key],str) or len(payload[key].strip())>length or (key=='full_name' and not payload[key].strip()):
                    raise HTTPException(422,'Invalid '+key)
                data[key]=payload[key].strip()
        if 'support_email_updates' in payload:
            if type(payload['support_email_updates']) is not bool:
                raise HTTPException(422,'Invalid support notification setting')
            data['support_email_updates']=int(payload['support_email_updates'])
        if 'timezone' in data:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(data['timezone'])
            except Exception:
                raise HTTPException(422,'Enter a valid IANA timezone, such as America/Toronto')
        with db() as con:
            u=get_user(request,con)
            if 'full_name' in data:
                con.execute('UPDATE users SET full_name=? WHERE id=?',(data.pop('full_name'),u['id']))
            old=con.execute('SELECT phone,company,timezone,support_email_updates FROM account_profiles WHERE user_id=?',(u['id'],)).fetchone()
            next_profile={**({'phone':'','company':'','timezone':'UTC','support_email_updates':1} if not old else dict(old)),**data}
            con.execute('''INSERT INTO account_profiles (user_id,phone,company,timezone,support_email_updates,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                phone=excluded.phone,company=excluded.company,timezone=excluded.timezone,
                support_email_updates=excluded.support_email_updates,updated_at=excluded.updated_at''',
                (u['id'],next_profile['phone'],next_profile['company'],next_profile['timezone'],next_profile['support_email_updates'],stamp()))
            return {'ok':True}

    @app.post('/api/account/password')
    def change_password(request:Request,payload:dict,response:Response):
        check_payload(payload,{'current_password','new_password'},{'current_password','new_password'})
        old=payload['current_password'];new=payload['new_password']
        if not isinstance(old,str) or not 1<=len(old)<=256 or not isinstance(new,str) or not 12<=len(new)<=256:
            raise HTTPException(422,'Enter your current password and a new password of 12–256 characters')
        if secrets.compare_digest(old,new):
            raise HTTPException(422,'New password must be different')
        with db() as con:
            u=get_user(request,con)
            password=con.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()['password_hash']
            if not valid_password(old,password):
                raise HTTPException(403,'Current password is incorrect')
            con.execute('UPDATE users SET password_hash=? WHERE id=?',(password_hash(new),u['id']))
            con.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
            con.execute('DELETE FROM auth_one_time_tokens WHERE user_id=?',(u['id'],))
            set_session(response,con,u['id'])
            from email_notifications import enqueue_user
            enqueue_user(con,u['id'],'security','Your EventOS password was changed',
                         'Your account password was updated. If this was not you, contact support immediately.','/app')
        return {'ok':True,'message':'Password changed; all previous sessions have been signed out.'}

    @app.post('/api/account/email-change')
    def start_email_change(request:Request,payload:dict):
        check_payload(payload,{'new_email','current_password'},{'new_email','current_password'})
        email=require_string(payload,'new_email',5,255).lower()
        old=payload['current_password']
        if not isinstance(old,str) or not 1<=len(old)<=256:
            raise HTTPException(422,'Enter your current password')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
            raise HTTPException(422,'Enter a valid email address')
        from event_operations import mail_enabled, send_email
        from phase7_auth import origin
        if not mail_enabled():
            raise HTTPException(503,'Email changes require configured SMTP verification')
        base=origin()
        with db() as con:
            u=get_user(request,con)
            stored=con.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()['password_hash']
            if not valid_password(old,stored):
                raise HTTPException(403,'Current password is incorrect')
            if u['email']==email:
                raise HTTPException(422,'That is already your email address')
            if con.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
                raise HTTPException(409,'Email address is already in use')
            raw=secrets.token_urlsafe(36)
            expires=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(timespec='seconds')
            con.execute('DELETE FROM account_email_changes WHERE user_id=?',(u['id'],))
            con.execute('INSERT INTO account_email_changes VALUES(?,?,?,?,?)',
                (token_hash(raw),u['id'],email,expires,stamp()))
        from urllib.parse import quote
        link=base+'/app#/confirm-email-change?token='+quote(raw,safe='')
        try:
            send_email(email,'Confirm your new EventOS email address',
                'To complete your EventOS email change, open this one-time link:\n'+link+
                '\n\nIt expires in 1 hour. If you did not request this change, ignore this message.')
        except Exception:
            raise HTTPException(503,'Verification email could not be delivered. Please retry.')
        return {'ok':True,'message':'Check your new email address for a confirmation link.'}

    @app.post('/api/account/email-confirm')
    def confirm_email(payload:dict):
        check_payload(payload,{'token'},{'token'})
        raw=require_string(payload,'token',30,180)
        with db() as con:
            row=con.execute('SELECT * FROM account_email_changes WHERE token_hash=? AND expires_at>?',
                (token_hash(raw),stamp())).fetchone()
            if not row:
                raise HTTPException(422,'Link expired or already used')
            if con.execute('SELECT 1 FROM users WHERE email=? AND id<>?',(row['new_email'],row['user_id'])).fetchone():
                raise HTTPException(409,'This email address is already in use')
            try:
                changed=con.execute('UPDATE users SET email=?,email_verified=1 WHERE id=? AND is_active=1',
                    (row['new_email'],row['user_id'])).rowcount
            except Exception as exc:
                raise HTTPException(409,'The email update could not be completed') from exc
            if changed!=1:
                raise HTTPException(403,'Account is inactive')
            con.execute('DELETE FROM account_email_changes WHERE user_id=?',(row['user_id'],))
            con.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],))
            con.execute('DELETE FROM auth_one_time_tokens WHERE user_id=?',(row['user_id'],))
            return {'ok':True,'message':'Email updated. Sign in again with the new address.'}

    @app.get('/api/support/tickets')
    def tickets(request:Request):
        with db() as con:
            u=get_user(request,con)
            rows=con.execute('''SELECT id,ticket_number,subject,category,priority,status,created_at,updated_at
                FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC LIMIT 200''',(u['id'],))
            return {'tickets':[dict(r) for r in rows]}

    @app.post('/api/support/tickets')
    def new_ticket(request:Request,payload:dict):
        check_payload(payload,{'subject','category','priority','message','workspace_id'},{'subject','category','message'})
        subject=require_string(payload,'subject',5,160)
        body=require_string(payload,'message',15,5000)
        category=payload['category'];priority=payload.get('priority','normal')
        if category not in ('account','billing','technical','event','other') or priority not in ('low','normal','high'):
            raise HTTPException(422,'Invalid category or priority')
        requested_wid=payload.get('workspace_id','')
        if not isinstance(requested_wid,str) or len(requested_wid)>191:
            raise HTTPException(422,'Invalid workspace')
        with db() as con:
            u=get_user(request,con)
            if requested_wid and not con.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',
                (requested_wid,u['id'])).fetchone():
                raise HTTPException(403,'Workspace is not accessible')
            cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds')
            if con.execute('SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND created_at>?',
                (u['id'],cutoff)).fetchone()[0]>=5:
                raise HTTPException(429,'Ticket limit reached. Please try again later.')
            if con.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND status NOT IN ('resolved','closed')",(u['id'],)).fetchone()[0]>=25:
                raise HTTPException(429,'You have too many open tickets; please wait for a reply')
            tid=uuid.uuid4().hex
            ticket_number='EV-'+datetime.now(timezone.utc).strftime('%y%m%d')+'-'+secrets.token_hex(4).upper()
            current=stamp()
            con.execute('''INSERT INTO support_tickets (id,ticket_number,user_id,workspace_id,subject,category,priority,status,created_at,updated_at,closed_at)
                VALUES (?,?,?,?,?,?,?,'open',?,?,'')''',(tid,ticket_number,u['id'],requested_wid,subject,category,priority,current,current))
            con.execute('INSERT INTO support_ticket_messages VALUES (?,?,?,?,?,?)',
                (uuid.uuid4().hex,tid,u['id'],'customer',body,current))
            from email_notifications import enqueue_user, admins
            enqueue_user(con,u['id'],'support','EventOS support ticket received','Your support request '+ticket_number+' has been received.','/app',dedupe='support-created:'+tid)
            admins(con,'support','New EventOS support request','A customer opened support ticket '+ticket_number+'.','/app',dedupe_prefix='support-admin:'+tid)
            return {'ok':True,'id':tid,'ticket_number':ticket_number}

    @app.get('/api/support/tickets/{ticket_id}')
    def ticket_detail(request:Request,ticket_id:str):
        with db() as con:
            u=get_user(request,con)
            ticket=record_ticket(con,ticket_id,u)
            messages=[dict(r) for r in con.execute('''SELECT m.id,m.author_role,m.body,m.created_at,u.full_name AS author_name
                FROM support_ticket_messages m JOIN users u ON u.id=m.author_id
                WHERE m.ticket_id=? ORDER BY m.created_at ASC,m.id ASC LIMIT 500''',(ticket_id,))]
            return {'ticket':ticket,'messages':messages}

    @app.post('/api/support/tickets/{ticket_id}/reply')
    def reply(request:Request,ticket_id:str,payload:dict):
        check_payload(payload,{'message'},{'message'})
        body=require_string(payload,'message',2,5000)
        with db() as con:
            u=get_user(request,con)
            ticket=record_ticket(con,ticket_id,u)
            if ticket['status']=='closed':
                raise HTTPException(409,'Ticket is closed; please start a new one')
            is_staff=u['platform_role']=='admin'
            status='waiting_on_customer' if is_staff else 'open'
            con.execute('INSERT INTO support_ticket_messages VALUES (?,?,?,?,?,?)',
                (uuid.uuid4().hex,ticket_id,u['id'],'staff' if is_staff else 'customer',body,stamp()))
            con.execute('UPDATE support_tickets SET status=?,updated_at=? WHERE id=?',(status,stamp(),ticket_id))
            from email_notifications import enqueue_user, admins
            if is_staff and ticket['user_id']!=u['id']:
                preference=con.execute('SELECT support_email_updates FROM account_profiles WHERE user_id=?',(ticket['user_id'],)).fetchone()
                if not preference or preference['support_email_updates']:
                    enqueue_user(con,ticket['user_id'],'support','EventOS support replied: '+ticket['ticket_number'],
                         'You have a new support reply. Sign in to view it.','/app')
            elif not is_staff:
                admins(con,'support','Customer replied to '+ticket['ticket_number'],
                       'A customer added a reply to an EventOS support ticket.','/app')
        return {'ok':True,'status':status}

    @app.get('/api/platform/support')
    def operator_inbox(request:Request):
        with db() as con:
            u=get_user(request,con)
            if u['platform_role']!='admin':
                raise HTTPException(403,'Platform administrator permission required')
            rows=[dict(r) for r in con.execute('''SELECT t.id,t.ticket_number,t.subject,t.category,t.priority,t.status,t.created_at,t.updated_at,
                u.email AS customer_email,u.full_name AS customer_name,w.name AS workspace_name
                FROM support_tickets t JOIN users u ON u.id=t.user_id
                LEFT JOIN workspaces w ON w.id=t.workspace_id ORDER BY t.updated_at DESC LIMIT 500''')]
            counts={r['status']:r['total'] for r in con.execute('SELECT status,COUNT(*) AS total FROM support_tickets GROUP BY status')}
            return {'tickets':rows,'counts':counts}

    @app.patch('/api/platform/support/{ticket_id}')
    def operator_status(request:Request,ticket_id:str,payload:dict):
        check_payload(payload,{'status'},{'status'})
        if payload['status'] not in ('open','in_progress','waiting_on_customer','resolved','closed'):
            raise HTTPException(422,'Invalid support status')
        with db() as con:
            u=get_user(request,con)
            if u['platform_role']!='admin':
                raise HTTPException(403,'Platform administrator permission required')
            record_ticket(con,ticket_id,u)
            con.execute('UPDATE support_tickets SET status=?,updated_at=?,closed_at=? WHERE id=?',
                (payload['status'],stamp(),stamp() if payload['status']=='closed' else '',ticket_id))
            from email_notifications import enqueue_user
            enqueue_user(con,record_ticket(con,ticket_id,u)['user_id'],'support','Support ticket status updated',
                         'Your support ticket status changed to '+payload['status'].replace('_',' ')+'.','/app')
            from phase7_billing import audit
            audit(con,u['id'],'support.status','support_ticket',ticket_id)
            return {'ok':True}

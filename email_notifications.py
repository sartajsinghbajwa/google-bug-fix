"""EventOS durable, opt-in transactional mail outbox for shared WHC/cPanel.

No SMTP credentials are stored in SQL. Without an explicit enable flag and valid
SMTP configuration, event hooks do not enqueue backlogs for surprise later mailing.
Run `scripts/send_email_notifications.py` from cPanel Cron Jobs (not a web endpoint).
Delivery is at least once if a worker crashes after SMTP accepted a message.
"""
from __future__ import annotations
import os, re, secrets, uuid
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request

KINDS = ('events', 'rsvp', 'vendors', 'contracts', 'billing', 'support', 'announcements', 'security')
OPTIONAL = tuple(x for x in KINDS if x != 'security')
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS email_notification_preferences (
       user_id VARCHAR(191) PRIMARY KEY,
       events INTEGER NOT NULL DEFAULT 1, rsvp INTEGER NOT NULL DEFAULT 1,
       vendors INTEGER NOT NULL DEFAULT 1, contracts INTEGER NOT NULL DEFAULT 1,
       billing INTEGER NOT NULL DEFAULT 1, support INTEGER NOT NULL DEFAULT 1,
       announcements INTEGER NOT NULL DEFAULT 0,
       updated_at VARCHAR(40) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS email_notification_outbox (
       id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(191) NOT NULL,
       kind VARCHAR(24) NOT NULL, subject VARCHAR(180) NOT NULL,
       body TEXT NOT NULL, link_url VARCHAR(300) NOT NULL DEFAULT '/app',
       dedupe_key VARCHAR(191) NOT NULL UNIQUE,
       status VARCHAR(16) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
       next_attempt_at VARCHAR(40) NOT NULL, lease_until VARCHAR(40) NOT NULL DEFAULT '',
       last_error VARCHAR(160) NOT NULL DEFAULT '', created_at VARCHAR(40) NOT NULL,
       sent_at VARCHAR(40) NOT NULL DEFAULT '')""",
    'CREATE INDEX IF NOT EXISTS idx_email_jobs_ready ON email_notification_outbox(status,next_attempt_at)',
    'CREATE INDEX IF NOT EXISTS idx_email_jobs_user ON email_notification_outbox(user_id,created_at)',
)

def utc(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def configured():
    from event_operations import mail_enabled
    return mail_enabled() and os.environ.get('EVENTOS_EMAIL_NOTIFICATIONS_ENABLED','0')=='1'

def migrate(db):
    with db() as con:
        for statement in SCHEMA:
            con.execute(statement)

def internal_url(url):
    return (isinstance(url,str) and url.startswith('/') and not url.startswith('//')
            and '\\' not in url and '\r' not in url and '\n' not in url and len(url)<=300)

def enqueue_user(con,user_id,kind,subject,body='',link='/app',dedupe=None):
    """Same transaction as the action; never send SMTP before SQL commits."""
    if not configured():return False
    if kind not in KINDS:raise ValueError('Unknown email notification category')
    if not isinstance(user_id,str) or not user_id or not internal_url(link):raise ValueError('Invalid notification recipient or link')
    if not isinstance(subject,str) or '\n' in subject or '\r' in subject or len(subject)>180:raise ValueError('Invalid mail subject')
    if not isinstance(body,str):raise ValueError('Invalid mail body')
    row=con.execute('SELECT is_active,email FROM users WHERE id=?',(user_id,)).fetchone()
    if not row or not row['is_active'] or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',row['email']):return False
    if kind!='security':
        pref=con.execute('SELECT '+kind+' AS enabled FROM email_notification_preferences WHERE user_id=?',(user_id,)).fetchone()
        if pref and not pref['enabled']:return False
        if not pref and kind=='announcements':return False
    key=dedupe or ('notice:'+uuid.uuid4().hex)
    if not isinstance(key,str) or len(key)>191:raise ValueError('Invalid dedupe key')
    con.execute('INSERT OR IGNORE INTO email_notification_outbox '
                '(id,user_id,kind,subject,body,link_url,dedupe_key,status,next_attempt_at,created_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,user_id,kind,subject[:180],body[:2500],link,key,'pending',utc(),utc()))
    return True

def admins(con,kind,subject,body='',link='/app',dedupe_prefix=''):
    recipients=con.execute("SELECT id FROM users WHERE platform_role='admin' AND is_active=1").fetchall()
    for row in recipients:
        enqueue_user(con,row['id'],kind,subject,body,link,
                     dedupe=(dedupe_prefix+':'+row['id']) if dedupe_prefix else None)

def workspace_members(con,wid,kind,subject,body='',link='/app',dedupe_prefix=''):
    for row in con.execute('SELECT DISTINCT user_id FROM memberships WHERE workspace_id=?',(wid,)):
        enqueue_user(con,row['user_id'],kind,subject,body,link,
                     dedupe=(dedupe_prefix+':'+row['user_id']) if dedupe_prefix else None)

def opt_in_announcement(con,audience,subject,body,dedupe_prefix):
    # Opted-in announcements only; never silently send mass marketing mail.

    # Large audiences need a dedicated batch fan-out/ESP instead of one WHC request.
    if not configured():return 0
    recipients=con.execute('''SELECT u.id FROM users u JOIN email_notification_preferences p ON p.user_id=u.id
                  WHERE u.is_active=1 AND p.announcements=1 LIMIT 1001''').fetchall()
    if len(recipients)>1000:return -1
    from growth_center import get_roles
    count=0
    for row in recipients:
        if audience in get_roles(con,{'id':row['id'],'platform_role':'user'}):
            count+=bool(enqueue_user(con,row['id'],'announcements',subject,body,'/app',dedupe_prefix+':'+row['id']))
    return count


def install(app,db,get_user,mariadb=False):
    if not mariadb:migrate(db)
    @app.get('/api/account/email-preferences')
    def preferences(request:Request):
        with db() as con:
            user=get_user(request,con)
            row=con.execute('SELECT * FROM email_notification_preferences WHERE user_id=?',(user['id'],)).fetchone()
            return {'preferences':{k:bool(row[k]) if row else k!='announcements' for k in OPTIONAL},
                    'security_required':True,'delivery_configured':configured()}

    @app.patch('/api/account/email-preferences')
    def set_preferences(request:Request,payload:dict):
        if not isinstance(payload,dict) or not payload or set(payload)-set(OPTIONAL) or any(type(v) is not bool for v in payload.values()):
            raise HTTPException(422,'Choose valid email notification settings')
        with db() as con:
            user=get_user(request,con)
            con.execute('INSERT OR IGNORE INTO email_notification_preferences(user_id,updated_at) VALUES(?,?)',(user['id'],utc()))
            con.execute('UPDATE email_notification_preferences SET '+','.join(k+'=?' for k in payload)+',updated_at=? WHERE user_id=?',
                       (*[int(v) for v in payload.values()],utc(),user['id']))
        return {'ok':True}

    @app.post('/api/platform/email-test')
    def send_test(request:Request):
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':raise HTTPException(403,'Platform administrator required')
            if not configured():raise HTTPException(503,'Configure SMTP and enable email notifications first')
            queued=enqueue_user(con,user['id'],'security','EventOS SMTP test notification',
                                 'Your EventOS mail queue is configured. This test message was created by your administrator account.','/app')
            return {'queued':queued,'note':'Use cPanel email worker to deliver the queued test.'}

    @app.get('/api/platform/email-delivery')
    def delivery_status(request:Request):
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':raise HTTPException(403,'Platform administrator required')
            counts={r['status']:r['count'] for r in con.execute('SELECT status,COUNT(*) AS count FROM email_notification_outbox GROUP BY status')}
        from event_operations import mail_enabled
        return {'smtp_configured':mail_enabled(),'email_notifications_enabled':os.environ.get('EVENTOS_EMAIL_NOTIFICATIONS_ENABLED')=='1',
                'worker_required':True,'queue_counts':counts,'categories':list(KINDS)}

def process_queue(db,batch_size=30):
    """Designed for cPanel cron. Uses a conditional SQL lease; retries transient failures.

    Caution: Delivery is at least once, not exactly once; an SMTP crash may
    cause duplicate transactional messages. Avoid putting tokens/secrets in mail.
    """
    from event_operations import send_email
    if not configured():return {'disabled':True,'sent':0,'failed':0}
    limit=max(1,min(int(batch_size),100))
    now=utc()
    with db() as con:
        rows=con.execute('''SELECT id FROM email_notification_outbox
             WHERE ((status IN ('pending','retry') AND next_attempt_at<=?) OR
                (status='sending' AND lease_until<?)) AND attempts<5
             ORDER BY created_at LIMIT ?''',(now,now,limit)).fetchall()
    sent=failed=0
    for row in rows:
        uid=row['id'];now=utc();lease=(datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat(timespec='seconds')
        with db() as con:
            changed=con.execute('''UPDATE email_notification_outbox SET status='sending',lease_until=?,attempts=attempts+1
                  WHERE id=? AND ((status IN ('pending','retry') AND next_attempt_at<=?) OR
                  (status='sending' AND lease_until<?)) AND attempts<5''',(lease,uid,now,now)).rowcount
            if not changed:continue
            job=con.execute('''SELECT o.*,u.email,u.is_active FROM email_notification_outbox o
                              LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?''',(uid,)).fetchone()
            pref=con.execute('SELECT '+job['kind']+' AS enabled FROM email_notification_preferences WHERE user_id=?',(job['user_id'],)).fetchone() if job['kind']!='security' else None
            valid=job['is_active'] and job['email'] and re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',job['email'])
            allowed=(job['kind']=='security' or (pref['enabled'] if pref else job['kind']!='announcements'))
            if not valid or not allowed:
                con.execute("UPDATE email_notification_outbox SET status='skipped',lease_until='' WHERE id=?",(uid,));continue
            recipient=job['email'];subject=job['subject'];body=job['body'];link=job['link_url']
        try:
            base=os.getenv('EVENTOS_PUBLIC_BASE_URL','').rstrip('/')
            # Keep links internal and only add a configured HTTPS hostname.
            detail='\n\nOpen EventOS to review your account.'
            if base.startswith('https://') and internal_url(link):detail='\n\nOpen EventOS: '+base+link
            send_email(recipient,subject,'Hello,\n\n'+body+detail+'\n\nYou can change routine email categories in My Account.\nEventOS')
        except Exception as exc:
            with db() as con:
                attempts=job['attempts']
                backoff=(datetime.now(timezone.utc)+timedelta(minutes=min(60,2**min(attempts,5)))).isoformat(timespec='seconds')
                con.execute("UPDATE email_notification_outbox SET status=?,next_attempt_at=?,lease_until='',last_error=? WHERE id=? AND status='sending'",
                            ('failed' if attempts>=5 else 'retry',backoff,type(exc).__name__[:160],uid))
            failed+=1
        else:
            with db() as con:
                con.execute("UPDATE email_notification_outbox SET status='sent',sent_at=?,lease_until='',last_error='' WHERE id=? AND status='sending'",(utc(),uid))
            sent+=1
    return {'disabled':False,'sent':sent,'failed':failed}

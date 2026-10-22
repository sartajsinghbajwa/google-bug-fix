"""EventOS Phase 7 subscription lifecycle.

Stripe is strictly opt-in. Only authenticated workspace owners can open checkout
or the billing portal. No plan is upgraded by visiting a success URL; signed,
idempotent Stripe subscription webhooks determine billing state and quotas.
"""
from __future__ import annotations

import os
import gzip
import hashlib
import secrets
import tempfile
import sqlite3
from pathlib import Path
from functools import lru_cache
from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response

PLANS = {
    'free': {'max_events': 3, 'max_guests': 120, 'max_members': 2, 'label': 'Starter'},
    'pro': {'max_events': 10, 'max_guests': 1000, 'max_members': 5, 'label': 'Pro'},
    'business': {'max_events': 60, 'max_guests': 10000, 'max_members': 25, 'label': 'Business'},
}
ACTIVE = {'active', 'trialing'}
VALID_STATUSES = {'active', 'trialing', 'past_due', 'unpaid', 'canceled', 'incomplete',
                  'incomplete_expired', 'paused', 'manual', 'legacy', 'free'}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def price_id_for(plan,cycle='monthly',con=None):
    if cycle not in ('monthly','yearly'):return None
    if plan=='free':return None
    if con is not None:
        row=con.execute('SELECT stripe_monthly_price,stripe_yearly_price,is_active FROM platform_packages WHERE id=?',(plan,)).fetchone()
        if not row or not row['is_active']:return None
        configured=row['stripe_yearly_price' if cycle=='yearly' else 'stripe_monthly_price']
        if configured:return configured
    if plan not in ('pro','business'):return None
    suffix='_YEARLY' if cycle=='yearly' else ''
    return os.getenv('EVENTOS_STRIPE_PRICE_'+plan.upper()+suffix,'').strip() or None


def stripe_ready():
    return bool(os.getenv('EVENTOS_STRIPE_SECRET_KEY') and os.getenv('EVENTOS_STRIPE_WEBHOOK_SECRET')
                and os.getenv('EVENTOS_PUBLIC_BASE_URL','').startswith('https://'))


def price_to_plan(price_id,con=None):
    if not price_id:return None
    if con:
        return next((row['id'] for row in con.execute('SELECT id,stripe_monthly_price,stripe_yearly_price FROM platform_packages WHERE is_active=1')
                     if price_id in (row['stripe_monthly_price'],row['stripe_yearly_price'])),None) or \
                next((p for p in ('pro','business') for c in ('monthly','yearly') if price_id_for(p,c)==price_id),None)
    return next((p for p in ('pro','business') for c in ('monthly','yearly') if price_id_for(p,c)==price_id),None)


@lru_cache(maxsize=20)
def _public_price(price_id, expected_interval, secret_key):
    # The public page never reads price amounts from hard-coded marketing copy.
    # Values are returned only when Stripe identifies the configured recurring price.
    stripe=stripe_api()
    price=stripe.Price.retrieve(price_id)
    details=price if isinstance(price,dict) else dict(price)
    recurring=details.get('recurring') or {}
    if not details.get('active') or recurring.get('interval')!=expected_interval:
        return None
    cents=details.get('unit_amount')
    currency=details.get('currency')
    if type(cents) is not int or cents<0 or not isinstance(currency,str):
        return None
    return {'amount_cents':cents,'currency':currency.upper(),'interval':expected_interval}


def public_prices(db=None):
    results=[]
    if db:
        from platform_studio import packages
        with db() as con:
            rows=packages(con,only_active=True)
            plan_prices=[(x['id'],x, {c:price_id_for(x['id'],c,con) for c in ('monthly','yearly')}) for x in rows]
    else:
        plan_prices=[(slug,details,{c:price_id_for(slug,c) for c in ('monthly','yearly')}) for slug,details in PLANS.items()]
    for slug,details,ids in plan_prices:
        prices={'monthly':None,'yearly':None}
        if slug!='free' and stripe_ready():
            for cycle,pid in ids.items():
                if pid:
                    try:prices[cycle]=_public_price(pid,'month' if cycle=='monthly' else 'year',os.environ['EVENTOS_STRIPE_SECRET_KEY'])
                    except Exception:pass
        results.append({'id':slug,'label':details['label'],'max_events':details['max_events'],
                        'max_guests':details['max_guests'],'max_members':details['max_members'],
                        'prices':prices,'available':slug=='free' or any(prices.values())})
    return {'plans':results,'billing_enabled':stripe_ready()}



def stripe_api():
    if not stripe_ready():
        raise HTTPException(409, 'Stripe Billing is not configured by the platform administrator')
    import stripe
    stripe.api_key = os.environ['EVENTOS_STRIPE_SECRET_KEY']
    return stripe


def migrate(db):
    with db() as con:
        cols = {x['name'] for x in con.execute('PRAGMA table_info(workspaces)')}
        for name,definition in [
            ('billing_plan', "TEXT NOT NULL DEFAULT 'legacy'"),
            ('billing_status', "TEXT NOT NULL DEFAULT 'legacy'"),
            ('billing_updated_at', "TEXT NOT NULL DEFAULT ''"),
        ]:
            if name not in cols:
                con.execute('ALTER TABLE workspaces ADD COLUMN '+name+' '+definition)
        con.executescript("""
        CREATE TABLE IF NOT EXISTS saas_billing_accounts (
          workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
          stripe_customer_id TEXT NOT NULL UNIQUE,
          stripe_subscription_id TEXT NOT NULL DEFAULT '',
          stripe_price_id TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS saas_billing_events (
          event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
          processed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS saas_admin_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          actor_user_id TEXT NOT NULL,
          action TEXT NOT NULL,
          target_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_saas_audit_recent ON saas_admin_audit(created_at DESC);
        CREATE TABLE IF NOT EXISTS saas_backup_history(
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, storage TEXT NOT NULL,
          size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_saas_backup_recent ON saas_backup_history(created_at DESC);
        """)
        columns={r['name'] for r in con.execute('PRAGMA table_info(saas_billing_accounts)')}
        if 'stripe_event_created' not in columns:
            con.execute("ALTER TABLE saas_billing_accounts ADD COLUMN stripe_event_created INTEGER NOT NULL DEFAULT 0")


def snapshot_bytes(path):
    """SQLite online backup, verified before export. Limit memory requirements."""
    source_path=Path(path)
    if not source_path.is_file():raise HTTPException(503,'Database path is unavailable')
    if source_path.stat().st_size>250*1024*1024:
        raise HTTPException(413,'Database too large for manual export')
    fd,tmp=tempfile.mkstemp(prefix='eventos-snapshot-',suffix='.sqlite3')
    os.close(fd)
    try:
        with sqlite3.connect('file:'+str(source_path.resolve())+'?mode=ro',uri=True,timeout=30) as src:
            with sqlite3.connect(tmp,timeout=30) as dst:
                src.backup(dst,pages=256,sleep=.05)
        if Path(tmp).stat().st_size>250*1024*1024:
            raise HTTPException(413,'Snapshot too large')
        with sqlite3.connect(tmp) as validator:
            if validator.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise HTTPException(503,'Snapshot integrity check failed')
        return gzip.compress(Path(tmp).read_bytes(),compresslevel=6)
    finally:
        Path(tmp).unlink(missing_ok=True)


def last_backup(con):
    row=con.execute('SELECT created_at,storage,size_bytes,sha256 FROM saas_backup_history ORDER BY created_at DESC LIMIT 1').fetchone()
    return dict(row) if row else None


def audit(con, actor, action, target_type, target_id):
    con.execute('INSERT INTO saas_admin_audit(actor_user_id,action,target_type,target_id,created_at) VALUES (?,?,?,?,?)',
                (actor, action, target_type, target_id, utc()))


def activate_plan(con,wid,plan,status='manual'):
    if status not in VALID_STATUSES:
        raise HTTPException(422, 'Invalid subscription status')
    quotas=con.execute('SELECT max_events,max_guests,max_members FROM platform_packages WHERE id=? AND is_active=1',(plan,)).fetchone()
    if not quotas:
        raise HTTPException(422, 'Invalid subscription package')
    changed=con.execute("""UPDATE workspaces SET billing_plan=?,billing_status=?,billing_updated_at=?,
            max_events=?,max_guests=?,max_members=? WHERE id=?""",
            (plan,status,utc(),quotas['max_events'],quotas['max_guests'],quotas['max_members'],wid)).rowcount
    if not changed:
        raise HTTPException(404,'Workspace not found')


def owner(request,con,wid,access):
    role=access(request,con,wid)
    if role['role']!='owner':
        raise HTTPException(403,'Workspace owner permission required')
    return role


def subscription_event(con,event):
    """Apply a Stripe subscription only after signature verification upstream."""
    obj=event['data']['object']
    event_id=event['id']
    if con.execute('SELECT 1 FROM saas_billing_events WHERE event_id=?',(event_id,)).fetchone():
        return 'duplicate'
    typ=event['type']
    sub_id=obj.get('id','')
    customer=obj.get('customer','')
    items=(obj.get('items') or {}).get('data') or []
    price=(items[0].get('price') or {}).get('id') if items else None
    plan=price_to_plan(price,con)
    metadata=obj.get('metadata') or {}
    wid=metadata.get('eventos_workspace_id')
    # Initial webhook may precede checkout.session.completed: subscription
    # metadata, not an unverified user-supplied URL, identifies the tenant.
    if not wid:
        saved=con.execute('SELECT workspace_id FROM saas_billing_accounts WHERE stripe_subscription_id=?',(sub_id,)).fetchone()
        wid=saved['workspace_id'] if saved else None
    if not wid or not plan:
        # Unrecognized subscriptions are never given privileges.
        con.execute('INSERT INTO saas_billing_events VALUES (?,?,?)',(event_id,typ,utc()))
        return 'ignored'
    w=con.execute('SELECT id FROM workspaces WHERE id=?',(wid,)).fetchone()
    if not w:
        con.execute('INSERT INTO saas_billing_events VALUES (?,?,?)',(event_id,typ,utc()))
        return 'ignored'
    old=con.execute('SELECT stripe_customer_id FROM saas_billing_accounts WHERE workspace_id=?',(wid,)).fetchone()
    if old and old['stripe_customer_id'] != customer:
        raise HTTPException(409,'Subscription customer mismatch')
    last=con.execute('SELECT stripe_event_created FROM saas_billing_accounts WHERE workspace_id=?',(wid,)).fetchone()
    event_ts=event.get('created',0)
    if type(event_ts) is int and event_ts>0 and last and last['stripe_event_created']>event_ts:
        con.execute('INSERT INTO saas_billing_events VALUES (?,?,?)',(event_id,typ,utc()))
        return 'outdated'
    status=obj.get('status','')
    if typ=='customer.subscription.deleted':
        status='canceled'
    if status not in VALID_STATUSES:
        raise HTTPException(422,'Unsupported Stripe subscription state')
    con.execute("""INSERT INTO saas_billing_accounts
        (workspace_id,stripe_customer_id,stripe_subscription_id,stripe_price_id,updated_at,stripe_event_created)
        VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
        stripe_subscription_id=excluded.stripe_subscription_id,stripe_price_id=excluded.stripe_price_id,
        updated_at=excluded.updated_at,stripe_event_created=excluded.stripe_event_created""",
        (wid,customer,sub_id,price,utc(),event_ts if type(event_ts) is int else 0))
    # Past-due and canceled workspaces keep read access, but free quotas
    # replace paid limits for new creations. Existing records are untouched.
    effective_plan=plan if status in ACTIVE else 'free'
    activate_plan(con,wid,effective_plan,status)
    con.execute('INSERT INTO saas_billing_events VALUES (?,?,?)',(event_id,typ,utc()))
    return 'applied'


def install_billing(app,db,access,get_user):
    migrate(db)

    @app.get('/api/public/plans')
    def published_plans():
        return public_prices(db)

    @app.get('/api/w/{wid}/billing')
    def get_billing(request:Request,wid:str):
        with db() as con:
            role=access(request,con,wid)
            w=con.execute('SELECT billing_plan,billing_status,max_events,max_guests,max_members FROM workspaces WHERE id=?',(wid,)).fetchone()
            stats={
                'events':con.execute('SELECT COUNT(*) FROM events WHERE workspace_id=?',(wid,)).fetchone()[0],
                'guests':con.execute('SELECT COUNT(*) FROM guests WHERE workspace_id=?',(wid,)).fetchone()[0],
                'members':con.execute('SELECT COUNT(*) FROM memberships WHERE workspace_id=?',(wid,)).fetchone()[0],
            }
            return {'plan':w['billing_plan'],'status':w['billing_status'],
                    'limits':{k:w['max_'+k] for k in ('events','guests','members')},
                    'usage':stats,'plans':{r['id']:r for r in __import__('platform_studio').packages(con,True)},'billing_enabled':stripe_ready(),
                    'available_cycles':{row['id']:{cycle:bool(stripe_ready() and price_id_for(row['id'],cycle,con))
                                              for cycle in ('monthly','yearly')}
                                        for row in __import__('platform_studio').packages(con,True) if row['id']!='free'},
                    'can_manage':role['role']=='owner'}

    @app.post('/api/w/{wid}/billing/checkout')
    def checkout(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{'plan','billing_cycle'} or not isinstance(payload.get('plan'),str) or not payload.get('plan') or payload.get('billing_cycle','monthly') not in ('monthly','yearly'):
            raise HTTPException(422,'Choose Pro or Business and a billing period')
        requested=payload['plan']
        cycle=payload.get('billing_cycle','monthly')
        with db() as con:
            owner(request,con,wid,access)
            user=get_user(request,con)
            account=con.execute('SELECT stripe_customer_id FROM saas_billing_accounts WHERE workspace_id=?',(wid,)).fetchone()
            existing=con.execute('SELECT billing_status FROM workspaces WHERE id=?',(wid,)).fetchone()
            if account and existing['billing_status'] in ACTIVE:
                raise HTTPException(409,'Manage an active subscription in the billing portal')
        with db() as con:
            price=price_id_for(requested,cycle,con)
        if not price:
            raise HTTPException(409, 'This subscription plan is not configured')
        stripe=stripe_api()
        site=os.environ['EVENTOS_PUBLIC_BASE_URL'].rstrip('/')
        args={'mode':'subscription','line_items':[{'price':price,'quantity':1}],
              'success_url':site+'/app?billing=return',
              'cancel_url':site+'/app?billing=cancel',
              'client_reference_id':wid,
              'subscription_data':{'metadata':{'eventos_workspace_id':wid}},
              'metadata':{'eventos_workspace_id':wid}}
        if account:
            args['customer']=account['stripe_customer_id']
        else:
            args['customer_email']=user['email']
        try:
            session=stripe.checkout.Session.create(**args)
        except Exception:
            raise HTTPException(502,'Could not open the billing provider; try again later')
        return {'checkout_url':session.url}

    @app.post('/api/w/{wid}/billing/portal')
    def portal(request:Request,wid:str):
        with db() as con:
            owner(request,con,wid,access)
            a=con.execute('SELECT stripe_customer_id FROM saas_billing_accounts WHERE workspace_id=?',(wid,)).fetchone()
            if not a:
                raise HTTPException(404,'This workspace has no provider subscription')
        stripe=stripe_api()
        try:
            session=stripe.billing_portal.Session.create(customer=a['stripe_customer_id'],
                return_url=os.environ['EVENTOS_PUBLIC_BASE_URL'].rstrip('/')+'/app')
        except Exception:
            raise HTTPException(502,'Billing portal is temporarily unavailable')
        return {'portal_url':session.url}

    @app.post('/api/billing/stripe-webhook')
    async def webhook(request:Request):
        if not stripe_ready():
            raise HTTPException(503,'Billing provider is not configured')
        raw=await request.body()
        if len(raw)>512*1024:
            raise HTTPException(413,'Webhook payload too large')
        header=request.headers.get('stripe-signature','')
        stripe=stripe_api()
        try:
            event=stripe.Webhook.construct_event(raw,header,os.environ['EVENTOS_STRIPE_WEBHOOK_SECRET'])
        except (ValueError,stripe.error.SignatureVerificationError):
            raise HTTPException(400,'Invalid billing signature')
        typ=event.get('type','')
        if typ not in ('customer.subscription.created','customer.subscription.updated','customer.subscription.deleted'):
            return {'ok':True,'ignored':True}
        with db() as con:
            result=subscription_event(con,event)
            if result=='applied':
                from email_notifications import workspace_members
                obj=event['data']['object']
                wid=(obj.get('metadata') or {}).get('eventos_workspace_id')
                if not wid:
                    match=con.execute('SELECT workspace_id FROM saas_billing_accounts WHERE stripe_subscription_id=?',(obj.get('id',''),)).fetchone()
                    wid=match['workspace_id'] if match else None
                if wid:
                    workspace_members(con,wid,'billing','EventOS subscription updated',
                         'The subscription status for one of your workspaces has changed. Review billing details in EventOS.',
                         '/app',dedupe_prefix='subscription:'+str(event.get('id','')))
        return {'ok':True,'result':result}

    @app.get('/api/platform/backups')
    def backup_status(request:Request):
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':raise HTTPException(403,'Admin required')
            return {'last_backup':last_backup(con),'automatic_backups_enabled':False,
                    'restore_test_verified':False}

    @app.get('/api/platform/backups/export')
    def export_backup(request:Request):
        if os.getenv('EVENTOS_DATABASE_ENGINE')=='mariadb':
            raise HTTPException(409,'Use cPanel phpMyAdmin/mysqldump and verify off-site MariaDB backup and restore')
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':raise HTTPException(403,'Admin required')
        from production_database import postgres_enabled
        if postgres_enabled():raise HTTPException(409,'Use managed PostgreSQL backup jobs, not the SQLite export endpoint')
        from production_database import postgres_enabled
        if postgres_enabled():raise HTTPException(409,'Use managed PostgreSQL backup jobs, not the SQLite cloud snapshot endpoint')
        path=os.getenv('EVENTOS_DB_PATH',str(Path(__file__).resolve().parent/'eventos.sqlite3'))
        body=snapshot_bytes(path)
        sha=hashlib.sha256(body).hexdigest()
        with db() as con:
            audit(con,user['id'],'backup.export','platform',sha[:16])
            con.execute('INSERT INTO saas_backup_history VALUES(?,?,?,?,?)',
               (secrets.token_hex(16),utc(),'download',len(body),sha))
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        return Response(body,media_type='application/gzip',headers={
          'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
          'X-Backup-SHA256':sha,
          'Content-Disposition':f'attachment; filename="eventos-{stamp}.sqlite3.gz"'})

    @app.post('/api/platform/backups/cloud')
    def cloud_backup(request:Request):
        if os.getenv('EVENTOS_DATABASE_ENGINE')=='mariadb':
            raise HTTPException(409,'Use cPanel phpMyAdmin/mysqldump and verify off-site MariaDB backup and restore')
        import cloud_media
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':raise HTTPException(403,'Admin required')
        if not cloud_media.configured():
            raise HTTPException(503,'Private S3 cloud storage is not configured')
        path=os.getenv('EVENTOS_DB_PATH',str(Path(__file__).resolve().parent/'eventos.sqlite3'))
        body=snapshot_bytes(path)
        sha=hashlib.sha256(body).hexdigest()
        key='eventos-private-backups/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'/'+secrets.token_hex(14)+'.sqlite3.gz'
        try:
            cloud_media.client().put_object(Bucket=cloud_media.bucket(),Key=key,Body=body,
              ContentType='application/gzip',CacheControl='private, no-store',
              ServerSideEncryption='AES256',Metadata={'sha256':sha})
        except Exception as exc:
            raise HTTPException(503,'Private S3 backup failed') from exc
        with db() as con:
            audit(con,user['id'],'backup.cloud','platform',sha[:16])
            con.execute('INSERT INTO saas_backup_history VALUES(?,?,?,?,?)',
               (secrets.token_hex(16),utc(),'private_s3',len(body),sha))
        return {'ok':True,'size_bytes':len(body),'sha256':sha}

    @app.get('/api/platform/readiness')
    def readiness(request:Request):
        from phase7_auth import verify_required,mail_enabled
        import cloud_media
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':
                raise HTTPException(403,'Platform admin permission required')
        with db() as con:last=last_backup(con)
        db_location=os.getenv('EVENTOS_DB_PATH','eventos.sqlite3')
        # Connection settings do not prove persistence or successful recovery.
        from production_launch import production_flags
        flags=production_flags()
        return {'email_verification_required':verify_required(),
                'smtp_configured':mail_enabled(),
                'password_reset_available':mail_enabled() and os.getenv('EVENTOS_PUBLIC_BASE_URL','').startswith('https://'),
                'stripe_configured':stripe_ready(),
                'private_cloud_media_configured':cloud_media.configured(),
                'database_engine':flags['database_engine'],
                'database_persistence_verified':False,
                'backup_automation_configured':flags['offsite_backup_scheduled'],'last_manual_backup':last,
                'provider_webhooks_active_verified':False,
                'note':'A persistent managed database, backups, provider credentials and production audit remain launch requirements.'}

    @app.get('/api/platform/billing-audit')
    def audit_log(request:Request):
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':
                raise HTTPException(403,'Platform admin permission required')
            return [dict(row) for row in con.execute('''SELECT actor_user_id,action,target_type,target_id,created_at
              FROM saas_admin_audit ORDER BY id DESC LIMIT 150''')]

    @app.patch('/api/platform/workspaces/{wid}/subscription')
    def manual_grant(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'plan'} or not isinstance(payload['plan'],str):
            raise HTTPException(422,'Invalid plan')
        with db() as con:
            user=get_user(request,con)
            if user['platform_role']!='admin':
                raise HTTPException(403,'Platform admin permission required')
            linked=con.execute('SELECT 1 FROM saas_billing_accounts WHERE workspace_id=?',(wid,)).fetchone()
            if linked:
                raise HTTPException(409,'Provider-managed subscriptions cannot be overridden manually')
            activate_plan(con,wid,payload['plan'],'manual')
            audit(con,user['id'],'subscription.grant','workspace',wid)
            return {'ok':True,'plan':payload['plan']}

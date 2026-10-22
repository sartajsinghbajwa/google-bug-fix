"""Honestly Zen Monetization Center.

WHC/MariaDB-compatible catalog and manually verified commerce ledger.  No card
charges, refunds, Stripe subscriptions, automatic lead sales or transfers occur
in this module.  Existing subscription checkout/webhooks remain authoritative.
"""
from __future__ import annotations
import re, uuid, csv, io, html
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

now=lambda:datetime.now(timezone.utc).isoformat(timespec='seconds')
uid=lambda:uuid.uuid4().hex
KINDS=('sponsorship','lead_package','premium_event')
FEATURES=('premium_templates','premium_event_website','advanced_seating','guest_automation','premium_invitation')
ORDER_STATUSES=('requested','rejected','cancelled','active','expired')

# Compatible with SQLite and MariaDB without using TEXT in indexed keys.
TABLES={
 'monetization_offers':'''CREATE TABLE IF NOT EXISTS monetization_offers (
 id VARCHAR(64) PRIMARY KEY,kind VARCHAR(30) NOT NULL,title VARCHAR(130) NOT NULL,
 description TEXT NOT NULL,amount_cents BIGINT NOT NULL,currency VARCHAR(3) NOT NULL DEFAULT 'CAD',
 billing_period VARCHAR(20) NOT NULL DEFAULT 'one_time',credits INTEGER NOT NULL DEFAULT 0,
 duration_days INTEGER NOT NULL DEFAULT 30,feature_key VARCHAR(60) NOT NULL DEFAULT '',
 is_active INTEGER NOT NULL DEFAULT 1,created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL)''',
 'monetization_orders':'''CREATE TABLE IF NOT EXISTS monetization_orders (
 id VARCHAR(64) PRIMARY KEY,offer_id VARCHAR(64) NOT NULL,buyer_id VARCHAR(191) NOT NULL,
 listing_id VARCHAR(64) NOT NULL DEFAULT '',workspace_id VARCHAR(191) NOT NULL DEFAULT '',
 promo_code VARCHAR(50) NOT NULL DEFAULT '',original_cents BIGINT NOT NULL,
 discount_cents BIGINT NOT NULL DEFAULT 0,amount_cents BIGINT NOT NULL,
 currency VARCHAR(3) NOT NULL DEFAULT 'CAD',status VARCHAR(24) NOT NULL DEFAULT 'requested',
 credits_remaining INTEGER NOT NULL DEFAULT 0,starts_at VARCHAR(40) NOT NULL DEFAULT '',
 ends_at VARCHAR(40) NOT NULL DEFAULT '',payment_reference VARCHAR(180) NOT NULL DEFAULT '',
 verified_by VARCHAR(191) NOT NULL DEFAULT '',verified_at VARCHAR(40) NOT NULL DEFAULT '',
 created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL)''',
 'monetization_promotions':'''CREATE TABLE IF NOT EXISTS monetization_promotions (
 code VARCHAR(50) PRIMARY KEY,discount_type VARCHAR(15) NOT NULL,
 discount_value INTEGER NOT NULL,max_uses INTEGER NOT NULL DEFAULT 0,
 uses INTEGER NOT NULL DEFAULT 0,expires_at VARCHAR(40) NOT NULL DEFAULT '',
 is_active INTEGER NOT NULL DEFAULT 1,created_at VARCHAR(40) NOT NULL)''',
 'monetization_promo_redemptions':'''CREATE TABLE IF NOT EXISTS monetization_promo_redemptions (
 order_id VARCHAR(64) PRIMARY KEY,code VARCHAR(50) NOT NULL,
 redeemed_at VARCHAR(40) NOT NULL)''',
 'monetization_lead_deliveries':'''CREATE TABLE IF NOT EXISTS monetization_lead_deliveries (
 id VARCHAR(64) PRIMARY KEY,order_id VARCHAR(64) NOT NULL,
 quote_id VARCHAR(64) NOT NULL,created_at VARCHAR(40) NOT NULL,
 UNIQUE(order_id,quote_id))''',
 'monetization_commission_rules':'''CREATE TABLE IF NOT EXISTS monetization_commission_rules (
 id VARCHAR(64) PRIMARY KEY,title VARCHAR(120) NOT NULL,
 category VARCHAR(90) NOT NULL DEFAULT '*',rate_bps INTEGER NOT NULL DEFAULT 0,
 flat_cents BIGINT NOT NULL DEFAULT 0,is_active INTEGER NOT NULL DEFAULT 1,
 created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL)''',
 'monetization_commission_entries':'''CREATE TABLE IF NOT EXISTS monetization_commission_entries (
 id VARCHAR(64) PRIMARY KEY,quote_id VARCHAR(64) NOT NULL UNIQUE,
 listing_id VARCHAR(64) NOT NULL,rule_id VARCHAR(64) NOT NULL,
 amount_basis_cents BIGINT NOT NULL,fee_cents BIGINT NOT NULL,
 currency VARCHAR(3) NOT NULL DEFAULT 'CAD',status VARCHAR(24) NOT NULL DEFAULT 'accrued',
 payment_reference VARCHAR(180) NOT NULL DEFAULT '',recorded_by VARCHAR(191) NOT NULL,
 collected_at VARCHAR(40) NOT NULL DEFAULT '',created_at VARCHAR(40) NOT NULL)''',
 'monetization_invoices':'''CREATE TABLE IF NOT EXISTS monetization_invoices (
 id VARCHAR(64) PRIMARY KEY,number VARCHAR(60) NOT NULL UNIQUE,
 buyer_id VARCHAR(191) NOT NULL DEFAULT '',order_id VARCHAR(64) NOT NULL DEFAULT '',
 recipient VARCHAR(160) NOT NULL,description TEXT NOT NULL,amount_cents BIGINT NOT NULL,
 currency VARCHAR(3) NOT NULL DEFAULT 'CAD',status VARCHAR(20) NOT NULL DEFAULT 'draft',
 due_date VARCHAR(12) NOT NULL DEFAULT '',payment_reference VARCHAR(180) NOT NULL DEFAULT '',
 paid_at VARCHAR(40) NOT NULL DEFAULT '',created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL)''',
 'monetization_audit':'''CREATE TABLE IF NOT EXISTS monetization_audit (
 id VARCHAR(64) PRIMARY KEY,actor_id VARCHAR(191) NOT NULL,
 action VARCHAR(80) NOT NULL,entity_id VARCHAR(64) NOT NULL,
 details VARCHAR(300) NOT NULL DEFAULT '',created_at VARCHAR(40) NOT NULL)''',
}
INDEXES=(
 'CREATE INDEX IF NOT EXISTS idx_monet_orders_buyer ON monetization_orders(buyer_id,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_monet_orders_status ON monetization_orders(status,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_monet_orders_listing ON monetization_orders(listing_id,status)',
 'CREATE INDEX IF NOT EXISTS idx_monet_lead_order ON monetization_lead_deliveries(order_id)',
 'CREATE INDEX IF NOT EXISTS idx_monet_invoice_status ON monetization_invoices(status,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_monet_audit_date ON monetization_audit(created_at)',
)

def migrate(db):
 with db() as con:
  for ddl in TABLES.values():con.execute(ddl)
  for ddl in INDEXES:con.execute(ddl)

def ensure_admin(request,con,get_user):
 u=get_user(request,con)
 if u['platform_role']!='admin':raise HTTPException(403,'Platform administrator required')
 return u

def payload_keys(d,allowed,required=()):
 if not isinstance(d,dict) or set(d)-set(allowed) or not set(required).issubset(d):
  raise HTTPException(422,'Invalid fields')

def string(d,k,maximum=180,minimum=0):
 v=d.get(k,'')
 if not isinstance(v,str) or not minimum<=len(v.strip())<=maximum or '\x00' in v:raise HTTPException(422,'Invalid '+k)
 return v.strip()

def integer(d,k,minimum=0,maximum=100_000_000):
 v=d.get(k)
 if type(v)!=int or not minimum<=v<=maximum:raise HTTPException(422,'Invalid '+k)
 return v

def yn(d,k):
 if type(d.get(k))!=bool:raise HTTPException(422,'Invalid '+k)
 return int(d[k])

def existing(con,table,key):
 row=con.execute('SELECT * FROM '+table+' WHERE id=?',(key,)).fetchone()
 if not row:raise HTTPException(404,'Record not found')
 return dict(row)

def audit(con,actor,action,entity,details=''):
 con.execute('INSERT INTO monetization_audit VALUES(?,?,?,?,?,?)',(uid(),actor,action,entity,details[:300],now()))

def iso_date(s):
 if not s:return ''
 if not isinstance(s,str) or not re.fullmatch(r'20\d{2}-\d\d-\d\d',s):raise HTTPException(422,'Use YYYY-MM-DD')
 try:datetime.strptime(s,'%Y-%m-%d')
 except ValueError:raise HTTPException(422,'Invalid date')
 return s

def active_promotion(con,code,amount):
 if not code:return 0
 p=con.execute('SELECT * FROM monetization_promotions WHERE code=?',(code,)).fetchone()
 if not p or not p['is_active'] or (p['max_uses'] and p['uses']>=p['max_uses']) or (p['expires_at'] and p['expires_at']<now()):
  raise HTTPException(409,'Promotion is unavailable')
 disc=amount*p['discount_value']//100 if p['discount_type']=='percent' else p['discount_value']
 return min(amount,disc)

def order_details(con,order):
 out=dict(order);offer=con.execute('SELECT title,kind,feature_key FROM monetization_offers WHERE id=?',(out['offer_id'],)).fetchone()
 out['title']=offer['title'] if offer else 'Retired offer';out['kind']=offer['kind'] if offer else 'unknown'
 out['feature_key']=offer['feature_key'] if offer else ''
 return out

def report(con):
 rows=[dict(x) for x in con.execute('SELECT * FROM monetization_orders')]
 invoice_rows=[dict(x) for x in con.execute('SELECT * FROM monetization_invoices')]
 commissions=[dict(x) for x in con.execute('SELECT * FROM monetization_commission_entries')]
 paid_invoice_orders={x['order_id'] for x in invoice_rows if x['status']=='paid' and x['order_id']}
 by_currency={}
 def bucket(cur):return by_currency.setdefault(cur,{'verified_orders_cents':0,'paid_invoices_cents':0,'collected_commissions_cents':0,'recognized_total_cents':0,'outstanding_invoices_cents':0,'accrued_commissions_cents':0})
 for x in rows:
  if x['verified_at'] and x['status'] in ('active','expired') and x['id'] not in paid_invoice_orders:
   bucket(x['currency'])['verified_orders_cents']+=x['amount_cents']
 for x in invoice_rows:
  b=bucket(x['currency'])
  if x['status']=='paid':b['paid_invoices_cents']+=x['amount_cents']
  if x['status']=='issued':b['outstanding_invoices_cents']+=x['amount_cents']
 for x in commissions:
  b=bucket(x['currency'])
  if x['status']=='collected':b['collected_commissions_cents']+=x['fee_cents']
  if x['status']=='accrued':b['accrued_commissions_cents']+=x['fee_cents']
 for b in by_currency.values():b['recognized_total_cents']=b['verified_orders_cents']+b['paid_invoices_cents']+b['collected_commissions_cents']
 return {'by_currency':by_currency,'pending_orders':sum(x['status']=='requested' for x in rows),
         'active_sponsors':sum(x['status']=='active' and x['listing_id'] and x['starts_at']<=now() and (not x['ends_at'] or x['ends_at']>=now()) and x['verified_at'] for x in rows),
         'issued_invoices':sum(x['status']=='issued' for x in invoice_rows),
         'note':'Operational sales ledger, not accounting or tax reporting. Paid state is manually verified; Stripe subscriptions are maintained by the existing billing module.'}

def sponsored_listing_ids(con):
 today=now()
 return {r['listing_id'] for r in con.execute('''SELECT o.listing_id FROM monetization_orders o
   JOIN monetization_offers f ON f.id=o.offer_id
   JOIN market_listings l ON l.id=o.listing_id
   WHERE f.kind='sponsorship' AND o.status='active' AND o.verified_at<>''
    AND o.starts_at<=? AND o.ends_at>=? AND l.publication_status='published' ''',(today,today))}

def install(app,db,get_user,access,mariadb=False):
 if not mariadb:migrate(db)

 @app.get('/api/monetization/offers')
 def public_offers(request:Request):
  with db() as con:
   get_user(request,con)
   return [dict(r) for r in con.execute('SELECT * FROM monetization_offers WHERE is_active=1 ORDER BY kind,title')]

 @app.get('/api/monetization/mine')
 def mine(request:Request):
  with db() as con:
   u=get_user(request,con)
   return {'orders':[order_details(con,r) for r in con.execute('SELECT * FROM monetization_orders WHERE buyer_id=? ORDER BY created_at DESC LIMIT 200',(u['id'],))],
           'offers':[dict(r) for r in con.execute('SELECT * FROM monetization_offers WHERE is_active=1 ORDER BY kind,title')]}

 @app.post('/api/monetization/orders')
 def request_offer(request:Request,d:dict):
  payload_keys(d,('offer_id','listing_id','workspace_id','promo_code'),('offer_id',))
  with db() as con:
   u=get_user(request,con)
   offer=existing(con,'monetization_offers',string(d,'offer_id',64,1))
   if not offer['is_active']:raise HTTPException(409,'Offer not available')
   listing_id=string(d,'listing_id',64);workspace_id=string(d,'workspace_id',191)
   if offer['kind'] in ('sponsorship','lead_package'):
    if not listing_id or workspace_id:raise HTTPException(422,'Choose your own vendor listing')
    listing=con.execute('SELECT owner_id,publication_status FROM market_listings WHERE id=?',(listing_id,)).fetchone()
    if not listing or listing['owner_id']!=u['id']:raise HTTPException(403,'Your vendor listing is required')
    if offer['kind']=='sponsorship' and listing['publication_status']!='published':raise HTTPException(409,'Publish your listing before sponsoring')
   elif offer['kind']=='premium_event':
    if not workspace_id or listing_id:raise HTTPException(422,'Choose a workspace')
    role=access(request,con,workspace_id)
    if role['role']!='owner':raise HTTPException(403,'Workspace owner required')
   outstanding=con.execute('''SELECT COUNT(*) AS n FROM monetization_orders
      WHERE buyer_id=? AND offer_id=? AND listing_id=? AND workspace_id=? AND status='requested' ''',
      (u['id'],offer['id'],listing_id,workspace_id)).fetchone()
   if outstanding['n']:raise HTTPException(409,'You already have a pending request for this offer')
   code=string(d,'promo_code',50).upper()
   discount=active_promotion(con,code,offer['amount_cents'])
   ident=uid();t=now()
   con.execute('''INSERT INTO monetization_orders(id,offer_id,buyer_id,listing_id,workspace_id,promo_code,
     original_cents,discount_cents,amount_cents,currency,status,created_at,updated_at)
     VALUES(?,?,?,?,?,?,?,?,?,?,'requested',?,?)''',
     (ident,offer['id'],u['id'],listing_id,workspace_id,code,offer['amount_cents'],discount,offer['amount_cents']-discount,offer['currency'],t,t))
   audit(con,u['id'],'order.requested',ident,offer['kind'])
   return {'ok':True,'order_id':ident,'status':'requested','amount_cents':offer['amount_cents']-discount,'currency':offer['currency'],'payment_collected':False}

 @app.get('/api/platform/monetization')
 def overview(request:Request):
  with db() as con:
   ensure_admin(request,con,get_user)
   return {'offers':[dict(r) for r in con.execute('SELECT * FROM monetization_offers ORDER BY created_at DESC LIMIT 300')],
    'orders':[order_details(con,r) for r in con.execute('SELECT * FROM monetization_orders ORDER BY created_at DESC LIMIT 400')],
    'promotions':[dict(r) for r in con.execute('SELECT * FROM monetization_promotions ORDER BY created_at DESC LIMIT 200')],
    'rules':[dict(r) for r in con.execute('SELECT * FROM monetization_commission_rules ORDER BY created_at DESC LIMIT 100')],
    'commissions':[dict(r) for r in con.execute('SELECT * FROM monetization_commission_entries ORDER BY created_at DESC LIMIT 300')],
    'invoices':[dict(r) for r in con.execute('SELECT * FROM monetization_invoices ORDER BY created_at DESC LIMIT 300')],
    'leads':[dict(r) for r in con.execute('SELECT * FROM monetization_lead_deliveries ORDER BY created_at DESC LIMIT 250')],
    'audit':[dict(r) for r in con.execute('SELECT * FROM monetization_audit ORDER BY created_at DESC LIMIT 100')],
    'report':report(con),
    'packages':[dict(r) for r in con.execute('SELECT * FROM platform_packages ORDER BY id')],
    'premium_features':FEATURES}

 @app.post('/api/platform/monetization/offers')
 def create_offer(request:Request,d:dict):
  keys=('kind','title','description','amount_cents','currency','billing_period','credits','duration_days','feature_key','is_active')
  payload_keys(d,keys,('kind','title','amount_cents'))
  kind=string(d,'kind',30)
  if kind not in KINDS:raise HTTPException(422,'Unknown offer kind')
  title=string(d,'title',130,3);desc=string(d,'description',1500)
  amount=integer(d,'amount_cents',0,100_000_000)
  currency=string(d,'currency',3).upper() or 'CAD'
  if currency!='CAD':raise HTTPException(422,'Only CAD is supported by this offer catalog')
  period=string(d,'billing_period',20) or 'one_time'
  if period not in ('one_time','monthly','yearly'):raise HTTPException(422,'Invalid billing period')
  credits=d.get('credits',0);days=d.get('duration_days',30)
  if type(credits)!=int or not 0<=credits<=100000:raise HTTPException(422,'Invalid credits')
  if type(days)!=int or not 1<=days<=3650:raise HTTPException(422,'Invalid days')
  feature=string(d,'feature_key',60)
  if kind=='lead_package' and credits<1:raise HTTPException(422,'Lead package needs credits')
  if kind=='premium_event' and feature not in FEATURES:raise HTTPException(422,'Choose a premium feature')
  if kind!='premium_event' and feature:raise HTTPException(422,'Feature key is for premium events only')
  if kind!='lead_package' and credits:raise HTTPException(422,'Credits are for lead packages only')
  active=d.get('is_active',True)
  if type(active)!=bool:raise HTTPException(422,'Invalid active flag')
  t=now();ident=uid()
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   con.execute('''INSERT INTO monetization_offers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
     (ident,kind,title,desc,amount,currency,period,credits,days,feature,int(active),t,t))
   audit(con,admin['id'],'offer.created',ident,kind)
  return {'ok':True,'id':ident}

 @app.patch('/api/platform/monetization/offers/{oid}')
 def offer_toggle(request:Request,oid:str,d:dict):
  payload_keys(d,('is_active',),('is_active',));active=yn(d,'is_active')
  with db() as con:
   admin=ensure_admin(request,con,get_user);existing(con,'monetization_offers',oid)
   con.execute('UPDATE monetization_offers SET is_active=?,updated_at=? WHERE id=?',(active,now(),oid))
   audit(con,admin['id'],'offer.updated',oid,str(active))
  return {'ok':True}

 @app.post('/api/platform/monetization/promotions')
 def add_promotion(request:Request,d:dict):
  payload_keys(d,('code','discount_type','discount_value','max_uses','expires_at'),('code','discount_type','discount_value'))
  code=string(d,'code',50,3).upper()
  if not re.fullmatch(r'[A-Z0-9_-]{3,50}',code):raise HTTPException(422,'Invalid code')
  typ=string(d,'discount_type',15)
  if typ not in ('percent','fixed_cents'):raise HTTPException(422,'Unknown discount type')
  val=integer(d,'discount_value',1,10000000)
  if typ=='percent' and val>100:raise HTTPException(422,'Percent cannot exceed 100')
  max_uses=d.get('max_uses',0)
  if type(max_uses)!=int or not 0<=max_uses<=100000:raise HTTPException(422,'Invalid max uses')
  expires=iso_date(d.get('expires_at',''))
  if expires:expires+='T23:59:59+00:00'
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   if con.execute('SELECT 1 FROM monetization_promotions WHERE code=?',(code,)).fetchone():raise HTTPException(409,'Promotion exists')
   con.execute('INSERT INTO monetization_promotions VALUES(?,?,?,?,?,?,?,?)',(code,typ,val,max_uses,0,expires,1,now()))
   audit(con,admin['id'],'promotion.created',code)
  return {'ok':True}

 @app.patch('/api/platform/monetization/promotions/{code}')
 def promo_toggle(request:Request,code:str,d:dict):
  payload_keys(d,('is_active',),('is_active',));active=yn(d,'is_active')
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   if not con.execute('SELECT 1 FROM monetization_promotions WHERE code=?',(code,)).fetchone():raise HTTPException(404,'Promotion not found')
   con.execute('UPDATE monetization_promotions SET is_active=? WHERE code=?',(active,code))
   audit(con,admin['id'],'promotion.updated',code,str(active))
  return {'ok':True}

 @app.post('/api/platform/monetization/orders/{oid}/decision')
 def decide_order(request:Request,oid:str,d:dict):
  payload_keys(d,('decision','payment_reference'),('decision',))
  decision=string(d,'decision',15)
  if decision not in ('activate','reject','cancel'):raise HTTPException(422,'Invalid decision')
  ref=string(d,'payment_reference',180)
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   order=existing(con,'monetization_orders',oid)
   if order['status']!='requested':raise HTTPException(409,'Only requested orders may be reviewed')
   if decision=='activate':
    if not ref:raise HTTPException(422,'Provide verified external payment reference')
    if order['promo_code']:
     # Atomic cap in MariaDB/SQLite: prevent concurrent over-redemption.
     p=con.execute('''UPDATE monetization_promotions SET uses=uses+1 WHERE code=? AND is_active=1
       AND (max_uses=0 OR uses<max_uses) AND (expires_at='' OR expires_at>=?)''',(order['promo_code'],now()))
     if p.rowcount!=1:raise HTTPException(409,'Promotion redemption limit reached or expired')
     con.execute('INSERT INTO monetization_promo_redemptions VALUES(?,?,?)',(oid,order['promo_code'],now()))
    offer=existing(con,'monetization_offers',order['offer_id'])
    if offer['kind']=='sponsorship':
     listing=con.execute('SELECT publication_status,owner_id FROM market_listings WHERE id=?',(order['listing_id'],)).fetchone()
     if not listing or listing['publication_status']!='published' or listing['owner_id']!=order['buyer_id']:
      raise HTTPException(409,'Vendor listing must be published and owned by buyer')
    if offer['kind']=='premium_event':
     owner=con.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=? AND role='owner'",(order['workspace_id'],order['buyer_id'])).fetchone()
     if not owner:raise HTTPException(409,'Workspace ownership has changed')
    from datetime import timedelta
    start=now();end=(datetime.now(timezone.utc)+timedelta(days=offer['duration_days'])).isoformat(timespec='seconds')
    con.execute('''UPDATE monetization_orders SET status='active',verified_by=?,verified_at=?,payment_reference=?,
       credits_remaining=?,starts_at=?,ends_at=?,updated_at=? WHERE id=? AND status='requested' ''',
       (admin['id'],start,ref,offer['credits'] if offer['kind']=='lead_package' else 0,start,end,start,oid))
   else:
    con.execute('UPDATE monetization_orders SET status=?,updated_at=? WHERE id=?',( 'rejected' if decision=='reject' else 'cancelled',now(),oid))
   audit(con,admin['id'],'order.'+decision,oid,ref[-80:])
  return {'ok':True}

 @app.post('/api/platform/monetization/leads/deliver')
 def deliver_lead(request:Request,d:dict):
  payload_keys(d,('order_id','quote_id'),('order_id','quote_id'))
  oid=string(d,'order_id',64,1);qid=string(d,'quote_id',64,1)
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   order=existing(con,'monetization_orders',oid)
   offer=existing(con,'monetization_offers',order['offer_id'])
   quote=con.execute('''SELECT q.id,l.owner_id FROM market_quotes q
     JOIN market_listings l ON l.id=q.listing_id WHERE q.id=? AND q.listing_id=?''',(qid,order['listing_id'])).fetchone()
   if offer['kind']!='lead_package' or order['status']!='active' or not order['verified_at'] or order['ends_at']<now():
    raise HTTPException(409,'Active paid lead package required')
   if not quote or quote['owner_id']!=order['buyer_id']:raise HTTPException(404,'Vendor quote not found')
   if con.execute('SELECT 1 FROM monetization_lead_deliveries WHERE order_id=? AND quote_id=?',(oid,qid)).fetchone():raise HTTPException(409,'Lead already credited')
   changed=con.execute('UPDATE monetization_orders SET credits_remaining=credits_remaining-1 WHERE id=? AND credits_remaining>0',(oid,))
   if changed.rowcount!=1:raise HTTPException(409,'No lead credits remaining')
   con.execute('INSERT INTO monetization_lead_deliveries VALUES(?,?,?,?)',(uid(),oid,qid,now()))
   audit(con,admin['id'],'lead.delivered',oid,qid)
  return {'ok':True}

 @app.post('/api/platform/monetization/commission-rules')
 def rule(request:Request,d:dict):
  payload_keys(d,('title','category','rate_bps','flat_cents'),('title','rate_bps','flat_cents'))
  title=string(d,'title',120,3);category=string(d,'category',90) or '*'
  bps=integer(d,'rate_bps',0,10000);flat=integer(d,'flat_cents',0,10000000)
  if not (bps or flat):raise HTTPException(422,'Specify a commission rate or flat fee')
  with db() as con:
   admin=ensure_admin(request,con,get_user);ident=uid();t=now()
   con.execute('INSERT INTO monetization_commission_rules VALUES(?,?,?,?,?,?,?,?)',(ident,title,category,bps,flat,1,t,t))
   audit(con,admin['id'],'commission_rule.created',ident)
  return {'ok':True}

 @app.post('/api/platform/monetization/commissions')
 def record_commission(request:Request,d:dict):
  payload_keys(d,('quote_id','rule_id'),('quote_id','rule_id'))
  qid=string(d,'quote_id',64,1);rid=string(d,'rule_id',64,1)
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   rule=existing(con,'monetization_commission_rules',rid)
   if not rule['is_active']:raise HTTPException(409,'Commission rule inactive')
   q=con.execute('''SELECT q.*,l.category FROM market_quotes q JOIN market_listings l ON l.id=q.listing_id WHERE q.id=?''',(qid,)).fetchone()
   if not q or q['status']!='completed':raise HTTPException(409,'Only completed, signed quote bookings qualify')
   if rule['category'] not in ('*',q['category']):raise HTTPException(422,'Rule category does not match')
   if con.execute('SELECT 1 FROM monetization_commission_entries WHERE quote_id=?',(qid,)).fetchone():raise HTTPException(409,'Commission already recorded for this booking')
   # Use verified signed contract amount, not an untrusted browser amount.
   contract=con.execute("SELECT total_amount,status FROM market_contracts WHERE quote_id=?",(qid,)).fetchone()
   if not contract or contract['status']!='signed':raise HTTPException(409,'Signed contract required')
   basis=int(round(float(contract['total_amount'])*100))
   if basis<=0:raise HTTPException(422,'Signed amount must be positive')
   fee=(basis*rule['rate_bps']//10000)+rule['flat_cents']
   if fee>basis:raise HTTPException(422,'Commission exceeds booking total')
   ident=uid();con.execute('''INSERT INTO monetization_commission_entries
     (id,quote_id,listing_id,rule_id,amount_basis_cents,fee_cents,recorded_by,created_at)
     VALUES(?,?,?,?,?,?,?,?)''',(ident,qid,q['listing_id'],rid,basis,fee,admin['id'],now()))
   audit(con,admin['id'],'commission.accrued',ident,qid)
  return {'ok':True,'id':ident,'fee_cents':fee,'collected':False}

 @app.post('/api/platform/monetization/commissions/{cid}/collect')
 def collect_commission(request:Request,cid:str,d:dict):
  payload_keys(d,('payment_reference',),('payment_reference',));ref=string(d,'payment_reference',180,3)
  with db() as con:
   admin=ensure_admin(request,con,get_user);entry=existing(con,'monetization_commission_entries',cid)
   if entry['status']!='accrued':raise HTTPException(409,'Commission is not awaiting collection')
   con.execute("UPDATE monetization_commission_entries SET status='collected',payment_reference=?,collected_at=? WHERE id=?",(ref,now(),cid))
   audit(con,admin['id'],'commission.collected',cid,ref[-80:])
  return {'ok':True}

 @app.post('/api/platform/monetization/invoices')
 def new_invoice(request:Request,d:dict):
  payload_keys(d,('recipient','description','amount_cents','due_date','buyer_id','order_id'),('recipient','description','amount_cents'))
  recipient=string(d,'recipient',160,2);desc=string(d,'description',1500,2)
  amount=integer(d,'amount_cents',1,100_000_000);due=iso_date(d.get('due_date',''))
  buyer=string(d,'buyer_id',191);oid=string(d,'order_id',64)
  with db() as con:
   admin=ensure_admin(request,con,get_user)
   if oid:
    order=existing(con,'monetization_orders',oid)
    if order['verified_at']:raise HTTPException(409,'Order already marked payment-verified; cannot invoice it again')
    if con.execute('SELECT 1 FROM monetization_invoices WHERE order_id=?',(oid,)).fetchone():raise HTTPException(409,'Order already has an invoice')
    if amount!=order['amount_cents']:raise HTTPException(422,'Invoice must match order total')
    buyer=order['buyer_id']
   if buyer and not con.execute('SELECT 1 FROM users WHERE id=?',(buyer,)).fetchone():raise HTTPException(404,'Buyer not found')
   ident=uid();t=now();number='HZ-'+datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+ident[:8].upper()
   con.execute('''INSERT INTO monetization_invoices
     (id,number,buyer_id,order_id,recipient,description,amount_cents,created_at,updated_at,due_date)
     VALUES(?,?,?,?,?,?,?,?,?,?)''',(ident,number,buyer,oid,recipient,desc,amount,t,t,due))
   audit(con,admin['id'],'invoice.created',ident,number)
  return {'ok':True,'id':ident,'number':number,'status':'draft'}

 @app.post('/api/platform/monetization/invoices/{iid}/status')
 def invoice_status(request:Request,iid:str,d:dict):
  payload_keys(d,('status','payment_reference'),('status',))
  target=string(d,'status',20)
  if target not in ('issued','void','paid'):raise HTTPException(422,'Invalid invoice status')
  ref=string(d,'payment_reference',180)
  with db() as con:
   admin=ensure_admin(request,con,get_user);invoice=existing(con,'monetization_invoices',iid)
   options={'draft':('issued','void'),'issued':('paid','void')}
   if target not in options.get(invoice['status'],()):raise HTTPException(409,'Invalid invoice transition')
   if target=='paid' and not ref:raise HTTPException(422,'Verified payment reference required')
   con.execute('''UPDATE monetization_invoices SET status=?,payment_reference=?,paid_at=?,updated_at=? WHERE id=?''',
    (target,ref,now() if target=='paid' else '',now(),iid))
   audit(con,admin['id'],'invoice.'+target,iid,ref[-80:])
  return {'ok':True}

 @app.get('/api/platform/monetization/report.csv')
 def csv_report(request:Request):
  with db() as con:
   ensure_admin(request,con,get_user)
   report_data=report(con)
  out=io.StringIO();writer=csv.writer(out)
  writer.writerow(['Currency','Verified order sales cents','Paid invoices cents',
   'Collected commissions cents','Recognized total cents','Outstanding invoices cents',
   'Accrued commissions cents'])
  for currency,row in sorted(report_data['by_currency'].items()):
   writer.writerow([currency]+[row[k] for k in ('verified_orders_cents','paid_invoices_cents',
    'collected_commissions_cents','recognized_total_cents','outstanding_invoices_cents','accrued_commissions_cents')])
  return Response(out.getvalue(),media_type='text/csv',headers={
   'Content-Disposition':'attachment; filename="honestlyzen-monetization-summary.csv"',
   'Cache-Control':'private, no-store'})

 @app.get('/api/platform/monetization/invoices/{iid}/print')
 def print_invoice(request:Request,iid:str):
  with db() as con:
   ensure_admin(request,con,get_user)
   inv=existing(con,'monetization_invoices',iid)
  esc=lambda s:html.escape(str(s or ''),quote=True)
  price=f"{int(inv['amount_cents'])/100:,.2f} {inv['currency']}"
  html_page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Honestly Zen Invoice</title><style>body{font:16px system-ui,-apple-system,sans-serif;color:#182b25;max-width:760px;margin:50px auto;padding:24px}header{display:flex;justify-content:space-between;border-bottom:2px solid #315946;padding-bottom:24px}h1{font-size:30px;margin:0}dl{display:grid;grid-template-columns:1fr 2fr;gap:12px}dt{color:#63776b}dd{margin:0}article{margin:36px 0;border:1px solid #dee9e2;border-radius:12px;padding:20px}.amount{font-size:32px;font-weight:800}.note{font-size:12px;color:#60776b}button{padding:10px 18px;background:#315946;color:white;border:0;border-radius:8px}@media print{button{display:none}body{margin:0}}</style></head><body><header><div><h1>Honestly Zen Event Planning</h1><p>Manual invoice copy · verify with the issuer</p></div><div><strong>INVOICE</strong><p>'''+esc(inv['number'])+'''</p></div></header><article><dl><dt>Bill to</dt><dd>'''+esc(inv['recipient'])+'''</dd><dt>Description</dt><dd>'''+esc(inv['description'])+'''</dd><dt>Date</dt><dd>'''+esc(inv['created_at'][:10])+'''</dd><dt>Due</dt><dd>'''+esc(inv['due_date'] or 'Not specified')+'''</dd><dt>Status</dt><dd>'''+esc(inv['status']).upper()+'''</dd></dl></article><div>Amount</div><div class="amount">'''+esc(price)+'''</div><p class="note">This record does not establish tax compliance or successful collection. Payment state reflects an administrator's recorded verification; not an automatic card charge. No GST/HST calculation is included.</p><button onclick="window.print()">Print or save as PDF</button></body></html>'''
  return HTMLResponse(html_page,headers={'Cache-Control':'private, no-store',
    'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})

 @app.get('/api/w/{wid}/monetization/features')
 def my_features(request:Request,wid:str):
  with db() as con:
   access(request,con,wid)
   t=now();rows=con.execute('''SELECT f.feature_key,o.ends_at FROM monetization_orders o
     JOIN monetization_offers f ON f.id=o.offer_id WHERE o.workspace_id=? AND
     f.kind='premium_event' AND o.status='active' AND o.verified_at<>'' AND
     o.starts_at<=? AND o.ends_at>=?''',(wid,t,t)).fetchall()
   return {'features':{f:any(r['feature_key']==f for r in rows) for f in FEATURES},
     'valid_until':{r['feature_key']:r['ends_at'] for r in rows},
     'notice':'Premium entitlement registry; modules must use this server-side API to gate premium-only actions.'}

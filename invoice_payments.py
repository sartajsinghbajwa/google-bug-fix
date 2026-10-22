"""Stripe Connect direct-charge Checkout for PIN-authenticated agency invoices.

Opt-in only: requires connected agency account, signed Connect events, HTTPS,
no credit on browser redirects, per-invoice reservation and duplicate protection.
Payouts, refunds, disputes and tax compliance require operational processes.
"""
from __future__ import annotations
import os,re,uuid
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException,Request
from agency_portal import get_doc,doc_view,record_audit,now
from agency_advanced import client_session,MAX_CENTS


def configured():
 return (os.getenv('EVENTOS_AGENCY_LIVE_INVOICES')=='1' and
         os.getenv('EVENTOS_CONNECT_ENABLED')=='1' and
         bool(os.getenv('EVENTOS_CONNECT_SECRET_KEY','').startswith(('sk_live_','sk_test_'))) and
         os.getenv('EVENTOS_AGENCY_INVOICE_WEBHOOK_SECRET','').startswith('whsec_') and
         os.getenv('EVENTOS_PUBLIC_BASE_URL','').startswith('https://'))


def account(con,wid):
 row=con.execute('''SELECT stripe_account_id FROM agency_connect_accounts WHERE workspace_id=?
    AND details_submitted=1 AND charges_enabled=1 AND payouts_enabled=1''',(wid,)).fetchone()
 return row['stripe_account_id'] if row else None


def payable(con,wid):
 return configured() and bool(account(con,wid))


def init(db):
 with db() as con:
  con.executescript('''
   CREATE TABLE IF NOT EXISTS agency_invoice_checkouts(
     id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL,
     client_id TEXT NOT NULL,document_id TEXT NOT NULL,
     stripe_account_id TEXT NOT NULL,stripe_session_id TEXT NOT NULL UNIQUE,
     amount_cents INTEGER NOT NULL,currency TEXT NOT NULL,
     status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,
     completed_at TEXT NOT NULL DEFAULT '',payment_id TEXT NOT NULL DEFAULT '');
   CREATE INDEX IF NOT EXISTS idx_agency_invoice_checkouts_doc
     ON agency_invoice_checkouts(document_id,status);
   CREATE TABLE IF NOT EXISTS agency_invoice_webhook_events(
     id TEXT PRIMARY KEY,received_at TEXT NOT NULL);
  ''')


def install(app,db):
 init(db)

 @app.post('/api/client/{token}/documents/{doc_id}/checkout')
 def create_checkout(request:Request,token:str,doc_id:str):
  if not configured():raise HTTPException(503,'Online invoice payments are not enabled')
  with db() as con:
   portal,event=client_session(request,con,token)
   wid,eid=portal['workspace_id'],portal['event_id']
   destination=account(con,wid)
   if not destination:raise HTTPException(503,'Agency payment account not yet authorized')
   doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']!='invoice' or doc['status']!='issued':raise HTTPException(409,'Only issued invoices can be paid')
   view=doc_view(con,doc)
   balance=view['balance_cents']
   if type(balance) is not int or not 50<=balance<=MAX_CENTS:
    raise HTTPException(409,'Invoice has no payable balance or is below the provider minimum')
   recent=(datetime.now(timezone.utc)-timedelta(minutes=35)).isoformat(timespec='seconds')
   pending=con.execute("SELECT 1 FROM agency_invoice_checkouts WHERE document_id=? AND status='pending' AND created_at>=?",(doc_id,recent)).fetchone()
   if pending:raise HTTPException(409,'A checkout is already in progress for this invoice')
   checkout_id=str(uuid.uuid4());stage='reserved_'+checkout_id
   con.execute('''INSERT INTO agency_invoice_checkouts
    (id,workspace_id,event_id,client_id,document_id,stripe_account_id,stripe_session_id,
     amount_cents,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)''',
    (checkout_id,wid,eid,portal['id'],doc_id,destination,stage,balance,doc['subtotal_currency'].lower(),now()))
  import stripe
  stripe.api_key=os.environ['EVENTOS_CONNECT_SECRET_KEY']
  base=os.environ['EVENTOS_PUBLIC_BASE_URL'].rstrip('/')
  try:
   result=stripe.checkout.Session.create(
     mode='payment',payment_method_types=['card'],
     line_items=[{'price_data':{'currency':doc['subtotal_currency'].lower(),
       'product_data':{'name':(doc['number']+' · '+doc['title'])[:110]},
       'unit_amount':balance},'quantity':1}],
     client_reference_id=portal['id'],
     metadata={'invoice_id':doc_id,'workspace_id':wid,'event_id':eid,'checkout_id':checkout_id},
     success_url=base+'/client/'+token+'?payment=returned',
     cancel_url=base+'/client/'+token+'?payment=cancelled',
     expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=30)).timestamp()),
     stripe_account=destination,idempotency_key='eventos-invoice-'+checkout_id)
  except Exception as exc:
   with db() as con:con.execute("UPDATE agency_invoice_checkouts SET status='failed' WHERE id=? AND status='pending'",(checkout_id,))
   raise HTTPException(502,'Unable to create secure invoice checkout') from exc
  session_id=getattr(result,'id','');url=getattr(result,'url','')
  if not isinstance(session_id,str) or not re.fullmatch(r'cs_(?:test|live)_[A-Za-z0-9]+',session_id) or not isinstance(url,str) or not url.startswith('https://checkout.stripe.com/'):
   raise HTTPException(502,'Unrecognized Stripe checkout response')
  with db() as con:
   con.execute('UPDATE agency_invoice_checkouts SET stripe_session_id=? WHERE id=? AND status=\'pending\'',(session_id,checkout_id))
   record_audit(con,wid,eid,'client','invoice.checkout_started',checkout_id)
  return {'url':url,'amount_cents':balance,'currency':doc['subtotal_currency'],'test_mode':session_id.startswith('cs_test_')}

 @app.post('/api/agency/invoice-stripe-webhook')
 async def webhook(request:Request):
  if not configured():raise HTTPException(503,'Invoice payments are not enabled')
  raw=await request.body()
  if len(raw)>512000:raise HTTPException(413,'Webhook exceeds size limit')
  import stripe
  stripe.api_key=os.environ['EVENTOS_CONNECT_SECRET_KEY']
  try:
   event=stripe.Webhook.construct_event(raw,request.headers.get('stripe-signature',''),os.environ['EVENTOS_AGENCY_INVOICE_WEBHOOK_SECRET'])
  except Exception:raise HTTPException(400,'Invalid invoice webhook signature')
  if event.get('type') not in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
   return {'ok':True,'ignored':True}
  obj=event.get('data',{}).get('object',{})
  if obj.get('mode')!='payment' or obj.get('payment_status')!='paid':return {'ok':True,'unpaid':True}
  eid=event.get('id','');sid=obj.get('id','');acct=event.get('account','')
  if not isinstance(eid,str) or not eid.startswith('evt_') or not isinstance(sid,str) or not sid.startswith('cs_') or not isinstance(acct,str) or not acct.startswith('acct_'):
   raise HTTPException(422,'Malformed Stripe Connect event')
  with db() as con:
   con.execute('BEGIN IMMEDIATE')
   if con.execute('SELECT 1 FROM agency_invoice_webhook_events WHERE id=?',(eid,)).fetchone():return {'ok':True,'duplicate':True}
   row=con.execute('SELECT * FROM agency_invoice_checkouts WHERE stripe_session_id=? AND stripe_account_id=?',(sid,acct)).fetchone()
   if not row:return {'ok':True,'ignored':True}
   con.execute('INSERT INTO agency_invoice_webhook_events(id,received_at) VALUES(?,?)',(eid,now()))
   if row['status']!='pending':return {'ok':True,'duplicate':True}
   metadata=obj.get('metadata') or {}
   validated=(obj.get('amount_total')==row['amount_cents'] and
       str(obj.get('currency','')).lower()==row['currency'] and
       obj.get('client_reference_id')==row['client_id'] and
       metadata.get('checkout_id')==row['id'] and
       metadata.get('invoice_id')==row['document_id'] and
       metadata.get('workspace_id')==row['workspace_id'] and
       metadata.get('event_id')==row['event_id'])
   doc=get_doc(con,row['workspace_id'],row['event_id'],row['document_id'])
   balance=doc_view(con,doc)['balance_cents']
   if not validated or doc['status']!='issued' or row['amount_cents']>balance:
    con.execute("UPDATE agency_invoice_checkouts SET status='needs_review',completed_at=? WHERE id=?",(now(),row['id']))
    record_audit(con,row['workspace_id'],row['event_id'],'stripe','invoice.payment_needs_review',row['id'])
    return {'ok':True,'review':True}
   payment_id=str(uuid.uuid4());stamp=now()
   con.execute('''INSERT INTO agency_payments
     (id,workspace_id,event_id,document_id,amount_cents,method,note,paid_on,created_at,staff_id)
     VALUES(?,?,?,?,?,'stripe_connect',?,?,?,'stripe_webhook')''',
     (payment_id,row['workspace_id'],row['event_id'],row['document_id'],row['amount_cents'],
       'Verified Stripe Connect invoice payment',stamp[:10],stamp))
   con.execute("UPDATE agency_invoice_checkouts SET status='paid',payment_id=?,completed_at=? WHERE id=?",(payment_id,stamp,row['id']))
   record_audit(con,row['workspace_id'],row['event_id'],'stripe','invoice.payment_captured',payment_id)
   from email_notifications import workspace_members
   workspace_members(con,row['workspace_id'],'billing','EventOS invoice payment recorded',
       'A verified Stripe invoice payment was recorded for your workspace. Review the invoice in EventOS.',
       '/app',dedupe_prefix='invoice-payment:'+eid)
  return {'ok':True}

"""Phase 12 agency delivery: milestones, vendor allocations, actual expenses,
profitability reporting, signature evidence and optional Stripe TEST checkout.

No live money movement is enabled. Test Checkout is bound to ONE approved
workspace via EVENTOS_AGENCY_TEST_WORKSPACE_ID + an sk_test_ key. Payment credit
occurs ONLY on a verified and idempotent Stripe webhook, never on redirect.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from agency_portal import get_doc, doc_view, record_audit, text, iso_date, now, COOKIE, token_digest

CATEGORIES=('Venue','Catering','Decor','Florals','Photography','Entertainment','Transport','Staff','Technology','Other')
MILESTONE_STATES=('planned','in_progress','completed','blocked')
VENDOR_STATES=('shortlisted','booked','in_progress','complete','cancelled')
EXPENSE_STATES=('planned','incurred','paid')
MAX_ROWS=250
MAX_CENTS=1000000000

def uid():return str(uuid.uuid4())

def amount(v,maximum=MAX_CENTS):
 if type(v) is not int or not 0<=v<=maximum:raise HTTPException(422,'Invalid amount in cents')
 return v

def choice(v,options,label):
 if not isinstance(v,str) or v not in options:raise HTTPException(422,'Invalid '+label)
 return v

def date_opt(v,label):
 return iso_date(v,label)

def plain(v,max_len,label,required=False):
 return text(v,max_len,label,required)

def safer_csv(v):
 value=str(v if v is not None else '')
 return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value

def stripe_test_enabled(wid):
 return bool(os.getenv('EVENTOS_AGENCY_TEST_WORKSPACE_ID')==wid and
   os.getenv('STRIPE_SECRET_KEY','').startswith('sk_test_') and
   os.getenv('EVENTOS_AGENCY_STRIPE_WEBHOOK_SECRET','').startswith('whsec_') and
   os.getenv('EVENTOS_PUBLIC_BASE_URL','').startswith('https://'))

def metrics(con,wid,eid):
 invoices=con.execute("""SELECT id,total_cents,status FROM agency_documents
   WHERE workspace_id=? AND event_id=? AND kind='invoice'""",(wid,eid)).fetchall()
 billed=sum(x['total_cents'] for x in invoices if x['status']=='issued')
 received=con.execute("""SELECT COALESCE(SUM(p.amount_cents),0) FROM agency_payments p
   JOIN agency_documents d ON d.id=p.document_id
   WHERE d.workspace_id=? AND d.event_id=? AND d.kind='invoice' AND d.status='issued'""",(wid,eid)).fetchone()[0]
 recorded=con.execute("""SELECT COALESCE(SUM(amount_cents),0) FROM agency_expenses
   WHERE workspace_id=? AND event_id=? AND status IN ('incurred','paid')""",(wid,eid)).fetchone()[0]
 cash_paid=con.execute("""SELECT COALESCE(SUM(amount_cents),0) FROM agency_expenses
   WHERE workspace_id=? AND event_id=? AND status='paid'""",(wid,eid)).fetchone()[0]
 planned=con.execute("""SELECT COALESCE(SUM(amount_cents),0) FROM agency_expenses
   WHERE workspace_id=? AND event_id=? AND status='planned'""",(wid,eid)).fetchone()[0]
 committed=con.execute("""SELECT COALESCE(SUM(committed_cents),0) FROM agency_vendor_allocations
   WHERE workspace_id=? AND event_id=? AND status!='cancelled'""",(wid,eid)).fetchone()[0]
 # Profit is based on invoiced revenue, NOT forecast leads, unsigned proposals
 # or funds still awaiting collection.
 profit=billed-recorded
 return {'invoiced_cents':billed,'received_cents':received,
         'receivable_cents':max(0,billed-received),
         'incurred_expenses_cents':recorded,'paid_expenses_cents':cash_paid,
         'planned_expenses_cents':planned,'vendor_committed_cents':committed,
         'invoiced_margin_cents':profit,
         'invoiced_margin_pct':round(100*profit/billed,1) if billed else None,
         'cash_position_cents':received-cash_paid}

def client_session(request,con,token):
 # Reuse the exact Phase 9 PIN-protected portal cookie and session storage.
 if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{24,120}',token):
  raise HTTPException(404,'Client portal unavailable')
 portal=con.execute("""SELECT * FROM agency_clients WHERE access_token=?
    AND active=1 AND expires_at>?""",(token,now())).fetchone()
 if not portal:raise HTTPException(404,'Client portal unavailable')
 event=con.execute('SELECT id,name FROM events WHERE id=? AND workspace_id=?',
                   (portal['event_id'],portal['workspace_id'])).fetchone()
 if not event:raise HTTPException(404,'Client portal unavailable')
 raw=request.cookies.get(COOKIE,'')
 if not raw:raise HTTPException(401,'Enter your client portal PIN')
 session=con.execute("""SELECT 1 FROM agency_sessions
   WHERE session_hash=? AND client_id=? AND expires_at>?""",
   (token_digest(raw),portal['id'],now())).fetchone()
 if not session:raise HTTPException(401,'Client session expired')
 return portal,event

def migrate(db):
 with db() as con:
  con.executescript("""
   CREATE TABLE IF NOT EXISTS agency_milestones(
     id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
     event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
     title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',
     due_date TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planned',
     client_visible INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
     updated_at TEXT NOT NULL);
   CREATE INDEX IF NOT EXISTS idx_agency_milestones ON agency_milestones(workspace_id,event_id,due_date);
   CREATE TABLE IF NOT EXISTS agency_vendor_allocations(
     id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,
     event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
     vendor_id TEXT NOT NULL REFERENCES vendors(id),
     scope TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'shortlisted',
     committed_cents INTEGER NOT NULL DEFAULT 0,
     due_date TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
     UNIQUE(event_id,vendor_id));
   CREATE INDEX IF NOT EXISTS idx_agency_allocations ON agency_vendor_allocations(workspace_id,event_id);
   CREATE TABLE IF NOT EXISTS agency_expenses(
     id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,
     event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
     vendor_id TEXT REFERENCES vendors(id),
     title TEXT NOT NULL,category TEXT NOT NULL,amount_cents INTEGER NOT NULL,
     incurred_on TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planned',
     note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
   CREATE INDEX IF NOT EXISTS idx_agency_expenses ON agency_expenses(workspace_id,event_id,incurred_on);
   CREATE TABLE IF NOT EXISTS agency_test_checkouts(
     id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL,
     client_id TEXT NOT NULL REFERENCES agency_clients(id) ON DELETE CASCADE,
     document_id TEXT NOT NULL REFERENCES agency_documents(id),
     stripe_session_id TEXT NOT NULL UNIQUE,amount_cents INTEGER NOT NULL,
     currency TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
     created_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',
     payment_id TEXT NOT NULL DEFAULT '');
   CREATE INDEX IF NOT EXISTS idx_agency_checkout_doc ON agency_test_checkouts(document_id,status);
  """)

def install(app,db,access,check_event,get_user):
 migrate(db)
 def gate(request,con,wid,eid,write=False):
  role=access(request,con,wid,write)
  event=check_event(con,wid,eid)
  return role,event

 def vendor_in(con,wid,vendor_id):
  if vendor_id is None or vendor_id=='':return None
  if not isinstance(vendor_id,str):raise HTTPException(422,'Invalid vendor')
  row=con.execute('SELECT id FROM vendors WHERE id=? AND workspace_id=?',(vendor_id,wid)).fetchone()
  if not row:raise HTTPException(422,'Vendor must belong to this organization')
  return vendor_id

 def list_rows(con,wid,eid):
  milestones=[dict(r) for r in con.execute("""SELECT id,title,details,due_date,status,client_visible,created_at
   FROM agency_milestones WHERE workspace_id=? AND event_id=?
   ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'planned' THEN 2 ELSE 3 END,due_date,id""",(wid,eid))]
  allocations=[dict(r) for r in con.execute("""SELECT a.id,a.vendor_id,v.name vendor_name,v.category vendor_category,
     a.scope,a.status,a.committed_cents,a.due_date FROM agency_vendor_allocations a
     JOIN vendors v ON v.id=a.vendor_id AND v.workspace_id=a.workspace_id
     WHERE a.workspace_id=? AND a.event_id=? ORDER BY v.name COLLATE NOCASE""",(wid,eid))]
  expenses=[dict(r) for r in con.execute("""SELECT x.id,x.vendor_id,v.name vendor_name,x.title,x.category,
     x.amount_cents,x.incurred_on,x.status,x.note
     FROM agency_expenses x LEFT JOIN vendors v ON v.id=x.vendor_id AND v.workspace_id=x.workspace_id
     WHERE x.workspace_id=? AND x.event_id=? ORDER BY x.created_at DESC,x.id DESC""",(wid,eid))]
  return milestones,allocations,expenses

 @app.get('/api/w/{wid}/events/{eid}/agency/advanced')
 def overview(request:Request,wid:str,eid:str):
  with db() as con:
   role,event=gate(request,con,wid,eid)
   milestones,allocations,expenses=list_rows(con,wid,eid)
   vendors=[dict(r) for r in con.execute("""SELECT id,name,category FROM vendors
      WHERE workspace_id=? ORDER BY name COLLATE NOCASE LIMIT 500""",(wid,))]
   currency=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()['currency']
   pending=con.execute("""SELECT id,document_id,amount_cents,status,created_at FROM agency_test_checkouts
       WHERE workspace_id=? AND event_id=? AND status='needs_review' ORDER BY created_at DESC""",(wid,eid)).fetchall()
   return {'event_name':event['name'],'currency':currency,'can_edit':role['role'] in ('owner','editor'),
     'milestones':milestones,'vendor_assignments':allocations,'workspace_vendors':vendors,
     'expenses':expenses,'metrics':metrics(con,wid,eid),
     'stripe_test_enabled':stripe_test_enabled(wid),
     'payments_needing_review':[dict(x) for x in pending]}

 @app.post('/api/w/{wid}/events/{eid}/agency/advanced/milestones')
 def create_milestone(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'title','details','due_date','status','client_visible'}:
   raise HTTPException(422,'Provide milestone details')
  title=plain(payload['title'],150,'milestone title',True)
  details=plain(payload['details'],1000,'milestone details')
  due=date_opt(payload['due_date'],'milestone')
  status=choice(payload['status'],MILESTONE_STATES,'milestone status')
  vis=payload['client_visible']
  if type(vis) is not bool:raise HTTPException(422,'Invalid client visibility')
  with db() as con:
   gate(request,con,wid,eid,True)
   if con.execute('SELECT COUNT(*) FROM agency_milestones WHERE event_id=?',(eid,)).fetchone()[0]>=MAX_ROWS:
    raise HTTPException(409,'Maximum project milestones reached')
   ident=uid();ts=now()
   con.execute("""INSERT INTO agency_milestones VALUES(?,?,?,?,?,?,?,?,?,?)""",
      (ident,wid,eid,title,details,due,status,int(vis),ts,ts))
   record_audit(con,wid,eid,get_user(request,con)['id'],'milestone.created',ident)
   return {'id':ident,'title':title,'details':details,'due_date':due,'status':status,'client_visible':int(vis)}

 @app.patch('/api/w/{wid}/events/{eid}/agency/advanced/milestones/{ident}')
 def update_milestone(request:Request,wid:str,eid:str,ident:str,payload:dict):
  fields={'status':MILESTONE_STATES,'client_visible':None}
  if not isinstance(payload,dict) or not payload or set(payload)-set(fields):
   raise HTTPException(422,'Update milestone status or visibility')
  if 'status' in payload:choice(payload['status'],MILESTONE_STATES,'milestone status')
  if 'client_visible' in payload and type(payload['client_visible']) is not bool:
   raise HTTPException(422,'Invalid client visibility')
  with db() as con:
   gate(request,con,wid,eid,True)
   old=con.execute('SELECT * FROM agency_milestones WHERE id=? AND workspace_id=? AND event_id=?',
                   (ident,wid,eid)).fetchone()
   if not old:raise HTTPException(404,'Milestone not found')
   status=payload.get('status',old['status'])
   visible=int(payload.get('client_visible',bool(old['client_visible'])))
   con.execute("""UPDATE agency_milestones SET status=?,client_visible=?,updated_at=?
     WHERE id=? AND workspace_id=? AND event_id=?""",(status,visible,now(),ident,wid,eid))
   record_audit(con,wid,eid,get_user(request,con)['id'],'milestone.updated',ident)
   return {'ok':True,'status':status,'client_visible':visible}

 @app.delete('/api/w/{wid}/events/{eid}/agency/advanced/milestones/{ident}')
 def delete_milestone(request:Request,wid:str,eid:str,ident:str):
  with db() as con:
   gate(request,con,wid,eid,True)
   n=con.execute('DELETE FROM agency_milestones WHERE id=? AND workspace_id=? AND event_id=?',
                 (ident,wid,eid)).rowcount
   if not n:raise HTTPException(404,'Milestone not found')
   record_audit(con,wid,eid,get_user(request,con)['id'],'milestone.deleted',ident)
   return {'ok':True}

 @app.post('/api/w/{wid}/events/{eid}/agency/advanced/vendors')
 def assign_vendor(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'vendor_id','scope','status','committed_cents','due_date'}:
   raise HTTPException(422,'Enter vendor assignment details')
  scope=plain(payload['scope'],650,'vendor scope')
  status=choice(payload['status'],VENDOR_STATES,'vendor status')
  committed=amount(payload['committed_cents'])
  due=date_opt(payload['due_date'],'vendor due')
  with db() as con:
   gate(request,con,wid,eid,True)
   vid=vendor_in(con,wid,payload['vendor_id'])
   if not vid:raise HTTPException(422,'Select a vendor')
   ident=uid();ts=now()
   # Upsert assignment; only one record per vendor per event.
   old=con.execute('SELECT id FROM agency_vendor_allocations WHERE vendor_id=? AND event_id=?',(vid,eid)).fetchone()
   if old:ident=old['id']
   con.execute("""INSERT INTO agency_vendor_allocations VALUES(?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(event_id,vendor_id) DO UPDATE SET scope=excluded.scope,status=excluded.status,
      committed_cents=excluded.committed_cents,due_date=excluded.due_date,updated_at=excluded.updated_at""",
     (ident,wid,eid,vid,scope,status,committed,due,ts,ts))
   record_audit(con,wid,eid,get_user(request,con)['id'],'vendor.assignment_saved',ident)
   return {'id':ident,'vendor_id':vid,'scope':scope,'status':status,'committed_cents':committed,'due_date':due}

 @app.delete('/api/w/{wid}/events/{eid}/agency/advanced/vendors/{ident}')
 def remove_vendor(request:Request,wid:str,eid:str,ident:str):
  with db() as con:
   gate(request,con,wid,eid,True)
   n=con.execute('DELETE FROM agency_vendor_allocations WHERE id=? AND workspace_id=? AND event_id=?',
                 (ident,wid,eid)).rowcount
   if not n:raise HTTPException(404,'Vendor assignment not found')
   record_audit(con,wid,eid,get_user(request,con)['id'],'vendor.assignment_removed',ident)
   return {'ok':True}

 @app.post('/api/w/{wid}/events/{eid}/agency/advanced/expenses')
 def record_expense(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'title','category','amount_cents','vendor_id','incurred_on','status','note'}:
   raise HTTPException(422,'Enter expense details')
  title=plain(payload['title'],180,'expense title',True)
  cat=choice(payload['category'],CATEGORIES,'expense category')
  cents=amount(payload['amount_cents'])
  if cents<1:raise HTTPException(422,'Expense must be positive')
  status=choice(payload['status'],EXPENSE_STATES,'expense status')
  incurred=date_opt(payload['incurred_on'],'expense')
  note=plain(payload['note'],800,'expense note')
  with db() as con:
   gate(request,con,wid,eid,True)
   vid=vendor_in(con,wid,payload['vendor_id'])
   if con.execute('SELECT COUNT(*) FROM agency_expenses WHERE workspace_id=? AND event_id=?',(wid,eid)).fetchone()[0]>=MAX_ROWS:
    raise HTTPException(409,'Expense limit reached')
   ident=uid();ts=now()
   con.execute("""INSERT INTO agency_expenses VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
     (ident,wid,eid,vid,title,cat,cents,incurred,status,note,ts,ts))
   record_audit(con,wid,eid,get_user(request,con)['id'],'expense.recorded',ident)
   return {'id':ident,'title':title,'category':cat,'amount_cents':cents,'status':status}

 @app.patch('/api/w/{wid}/events/{eid}/agency/advanced/expenses/{ident}')
 def update_expense(request:Request,wid:str,eid:str,ident:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'status'}:
   raise HTTPException(422,'Change expense status only')
  status=choice(payload['status'],EXPENSE_STATES,'expense status')
  with db() as con:
   gate(request,con,wid,eid,True)
   n=con.execute("""UPDATE agency_expenses SET status=?,updated_at=?
      WHERE id=? AND workspace_id=? AND event_id=?""",(status,now(),ident,wid,eid)).rowcount
   if not n:raise HTTPException(404,'Expense not found')
   record_audit(con,wid,eid,get_user(request,con)['id'],'expense.status_changed',ident)
   return {'ok':True,'status':status}

 @app.delete('/api/w/{wid}/events/{eid}/agency/advanced/expenses/{ident}')
 def delete_expense(request:Request,wid:str,eid:str,ident:str):
  with db() as con:
   gate(request,con,wid,eid,True)
   n=con.execute('DELETE FROM agency_expenses WHERE id=? AND workspace_id=? AND event_id=?',
     (ident,wid,eid)).rowcount
   if not n:raise HTTPException(404,'Expense not found')
   record_audit(con,wid,eid,get_user(request,con)['id'],'expense.deleted',ident)
   return {'ok':True}

 @app.get('/api/w/{wid}/events/{eid}/agency/advanced/report.csv')
 def report_csv(request:Request,wid:str,eid:str):
  with db() as con:
   gate(request,con,wid,eid)
   m=metrics(con,wid,eid);milestones,vendors,expenses=list_rows(con,wid,eid)
   currency=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()['currency']
  out=io.StringIO()
  w=csv.writer(out)
  w.writerow(['EventOS Agency Financial Report','Currency',currency])
  w.writerow(['Metric','Amount (minor units)'])
  for k,v in m.items():w.writerow([k,v if v is not None else ''])
  w.writerow([])
  w.writerow(['Expense','Category','Vendor','Amount cents','Status','Date'])
  for x in expenses:w.writerow([safer_csv(x['title']),safer_csv(x['category']),
       safer_csv(x['vendor_name'] or ''),x['amount_cents'],x['status'],x['incurred_on']])
  w.writerow([])
  w.writerow(['Vendor','Scope','Committed cents','Status','Due'])
  for x in vendors:w.writerow([safer_csv(x['vendor_name']),safer_csv(x['scope']),
      x['committed_cents'],x['status'],x['due_date']])
  return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={
    'Content-Disposition':'attachment; filename="eventos-agency-report.csv"',
    'Cache-Control':'private, no-store'})

 @app.get('/api/w/{wid}/agency/advanced/report.csv')
 def workspace_report_csv(request:Request,wid:str):
  with db() as con:
   access(request,con,wid)
   events=con.execute('SELECT id,name FROM events WHERE workspace_id=? ORDER BY name',(wid,)).fetchall()
   rows=[(e['name'],metrics(con,wid,e['id'])) for e in events]
   currency=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()['currency']
  out=io.StringIO();w=csv.writer(out);w.writerow(['Event','Currency','Invoiced cents','Collected cents','Expenses incurred cents','Margin cents','Outstanding cents'])
  for name,m in rows:w.writerow([safer_csv(name),currency,m['invoiced_cents'],m['received_cents'],
                    m['incurred_expenses_cents'],m['invoiced_margin_cents'],m['receivable_cents']])
  return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={
   'Content-Disposition':'attachment; filename="eventos-agency-portfolio-report.csv"',
   'Cache-Control':'private, no-store'})

 @app.get('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/acknowledgment.pdf')
 def evidence_pdf(request:Request,wid:str,eid:str,doc_id:str):
  from reportlab.pdfgen import canvas
  from reportlab.lib.pagesizes import A4
  from reportlab.lib.colors import HexColor
  with db() as con:
   _,event=gate(request,con,wid,eid)
   doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']!='contract' or doc['status']!='signed':
    raise HTTPException(409,'Only acknowledged agreements have signature evidence')
   copy=dict(doc)
  W,H=A4;out=io.BytesIO();pdf=canvas.Canvas(out,pagesize=A4)
  pdf.setTitle('Agreement acknowledgment evidence - '+copy['number'])
  pdf.setFillColor(HexColor('#234e41'));pdf.rect(0,H-135,W,135,fill=1,stroke=0)
  pdf.setFillColor(HexColor('#ffffff'));pdf.setFont('Helvetica-Bold',20)
  pdf.drawString(42,H-73,'AGREEMENT ACKNOWLEDGMENT')
  pdf.setFont('Helvetica',11);pdf.drawString(42,H-101,'EventOS · Audit evidence record')
  y=H-172
  fields=[('Event',event['name']),('Agreement',copy['number']+' - '+copy['title']),
     ('Typed signer name',copy['signer_name']),('Issued (UTC)',copy['issued_at']),
     ('Acknowledged (UTC)',copy['responded_at']),
     ('Document fingerprint (SHA-256)',copy['signature_hash']),
     ('Network address fingerprint',copy['accepted_ip_hash'])]
  for label,value in fields:
   pdf.setFillColor(HexColor('#6a8176'));pdf.setFont('Helvetica',10);pdf.drawString(42,y,label)
   pdf.setFillColor(HexColor('#2a493b'));pdf.setFont('Helvetica',10)
   val=str(value).encode('latin-1','replace').decode('latin-1')
   if len(val)>85:val=val[:84]+'...'
   pdf.drawString(42,y-16,val);y-=55
  pdf.setFillColor(HexColor('#8b6c39'))
  pdf.setFont('Helvetica-Bold',11);pdf.drawString(42,y-22,'Important verification limitation')
  pdf.setFillColor(HexColor('#4a5a52'));pdf.setFont('Helvetica',10)
  for i,line in enumerate([
     'This is an EventOS client-portal acknowledgment record.',
     'A typed name and an explicit consent checkbox were required.',
     'Identity verification and qualified e-signature certification were NOT performed.',
     'Consult counsel before relying on this as a legally sufficient signed agreement.']):
   pdf.drawString(42,y-45-i*18,line)
  pdf.save()
  return Response(out.getvalue(),media_type='application/pdf',headers={
    'Content-Disposition':'attachment; filename="eventos-agreement-evidence.pdf"',
    'Cache-Control':'private, no-store'})

 # Stripe TEST-mode invoice payment checkout. Not multi-agency Stripe Connect!
 @app.post('/api/client/{token}/documents/{doc_id}/test-checkout')
 def client_test_checkout(request:Request,token:str,doc_id:str):
  import stripe
  with db() as con:
   portal,event=client_session(request,con,token)
   wid,eid=portal['workspace_id'],portal['event_id']
   if not stripe_test_enabled(wid):
    raise HTTPException(503,'Online test payments are not configured for this agency')
   doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']!='invoice' or doc['status']!='issued':
    raise HTTPException(409,'Only issued invoices may be paid')
   view=doc_view(con,doc)
   amount_due=view['balance_cents']
   if not amount_due:raise HTTPException(409,'This invoice has no outstanding balance')
   if amount_due>MAX_CENTS:raise HTTPException(422,'Unsupported invoice amount')
   cutoff=(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat(timespec='seconds')
   pending=con.execute("""SELECT 1 FROM agency_test_checkouts
      WHERE document_id=? AND status='pending' AND created_at>=? LIMIT 1""",(doc_id,cutoff)).fetchone()
   if pending:raise HTTPException(409,'A test checkout is already pending for this invoice')
   currency=doc['subtotal_currency'].lower()
   origin=os.getenv('EVENTOS_PUBLIC_BASE_URL','').rstrip('/')
   # Public service URL and checkout are created by the server, never client input.
   stripe.api_key=os.environ['STRIPE_SECRET_KEY']
   try:
    checkout=stripe.checkout.Session.create(
      mode='payment',payment_method_types=['card'],
      line_items=[{'price_data':{'currency':currency,'product_data':{
       'name':doc['number']+' · '+doc['title'][:110]},
       'unit_amount':amount_due},'quantity':1}],
      client_reference_id=portal['id'],
      metadata={'invoice_id':doc_id,'workspace_id':wid,'event_id':eid},
      success_url=origin+'/client/'+token+'?checkout=test-return',
      cancel_url=origin+'/client/'+token+'?checkout=cancelled',
      expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=31)).timestamp()))
   except Exception as exc:
    raise HTTPException(503,'Stripe test checkout is temporarily unavailable') from exc
   if not isinstance(getattr(checkout,'id',None),str) or not checkout.id.startswith('cs_test_') or not isinstance(getattr(checkout,'url',None),str) or not checkout.url.startswith('https://checkout.stripe.com/'):
    raise HTTPException(503,'Stripe returned an invalid test checkout session')
   identifier=uid()
   con.execute("""INSERT INTO agency_test_checkouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
     (identifier,wid,eid,portal['id'],doc_id,checkout.id,amount_due,currency,
      'pending',now(),'',''))
   record_audit(con,wid,eid,'client','checkout.test_started',identifier)
   return {'url':checkout.url,'test_mode':True,'amount_cents':amount_due}

 @app.post('/api/agency/test-stripe-webhook')
 async def test_checkout_webhook(request:Request):
  import stripe
  secret=os.getenv('EVENTOS_AGENCY_STRIPE_WEBHOOK_SECRET','')
  if not secret.startswith('whsec_') or not os.getenv('STRIPE_SECRET_KEY','').startswith('sk_test_'):
   raise HTTPException(503,'Stripe test webhook is not configured')
  raw=await request.body()
  if len(raw)>250000:raise HTTPException(413,'Payload too large')
  try:
   event=stripe.Webhook.construct_event(raw,request.headers.get('stripe-signature',''),secret)
  except Exception:
   raise HTTPException(400,'Invalid Stripe webhook signature')
  if event.get('type') not in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
   return {'ok':True,'ignored':True}
  obj=event['data']['object']
  if obj.get('payment_status')!='paid' or obj.get('mode')!='payment':
   return {'ok':True,'ignored':True}
  session=obj.get('id')
  if not isinstance(session,str):raise HTTPException(400,'Missing session ID')
  with db() as con:
   con.execute('BEGIN IMMEDIATE')
   row=con.execute('SELECT * FROM agency_test_checkouts WHERE stripe_session_id=?',(session,)).fetchone()
   if not row:return {'ok':True,'ignored':True}
   if not stripe_test_enabled(row['workspace_id']):return {'ok':True,'ignored':True}
   if row['status']!='pending':return {'ok':True,'duplicate':True}
   expected=int(row['amount_cents'])
   if (obj.get('amount_total')!=expected or str(obj.get('currency','')).lower()!=row['currency'] or
       obj.get('client_reference_id')!=row['client_id'] or
       obj.get('metadata',{}).get('invoice_id')!=row['document_id'] or
       obj.get('metadata',{}).get('workspace_id')!=row['workspace_id'] or
       obj.get('metadata',{}).get('event_id')!=row['event_id']):
    con.execute("UPDATE agency_test_checkouts SET status='needs_review',completed_at=? WHERE id=?",(now(),row['id']))
    record_audit(con,row['workspace_id'],row['event_id'],'stripe','checkout.mismatch_review',row['id'])
    return {'ok':True,'review':True}
   doc=get_doc(con,row['workspace_id'],row['event_id'],row['document_id'])
   balance=doc_view(con,doc)['balance_cents']
   if doc['status']!='issued' or expected>balance:
    con.execute("UPDATE agency_test_checkouts SET status='needs_review',completed_at=? WHERE id=?",(now(),row['id']))
    record_audit(con,row['workspace_id'],row['event_id'],'stripe','checkout.overpayment_review',row['id'])
    return {'ok':True,'review':True}
   payment_id=uid();stamp=now()
   con.execute("""INSERT INTO agency_payments(
        id,workspace_id,event_id,document_id,amount_cents,method,note,paid_on,created_at,staff_id)
        VALUES(?,?,?,?,?,'stripe_test',?,?,?,'stripe_webhook')""",
      (payment_id,row['workspace_id'],row['event_id'],row['document_id'],expected,
       'Stripe test-mode payment',stamp[:10],stamp))
   con.execute("""UPDATE agency_test_checkouts
       SET status='paid',completed_at=?,payment_id=? WHERE id=?""",(stamp,payment_id,row['id']))
   record_audit(con,row['workspace_id'],row['event_id'],'stripe','checkout.test_paid',payment_id)
  return {'ok':True}

 @app.get('/api/client/{token}/project')
 def client_milestones(request:Request,token:str):
  with db() as con:
   p,e=client_session(request,con,token)
   milestones=[dict(x) for x in con.execute("""SELECT title,details,due_date,status
      FROM agency_milestones WHERE workspace_id=? AND event_id=? AND client_visible=1
      ORDER BY due_date,id LIMIT 200""",(p['workspace_id'],p['event_id']))]
   from invoice_payments import payable
   return {'milestones':milestones,'stripe_test_enabled':stripe_test_enabled(p['workspace_id']),'stripe_invoice_enabled':payable(con,p['workspace_id'])}

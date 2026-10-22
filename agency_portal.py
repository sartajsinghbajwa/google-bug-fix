"""Phase 9: agency client access, proposals, agreements and invoices.

Client access is PIN-gated, event-scoped, and uses a separate HttpOnly cookie
session. Proposals, contracts, and invoices are version-locked once sent.
Invoice payments are offline/manual records, not card processing.
"""
from __future__ import annotations
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse

from event_operations import mail_enabled, send_email
from branding_studio import public_brand

COOKIE='eventos_client_session'
ROOT=Path(__file__).resolve().parent
KINDS=('proposal','contract','invoice')
DOC_STATES={'proposal':('draft','sent','accepted','declined'),
            'contract':('draft','sent','signed'),
            'invoice':('draft','issued','void')}
CURRENCIES=('CAD','USD','GBP','EUR','AUD','NZD')
METHODS=('cash','bank transfer','cheque','e-transfer','other')
EMAIL=re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
DATE=re.compile(r'^\d{4}-\d{2}-\d{2}$')


def now():
 return datetime.now(timezone.utc).isoformat(timespec='seconds')

def plus_days(days):
 return (datetime.now(timezone.utc)+timedelta(days=days)).isoformat(timespec='seconds')

def uid():
 return str(uuid.uuid4())

def token_digest(raw):
 return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def hash_pin(pin,salt):
 return hashlib.scrypt(pin.encode('utf-8'),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()

def text(value,limit,name,required=False):
 if not isinstance(value,str) or len(value)>limit:
  raise HTTPException(422,'Invalid '+name)
 result=value.strip()
 if required and not result:raise HTTPException(422,name+' is required')
 return result

def email(value):
 address=text(value,255,'client email').lower()
 if address and not EMAIL.fullmatch(address):raise HTTPException(422,'Invalid client email')
 return address

def iso_date(value,label):
 if value is None or value=='':return ''
 if not isinstance(value,str) or not DATE.fullmatch(value):
  raise HTTPException(422,'Invalid '+label+' date')
 try:date.fromisoformat(value)
 except ValueError:raise HTTPException(422,'Invalid '+label+' date')
 return value

def clean_lines(raw,allow_empty):
 if not isinstance(raw,list) or len(raw)>40 or (not allow_empty and not raw):
  raise HTTPException(422,'Provide 1–40 line items')
 result=[]
 for item in raw:
  if not isinstance(item,dict) or set(item)!={'description','quantity','unit_price_cents'}:
   raise HTTPException(422,'Invalid line item')
  label=text(item['description'],180,'line description',True)
  qty=item['quantity'];unit=item['unit_price_cents']
  if type(qty) is not int or not 1<=qty<=10000 or type(unit) is not int or not 0<=unit<=100000000:
   raise HTTPException(422,'Invalid line item price or quantity')
  result.append({'description':label,'quantity':qty,'unit_price_cents':unit})
 return result

def clean_document(data,kind):
 if not isinstance(data,dict) or set(data)!={'title','body','terms','lines','discount_cents','tax_bps','due_date','valid_until'}:
  raise HTTPException(422,'Invalid document form')
 title=text(data['title'],180,'title',True)
 body=text(data['body'],6000,'description')
 terms=text(data['terms'],10000,'terms')
 lines=clean_lines(data['lines'],kind=='contract')
 discount=data['discount_cents'];tax=data['tax_bps']
 if type(discount) is not int or discount<0 or discount>100000000:
  raise HTTPException(422,'Invalid discount')
 if type(tax) is not int or not 0<=tax<=3500:
  raise HTTPException(422,'Invalid tax percentage')
 if kind=='contract' and (not body or not terms):
  raise HTTPException(422,'Contracts require agreement text and terms')
 due=iso_date(data['due_date'],'due');valid=iso_date(data['valid_until'],'expiration')
 subtotal=sum(x['quantity']*x['unit_price_cents'] for x in lines)
 if subtotal>1000000000 or discount>subtotal:
  raise HTTPException(422,'Invalid line total or discount')
 taxable=subtotal-discount
 tax_cents=(taxable*tax+5000)//10000
 total=taxable+tax_cents
 return {'title':title,'body':body,'terms':terms,'lines_json':json.dumps(lines,sort_keys=True,separators=(',',':')),
         'discount_cents':discount,'tax_bps':tax,'due_date':due,'valid_until':valid,'total_cents':total}

def doc_view(con,row):
 d=dict(row);d['lines']=json.loads(d.pop('lines_json'))
 d['payments']= [dict(x) for x in con.execute("""SELECT id,amount_cents,method,note,paid_on,created_at
     FROM agency_payments WHERE document_id=? ORDER BY created_at,id""",(d['id'],))]
 d['paid_cents']=sum(p['amount_cents'] for p in d['payments'])
 d['balance_cents']=max(0,d['total_cents']-d['paid_cents'])
 d['display_status']=('paid' if d['kind']=='invoice' and d['status']=='issued' and d['balance_cents']==0 else
                       'partially_paid' if d['kind']=='invoice' and d['status']=='issued' and d['paid_cents'] else d['status'])
 d.pop('accepted_ip_hash',None)
 return d

def list_docs(con,eid,wid):
 rows=con.execute("SELECT * FROM agency_documents WHERE event_id=? AND workspace_id=? ORDER BY created_at DESC,id DESC",(eid,wid))
 return [doc_view(con,x) for x in rows]

def get_doc(con,wid,eid,doc_id):
 row=con.execute("SELECT * FROM agency_documents WHERE id=? AND event_id=? AND workspace_id=?",(doc_id,eid,wid)).fetchone()
 if not row:raise HTTPException(404,'Document not found')
 return row

def record_audit(con,wid,eid,actor,action,ref_id):
 con.execute("INSERT INTO agency_activity(id,workspace_id,event_id,actor,action,reference_id,created_at) VALUES(?,?,?,?,?,?,?)",
             (uid(),wid,eid,actor,action,ref_id,now()))

def migrate(db):
 with db() as con:
  con.executescript("""
  CREATE TABLE IF NOT EXISTS agency_profiles(
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    legal_name TEXT NOT NULL DEFAULT '',
    billing_address TEXT NOT NULL DEFAULT '',
    billing_email TEXT NOT NULL DEFAULT '',
    billing_phone TEXT NOT NULL DEFAULT '',
    tax_number TEXT NOT NULL DEFAULT '',
    payment_instructions TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS agency_sequences(
    workspace_id TEXT NOT NULL,kind TEXT NOT NULL,number INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(workspace_id,kind));
  CREATE TABLE IF NOT EXISTS agency_clients(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    client_name TEXT NOT NULL,client_email TEXT NOT NULL DEFAULT '',access_token TEXT NOT NULL UNIQUE,
    pin_salt TEXT NOT NULL,pin_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS agency_sessions(
    session_hash TEXT PRIMARY KEY,client_id TEXT NOT NULL REFERENCES agency_clients(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS agency_login_limits(
    client_id TEXT NOT NULL,remote_hash TEXT NOT NULL,window_started TEXT NOT NULL,failures INTEGER NOT NULL,
    PRIMARY KEY(client_id,remote_hash));
  CREATE TABLE IF NOT EXISTS agency_documents(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('proposal','contract','invoice')),number TEXT NOT NULL,
    title TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',terms TEXT NOT NULL DEFAULT '',
    lines_json TEXT NOT NULL DEFAULT '[]',subtotal_currency TEXT NOT NULL DEFAULT 'CAD',
    discount_cents INTEGER NOT NULL DEFAULT 0,tax_bps INTEGER NOT NULL DEFAULT 0,
    total_cents INTEGER NOT NULL DEFAULT 0,due_date TEXT NOT NULL DEFAULT '',
    valid_until TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'draft',
    issued_at TEXT NOT NULL DEFAULT '',responded_at TEXT NOT NULL DEFAULT '',
    signer_name TEXT NOT NULL DEFAULT '',signature_hash TEXT NOT NULL DEFAULT '',
    accepted_ip_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
    UNIQUE(event_id,number));
  CREATE INDEX IF NOT EXISTS idx_agency_docs_event ON agency_documents(event_id,kind);
  CREATE TABLE IF NOT EXISTS agency_payments(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES agency_documents(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL CHECK(amount_cents>0),method TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',paid_on TEXT NOT NULL,
    created_at TEXT NOT NULL,staff_id TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_agency_payments_document ON agency_payments(document_id);
  CREATE TABLE IF NOT EXISTS agency_activity(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,event_id TEXT NOT NULL,
    actor TEXT NOT NULL,action TEXT NOT NULL,reference_id TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_agency_audit_event ON agency_activity(event_id,created_at);
  """)

def install_agency(app,db,access,check_event,get_user):
 migrate(db)

 def staff(request,con,wid,eid,write=False):
  role=access(request,con,wid,write)
  ev=check_event(con,wid,eid)
  return role,ev

 def require_portal(con,wid,eid):
  row=con.execute("SELECT * FROM agency_clients WHERE workspace_id=? AND event_id=?",(wid,eid)).fetchone()
  if not row:raise HTTPException(404,'Client portal not created')
  return row

 def client_link(portal):
  return '/client/'+portal['access_token']

 def portal_summary(row):
  return {'client_name':row['client_name'],'client_email':row['client_email'],
          'active':bool(row['active']),'expires_at':row['expires_at'],
          'url':client_link(row),'created_at':row['created_at']}

 def get_client_by_link(con,token):
  if not re.fullmatch(r'[A-Za-z0-9_-]{24,120}',token or ''):
   raise HTTPException(404,'Client portal unavailable')
  row=con.execute('SELECT * FROM agency_clients WHERE access_token=?',(token,)).fetchone()
  if not row or not row['active'] or row['expires_at']<=now():
   raise HTTPException(404,'Client portal unavailable')
  e=con.execute('SELECT id,name,type,start_date,location FROM events WHERE id=? AND workspace_id=?',
                (row['event_id'],row['workspace_id'])).fetchone()
  if not e:raise HTTPException(404,'Client portal unavailable')
  return row,e

 def client_session(request,con,token):
  portal,e=get_client_by_link(con,token)
  raw=request.cookies.get(COOKIE,'')
  if not raw:raise HTTPException(401,'Enter your client portal PIN')
  session=con.execute("SELECT * FROM agency_sessions WHERE session_hash=? AND client_id=? AND expires_at>?",
                      (token_digest(raw),portal['id'],now())).fetchone()
  if not session:raise HTTPException(401,'Client portal session expired')
  return portal,e

 def set_cookie(response,raw):
  secure=os.getenv('EVENTOS_SECURE_COOKIES','0')=='1'
  response.set_cookie(COOKIE,raw,httponly=True,secure=secure,samesite='lax',path='/api/client/',
                      max_age=86400)

 def company_profile(con,wid):
  row=con.execute('SELECT * FROM agency_profiles WHERE workspace_id=?',(wid,)).fetchone()
  if row:
   d=dict(row);d.pop('updated_at',None);d.pop('workspace_id',None)
   return d
  w=con.execute('SELECT name FROM workspaces WHERE id=?',(wid,)).fetchone()
  return {'legal_name':w['name'] if w else '',
          'billing_address':'','billing_email':'','billing_phone':'',
          'tax_number':'','payment_instructions':''}

 @app.get('/api/w/{wid}/agency/profile')
 def agency_profile_get(request:Request,wid:str):
  with db() as con:
   access(request,con,wid)
   return company_profile(con,wid)

 @app.put('/api/w/{wid}/agency/profile')
 def agency_profile_save(request:Request,wid:str,payload:dict):
  caps={'legal_name':180,'billing_address':800,'billing_email':255,'billing_phone':80,
        'tax_number':110,'payment_instructions':1500}
  if not isinstance(payload,dict) or set(payload)!=set(caps):
   raise HTTPException(422,'Enter your agency billing details')
  d={k:text(payload[k],cap,k) for k,cap in caps.items()}
  if d['billing_email']:d['billing_email']=email(d['billing_email'])
  with db() as con:
   access(request,con,wid,True)
   con.execute("""INSERT INTO agency_profiles(workspace_id,legal_name,billing_address,billing_email,
     billing_phone,tax_number,payment_instructions,updated_at)
     VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
     legal_name=excluded.legal_name,billing_address=excluded.billing_address,
     billing_email=excluded.billing_email,billing_phone=excluded.billing_phone,
     tax_number=excluded.tax_number,payment_instructions=excluded.payment_instructions,
     updated_at=excluded.updated_at""",
     (wid,d['legal_name'],d['billing_address'],d['billing_email'],d['billing_phone'],
      d['tax_number'],d['payment_instructions'],now()))
   record_audit(con,wid,'',get_user(request,con)['id'],'profile.updated',wid)
  return d

 @app.get('/api/w/{wid}/agency')
 def workspace_agency(request:Request,wid:str):
  with db() as con:
   access(request,con,wid)
   e=con.execute("""SELECT e.id,e.name,e.type,e.start_date,e.status,c.client_name,c.client_email,c.active
      FROM events e LEFT JOIN agency_clients c ON c.event_id=e.id AND c.workspace_id=e.workspace_id
      WHERE e.workspace_id=? ORDER BY e.start_date DESC""",(wid,)).fetchall()
   result=[]
   for ev in e:
    documents=list_docs(con,ev['id'],wid)
    invoices=[d for d in documents if d['kind']=='invoice' and d['status']=='issued']
    from agency_advanced import metrics as project_financials
    finance=project_financials(con,wid,ev['id'])
    result.append({**dict(ev),'document_count':len(documents),
       'open_proposals':sum(1 for d in documents if d['kind']=='proposal' and d['status']=='sent'),
       'pending_contracts':sum(1 for d in documents if d['kind']=='contract' and d['status']=='sent'),
       'outstanding_cents':sum(d['balance_cents'] for d in invoices),
       'expense_cents':finance['incurred_expenses_cents'],
       'invoiced_cents':finance['invoiced_cents'],
       'margin_cents':finance['invoiced_margin_cents']})
   return {'events':result}

 @app.get('/api/w/{wid}/events/{eid}/agency')
 def agency_overview(request:Request,wid:str,eid:str):
  with db() as con:
   role,e=staff(request,con,wid,eid)
   portal=con.execute('SELECT * FROM agency_clients WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
   docs=list_docs(con,eid,wid)
   invoices=[x for x in docs if x['kind']=='invoice' and x['status']=='issued']
   return {'event':e,'portal':portal_summary(portal) if portal else None,
           'documents':docs,'can_edit':role['role'] in ('owner','editor'),
           'currency':con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()['currency'],
           'receivable_cents':sum(x['balance_cents'] for x in invoices),
           'paid_cents':sum(x['paid_cents'] for x in invoices),
           'company':company_profile(con,wid),
           'history':[dict(x) for x in con.execute("""SELECT actor,action,reference_id,created_at FROM agency_activity
               WHERE event_id=? AND workspace_id=? ORDER BY created_at DESC LIMIT 40""",(eid,wid))]}

 @app.post('/api/w/{wid}/events/{eid}/agency/portal')
 def setup_portal(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'client_name','client_email','pin'}:
   raise HTTPException(422,'Provide client name, email, and PIN')
  name=text(payload['client_name'],150,'client name',True)
  address=email(payload['client_email'])
  pin=payload['pin']
  if not isinstance(pin,str) or not re.fullmatch(r'[0-9]{6,12}',pin):
   raise HTTPException(422,'Use a 6–12 digit client PIN')
  with db() as con:
   staff(request,con,wid,eid,True)
   if con.execute('SELECT 1 FROM agency_clients WHERE event_id=?',(eid,)).fetchone():
    raise HTTPException(409,'Client portal exists; update access instead')
   salt=secrets.token_hex(16);t=now();token=secrets.token_urlsafe(32)
   con.execute("""INSERT INTO agency_clients(id,workspace_id,event_id,client_name,client_email,
      access_token,pin_salt,pin_hash,expires_at,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      (uid(),wid,eid,name,address,token,salt,hash_pin(pin,salt),plus_days(365),t,t))
   record_audit(con,wid,eid,get_user(request,con)['id'],'portal.created',eid)
   p=require_portal(con,wid,eid)
   return portal_summary(p)

 @app.patch('/api/w/{wid}/events/{eid}/agency/portal')
 def update_portal(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or not payload or set(payload)-{'client_name','client_email','active','pin','rotate_link','extend_days'}:
   raise HTTPException(422,'Invalid portal settings')
  with db() as con:
   staff(request,con,wid,eid,True)
   p=require_portal(con,wid,eid)
   updates={}
   if 'client_name' in payload:updates['client_name']=text(payload['client_name'],150,'client name',True)
   if 'client_email' in payload:updates['client_email']=email(payload['client_email'])
   if 'active' in payload:
    if type(payload['active']) is not bool:raise HTTPException(422,'Invalid active flag')
    updates['active']=int(payload['active'])
   if 'pin' in payload:
    pin=payload['pin']
    if not isinstance(pin,str) or not re.fullmatch(r'[0-9]{6,12}',pin):
     raise HTTPException(422,'Use a 6–12 digit client PIN')
    salt=secrets.token_hex(16);updates.update(pin_salt=salt,pin_hash=hash_pin(pin,salt))
   if payload.get('rotate_link') is True:
    updates['access_token']=secrets.token_urlsafe(32)
   elif 'rotate_link' in payload and payload['rotate_link'] is not False:
    raise HTTPException(422,'Invalid rotate flag')
   if 'extend_days' in payload:
    days=payload['extend_days']
    if type(days) is not int or not 1<=days<=730:raise HTTPException(422,'Invalid access duration')
    updates['expires_at']=plus_days(days)
   updates['updated_at']=now()
   con.execute('UPDATE agency_clients SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',
               [*updates.values(),p['id']])
   if any(k in updates for k in ('access_token','pin_hash','active','expires_at')):
    con.execute('DELETE FROM agency_sessions WHERE client_id=?',(p['id'],))
   record_audit(con,wid,eid,get_user(request,con)['id'],'portal.updated',p['id'])
   return portal_summary(require_portal(con,wid,eid))

 @app.post('/api/w/{wid}/events/{eid}/agency/portal/send')
 def send_invitation(request:Request,wid:str,eid:str):
  with db() as con:
   staff(request,con,wid,eid,True)
   p=require_portal(con,wid,eid)
   if not p['active'] or p['expires_at']<=now():raise HTTPException(409,'Client portal is not active')
   if not p['client_email']:raise HTTPException(422,'Add a client email before sending')
   if not mail_enabled():raise HTTPException(503,'SMTP email delivery is not configured')
   public=os.getenv('EVENTOS_PUBLIC_BASE_URL','').rstrip('/')
   if not public.startswith('https://'):
    raise HTTPException(503,'Secure public app URL is not configured')
   recipient=p['client_email']
   brand=public_brand(con,wid) or {}
   company=brand.get('name') or 'EventOS'
   content=('Your '+company+' client portal is ready.\n\nOpen '+public+client_link(p)+'\n\n'
            'Your event planner will provide your PIN separately. For your security, '
            'the PIN is not included in this email.')
  try:
   if brand:
    send_email(recipient,'Your '+company+' client portal',content,
               display_name=brand.get('email_name') or company,
               reply_to=brand.get('reply_to') or None)
   else:send_email(recipient,'Your EventOS client portal',content)
  except Exception:raise HTTPException(503,'Email could not be sent')
  with db() as con:record_audit(con,wid,eid,get_user(request,con)['id'],'portal.email_sent',p['id'])
  return {'ok':True,'message':'Portal link emailed. Share the PIN using a separate channel.'}

 @app.post('/api/w/{wid}/events/{eid}/agency/documents')
 def add_document(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'kind','document'} or payload['kind'] not in KINDS:
   raise HTTPException(422,'Choose proposal, contract, or invoice')
  kind=payload['kind'];data=clean_document(payload['document'],kind)
  with db() as con:
   staff(request,con,wid,eid,True)
   count=con.execute('SELECT COUNT(*) FROM agency_documents WHERE workspace_id=? AND event_id=?',(wid,eid)).fetchone()[0]
   if count>=100:raise HTTPException(409,'This event has reached the 100-document demo limit')
   # Unique per workspace, rather than per event, to avoid duplicate invoice IDs.
   con.execute("""INSERT INTO agency_sequences(workspace_id,kind,number) VALUES(?,?,1)
      ON CONFLICT(workspace_id,kind) DO UPDATE SET number=number+1""",(wid,kind))
   seq=con.execute('SELECT number FROM agency_sequences WHERE workspace_id=? AND kind=?',(wid,kind)).fetchone()[0]
   number={'proposal':'PRO','contract':'AGR','invoice':'INV'}[kind]+'-'+str(seq).zfill(5)
   currency=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()['currency']
   currency=currency if currency in CURRENCIES else 'CAD'
   did=uid();t=now()
   con.execute("""INSERT INTO agency_documents(
      id,workspace_id,event_id,kind,number,title,body,terms,lines_json,subtotal_currency,
      discount_cents,tax_bps,total_cents,due_date,valid_until,status,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
      (did,wid,eid,kind,number,data['title'],data['body'],data['terms'],data['lines_json'],currency,
       data['discount_cents'],data['tax_bps'],data['total_cents'],data['due_date'],data['valid_until'],t,t))
   record_audit(con,wid,eid,get_user(request,con)['id'],'document.created',did)
   return doc_view(con,get_doc(con,wid,eid,did))

 @app.put('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}')
 def edit_document(request:Request,wid:str,eid:str,doc_id:str,payload:dict):
  with db() as con:
   staff(request,con,wid,eid,True)
   doc=get_doc(con,wid,eid,doc_id)
   if doc['status']!='draft':raise HTTPException(409,'Sent documents are locked; create a new version')
   data=clean_document(payload,doc['kind'])
   con.execute("""UPDATE agency_documents SET title=?,body=?,terms=?,lines_json=?,discount_cents=?,tax_bps=?,
      total_cents=?,due_date=?,valid_until=?,updated_at=? WHERE id=?""",
      (data['title'],data['body'],data['terms'],data['lines_json'],data['discount_cents'],
       data['tax_bps'],data['total_cents'],data['due_date'],data['valid_until'],now(),doc_id))
   record_audit(con,wid,eid,get_user(request,con)['id'],'document.edited',doc_id)
   return doc_view(con,get_doc(con,wid,eid,doc_id))

 @app.delete('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}')
 def delete_draft(request:Request,wid:str,eid:str,doc_id:str):
  with db() as con:
   staff(request,con,wid,eid,True)
   doc=get_doc(con,wid,eid,doc_id)
   if doc['status']!='draft':
    raise HTTPException(409,'Issued documents cannot be deleted')
   con.execute('DELETE FROM agency_documents WHERE id=?',(doc_id,))
   record_audit(con,wid,eid,get_user(request,con)['id'],'document.draft_deleted',doc_id)
  return {'ok':True}

 @app.post('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/issue')
 def issue_document(request:Request,wid:str,eid:str,doc_id:str):
  with db() as con:
   staff(request,con,wid,eid,True);doc=get_doc(con,wid,eid,doc_id)
   if doc['status']!='draft':raise HTTPException(409,'Only drafts can be sent')
   if doc['kind']=='invoice' and not doc['due_date']:raise HTTPException(422,'Invoice requires due date')
   if doc['kind']=='contract' and (not doc['body'] or not doc['terms']):
    raise HTTPException(422,'Complete agreement text before sending')
   t=now()
   con.execute('UPDATE agency_documents SET status=?,issued_at=?,updated_at=? WHERE id=?',
              ('issued' if doc['kind']=='invoice' else 'sent',t,t,doc_id))
   record_audit(con,wid,eid,get_user(request,con)['id'],'document.issued',doc_id)
   return doc_view(con,get_doc(con,wid,eid,doc_id))

 @app.post('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/duplicate')
 def duplicate_document(request:Request,wid:str,eid:str,doc_id:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'kind'} or payload['kind'] not in KINDS:
   raise HTTPException(422,'Invalid target kind')
  kind=payload['kind']
  with db() as con:
   staff(request,con,wid,eid,True)
   doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']=='proposal' and kind=='invoice' and doc['status']!='accepted':
    raise HTTPException(409,'Accept the proposal before generating an invoice')
   data={k:doc[k] for k in ('title','body','terms','discount_cents','tax_bps','due_date','valid_until')}
   data['title']=('Invoice for ' if kind=='invoice' else 'Copy of ')+data['title']
   data['lines']=json.loads(doc['lines_json'])
  # Call the new document logic with an original, validated source snapshot.
  return add_document(request,wid,eid,{'kind':kind,'document':data})

 @app.post('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/notify')
 def notify_document(request:Request,wid:str,eid:str,doc_id:str):
  with db() as con:
   staff(request,con,wid,eid,True)
   document=get_doc(con,wid,eid,doc_id)
   if document['status']=='draft':
    raise HTTPException(409,'Issue the document before sending a notification')
   p=require_portal(con,wid,eid)
   if not p['active'] or p['expires_at']<=now() or not p['client_email']:
    raise HTTPException(409,'Enable the client portal and add a recipient email')
   if not mail_enabled():raise HTTPException(503,'SMTP is not configured')
   host=os.getenv('EVENTOS_PUBLIC_BASE_URL','').rstrip('/')
   if not host.startswith('https://'):raise HTTPException(503,'Secure public URL is not configured')
   name=p['client_name']
   recipient=p['client_email']
   link=host+client_link(p)
   brand=public_brand(con,wid) or {}
   company=brand.get('name') or company_profile(con,wid)['legal_name'] or 'Your event planner'
   note=('Hello '+name+',\n\nYour '+document['kind']+' '+document['number']+
         ' ('+document['title']+') is ready in your '+company+' portal.\n\n'
         'Open '+link+'\n\nUse your separate PIN to sign in.\n'
         'Contact your event planner with any questions.')
  try:
   if brand:
    send_email(recipient,company+': '+document['kind']+' '+document['number']+' is ready',note,
               display_name=brand.get('email_name') or company,
               reply_to=brand.get('reply_to') or None)
   else:send_email(recipient,'EventOS: '+document['kind']+' '+document['number']+' is ready',note)
  except Exception:raise HTTPException(503,'Email provider could not send your notification')
  with db() as con:
   record_audit(con,wid,eid,get_user(request,con)['id'],'document.email_sent',doc_id)
  return {'ok':True,'message':'Document notification sent (without PIN)'}

 @app.post('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/void')
 def void_invoice(request:Request,wid:str,eid:str,doc_id:str):
  with db() as con:
   staff(request,con,wid,eid,True);doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']!='invoice' or doc['status']!='issued':
    raise HTTPException(409,'Only issued invoices can be voided')
   if con.execute('SELECT 1 FROM agency_payments WHERE document_id=? LIMIT 1',(doc_id,)).fetchone():
    raise HTTPException(409,'Cannot void an invoice with recorded payments')
   con.execute("UPDATE agency_documents SET status='void',updated_at=? WHERE id=?",(now(),doc_id))
   record_audit(con,wid,eid,get_user(request,con)['id'],'invoice.voided',doc_id)
   return doc_view(con,get_doc(con,wid,eid,doc_id))

 @app.post('/api/w/{wid}/events/{eid}/agency/documents/{doc_id}/payments')
 def add_payment(request:Request,wid:str,eid:str,doc_id:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'amount_cents','method','note','paid_on'}:
   raise HTTPException(422,'Provide payment details')
  amt=payload['amount_cents']
  if type(amt) is not int or amt<=0 or amt>1000000000:
   raise HTTPException(422,'Invalid payment amount')
  method=payload['method']
  if not isinstance(method,str) or method not in METHODS:raise HTTPException(422,'Invalid payment method')
  note=text(payload['note'],600,'payment note')
  paid=iso_date(payload['paid_on'],'payment')
  if not paid:raise HTTPException(422,'Payment date required')
  with db() as con:
   staff(request,con,wid,eid,True)
   # BEGIN IMMEDIATE prevents concurrent manual payments from overbooking balance.
   con.execute('BEGIN IMMEDIATE') if not con.in_transaction else None
   doc=get_doc(con,wid,eid,doc_id)
   if doc['kind']!='invoice' or doc['status']!='issued':raise HTTPException(409,'Invoice is not issued')
   view=doc_view(con,doc)
   if amt>view['balance_cents']:raise HTTPException(409,'Payment exceeds the invoice balance')
   who=get_user(request,con)
   pid=uid()
   con.execute("""INSERT INTO agency_payments(id,workspace_id,event_id,document_id,amount_cents,method,note,
      paid_on,created_at,staff_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
      (pid,wid,eid,doc_id,amt,method,note,paid,now(),who['id']))
   record_audit(con,wid,eid,get_user(request,con)['id'],'payment.recorded',pid)
   return doc_view(con,get_doc(con,wid,eid,doc_id))

 @app.get('/api/w/{wid}/events/{eid}/agency/invoices.csv')
 def invoices_csv(request:Request,wid:str,eid:str):
  with db() as con:
   staff(request,con,wid,eid)
   docs=[x for x in list_docs(con,eid,wid) if x['kind']=='invoice']
  output=io.StringIO()
  w=csv.writer(output);w.writerow(['Number','Title','Currency','Status','Due date','Total cents','Paid cents','Balance cents'])
  for d in docs:
   w.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v
                for v in (d['number'],d['title'],d['subtotal_currency'],d['display_status'],
                          d['due_date'],d['total_cents'],d['paid_cents'],d['balance_cents'])])
  return Response('\ufeff'+output.getvalue(),media_type='text/csv',
                  headers={'Content-Disposition':'attachment; filename="eventos-invoices.csv"'})

 @app.get('/client/{token}')
 def client_html(token:str):
  with db() as con:get_client_by_link(con,token)
  return FileResponse(ROOT/'static'/'agency_client.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

 @app.get('/api/client/{token}/session')
 def session_state(request:Request,token:str):
  with db() as con:
   p,e=get_client_by_link(con,token)
   try: client_session(request,con,token)
   except HTTPException as error:
    if error.status_code==401:return {'authenticated':False,'event_name':e['name'],'client_name':p['client_name'],
                                    'brand':public_brand(con,p['workspace_id'])}
    raise
   return {'authenticated':True,'event_name':e['name'],'client_name':p['client_name'],
           'brand':public_brand(con,p['workspace_id'])}

 @app.post('/api/client/{token}/login')
 def client_login(request:Request,response:Response,token:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'pin'}:
   raise HTTPException(422,'Enter your PIN')
  pin=payload['pin']
  if not isinstance(pin,str) or not re.fullmatch(r'\d{6,12}',pin):
   raise HTTPException(401,'Incorrect access PIN')
  with db() as con:
   p,e=get_client_by_link(con,token)
   addr=request.client.host if request.client else 'unknown'
   remote=token_digest(addr)
   attempt=con.execute('SELECT * FROM agency_login_limits WHERE client_id=? AND remote_hash=?',
                       (p['id'],remote)).fetchone()
   current=now();window=plus_days(-1/96) # 15 minutes
   if attempt and attempt['window_started']>window and attempt['failures']>=5:
    return Response(status_code=429,content='Too many attempts. Try again later.')
   correct=hmac.compare_digest(hash_pin(pin,p['pin_salt']),p['pin_hash'])
   if not correct:
    failures=attempt['failures']+1 if attempt and attempt['window_started']>window else 1
    started=attempt['window_started'] if attempt and attempt['window_started']>window else current
    con.execute("""INSERT INTO agency_login_limits(client_id,remote_hash,window_started,failures)
         VALUES(?,?,?,?) ON CONFLICT(client_id,remote_hash) DO UPDATE
         SET window_started=excluded.window_started,failures=excluded.failures""",
         (p['id'],remote,started,failures))
   else:
    con.execute('DELETE FROM agency_login_limits WHERE client_id=? AND remote_hash=?',(p['id'],remote))
    raw=secrets.token_urlsafe(32)
    con.execute('INSERT INTO agency_sessions VALUES(?,?,?,?)',
                (token_digest(raw),p['id'],plus_days(1),current))
  if not correct:raise HTTPException(401,'Incorrect access PIN')
  set_cookie(response,raw)
  return {'ok':True}

 @app.post('/api/client/{token}/logout')
 def client_logout(request:Request,response:Response,token:str):
  raw=request.cookies.get(COOKIE,'')
  if raw:
   with db() as con:con.execute('DELETE FROM agency_sessions WHERE session_hash=?',(token_digest(raw),))
  response.delete_cookie(COOKIE,path='/api/client/')
  return {'ok':True}

 @app.get('/api/client/{token}/dashboard')
 def client_dashboard(request:Request,token:str):
  with db() as con:
   p,e=client_session(request,con,token)
   rows=[x for x in list_docs(con,p['event_id'],p['workspace_id']) if x['status']!='draft']
   for x in rows:
    if x['kind']=='invoice':
     # No internal staff identifiers, payment method notes, or PII on client views.
     x['payments']=[{k:v for k,v in payment.items() if k in ('amount_cents','paid_on','method')}
                    for payment in x['payments']]
   return {'client_name':p['client_name'],'event':dict(e),'documents':rows,
           'currency':con.execute('SELECT currency FROM workspaces WHERE id=?',(p['workspace_id'],)).fetchone()['currency'],
           'company':company_profile(con,p['workspace_id']),
           'brand':public_brand(con,p['workspace_id'])}

 @app.post('/api/client/{token}/documents/{doc_id}/respond')
 def client_respond(request:Request,token:str,doc_id:str,payload:dict):
  if not isinstance(payload,dict) or not set(payload).issubset({'decision','signer_name','consent','comment'}):
   raise HTTPException(422,'Invalid response')
  decision=payload.get('decision')
  if decision not in ('accept','decline','sign'):raise HTTPException(422,'Choose accept, decline or sign')
  with db() as con:
   p,e=client_session(request,con,token)
   doc=get_doc(con,p['workspace_id'],p['event_id'],doc_id)
   if doc['kind']=='proposal' and decision in ('accept','decline') and doc['status']=='sent':
    if doc['valid_until'] and date.fromisoformat(doc['valid_until'])<date.today():
     raise HTTPException(409,'Proposal has expired; request a new version')
    status='accepted' if decision=='accept' else 'declined'
    signature=''
    signer=text(payload.get('signer_name',''),150,'client name')
    if decision=='accept' and not signer:raise HTTPException(422,'Enter your full name')
   elif doc['kind']=='contract' and decision=='sign' and doc['status']=='sent':
    if doc['valid_until'] and date.fromisoformat(doc['valid_until'])<date.today():
     raise HTTPException(409,'Agreement has expired; request a new version')
    signer=text(payload.get('signer_name',''),150,'signature name',True)
    if payload.get('consent') is not True:
     raise HTTPException(422,'You must explicitly agree to sign')
    status='signed'
    timestamp=now()
    snapshot=json.dumps({'id':doc_id,'body':doc['body'],'terms':doc['terms'],
                         'lines':doc['lines_json'],'total_cents':doc['total_cents'],
                         'signer':signer,'accepted_at':timestamp},sort_keys=True)
    signature=hashlib.sha256(snapshot.encode()).hexdigest()
   else:
    raise HTTPException(409,'This response is not available for the document state')
   if doc['kind']=='proposal':timestamp=now()
   remote=request.client.host if request.client else ''
   iphash=token_digest(remote)
   con.execute("""UPDATE agency_documents SET status=?,responded_at=?,signer_name=?,signature_hash=?,
         accepted_ip_hash=?,updated_at=? WHERE id=?""",
         (status,timestamp,signer,signature,iphash,timestamp,doc_id))
   record_audit(con,p['workspace_id'],p['event_id'],'client',
                'contract.signed' if decision=='sign' else 'proposal.'+status,doc_id)
   return {'ok':True,'status':status,'document':doc_view(con,get_doc(con,p['workspace_id'],p['event_id'],doc_id))}

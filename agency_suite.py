"""EventOS Phase 9: client portals, proposals, agreements and manual invoicing.

Scoped by (workspace,event,client portal), with opaque visitor sessions.
No payment processing, cryptographic signatures or guaranteed legal compliance.
"""
from __future__ import annotations
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

ROOT=Path(__file__).resolve().parent
DOC_KINDS=('proposal','contract','invoice')
CURRENCY=re.compile(r'^[A-Z]{3}$')
DATE=re.compile(r'^\d{4}-\d{2}-\d{2}$')
SESSION_COOKIE='eventos_client_session'


def utc():
    return datetime.now(timezone.utc)


def stamp():
    return utc().isoformat(timespec='seconds')


def ident():
    return str(uuid.uuid4())


def text(value,maxlen,name,required=False):
    if not isinstance(value,str) or len(value)>maxlen:
        raise HTTPException(422,'Invalid '+name)
    value=value.strip()
    if required and not value:
        raise HTTPException(422,name+' is required')
    return value


def number(value,name,lo=0,hi=1000000000):
    if type(value) is not int or not lo<=value<=hi:
        raise HTTPException(422,'Invalid '+name)
    return value


def date(value):
    if value in ('',None):return ''
    v=text(value,10,'date')
    if not DATE.fullmatch(v):raise HTTPException(422,'Invalid date')
    try:datetime.strptime(v,'%Y-%m-%d')
    except ValueError:raise HTTPException(422,'Invalid date')
    return v


def pin_hash(salt,pin):
    return hashlib.scrypt(pin.encode('utf-8'),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()


def pin_pair():
    pin='-'.join((secrets.token_hex(3).upper(),secrets.token_hex(3).upper()))
    salt=secrets.token_hex(16)
    return pin,salt,pin_hash(salt,pin)


def htoken(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def migrate(db):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS agency_client_portals(
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
          client_name TEXT NOT NULL,
          client_email TEXT NOT NULL DEFAULT '',
          access_token TEXT NOT NULL UNIQUE,
          pin_salt TEXT NOT NULL,
          pin_hash TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_client_sessions(
          session_hash TEXT PRIMARY KEY,
          portal_id TEXT NOT NULL REFERENCES agency_client_portals(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_client_attempts(
          attempt_key TEXT PRIMARY KEY,
          window_at TEXT NOT NULL, attempts INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_documents(
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('proposal','contract','invoice')),
          serial TEXT NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          items_json TEXT NOT NULL,
          subtotal_cents INTEGER NOT NULL DEFAULT 0,
          discount_cents INTEGER NOT NULL DEFAULT 0,
          tax_bps INTEGER NOT NULL DEFAULT 0,
          tax_cents INTEGER NOT NULL DEFAULT 0,
          total_cents INTEGER NOT NULL DEFAULT 0,
          currency TEXT NOT NULL,
          due_date TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          sent_at TEXT NOT NULL DEFAULT '',
          responded_at TEXT NOT NULL DEFAULT '',
          signer_name TEXT NOT NULL DEFAULT '',
          signer_consent INTEGER NOT NULL DEFAULT 0,
          signer_ip_digest TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agency_docs_event ON agency_documents(workspace_id,event_id,created_at);
        CREATE TABLE IF NOT EXISTS agency_invoice_payments(
          id TEXT PRIMARY KEY,
          doc_id TEXT NOT NULL REFERENCES agency_documents(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL, event_id TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          method TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          recorded_by TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agency_pay_doc ON agency_invoice_payments(doc_id);
        CREATE TABLE IF NOT EXISTS agency_document_activity(
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
          event_id TEXT NOT NULL, doc_id TEXT NOT NULL,
          action TEXT NOT NULL, actor TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        """)


def payload_content(payload,kind):
    if not isinstance(payload,dict) or set(payload)-{'kind','title','body','items','discount_cents','tax_bps','due_date'}:
        raise HTTPException(422,'Invalid document fields')
    title=text(payload.get('title',''),180,'title',True)
    body=text(payload.get('body',''),12000,'document details')
    items=payload.get('items',[])
    if not isinstance(items,list) or len(items)>60:
        raise HTTPException(422,'Use up to 60 line items')
    result=[]
    for x in items:
        if not isinstance(x,dict) or set(x)!={'description','quantity','unit_cents'}:
            raise HTTPException(422,'Invalid line item')
        result.append({'description':text(x['description'],250,'item description',True),
                       'quantity':number(x['quantity'],'quantity',1,10000),
                       'unit_cents':number(x['unit_cents'],'unit amount',0,100000000)})
    if kind=='contract':
        if not body:raise HTTPException(422,'Agreement terms are required')
        if result:raise HTTPException(422,'Agreement cannot have invoice items')
    elif not result:raise HTTPException(422,'Include at least one line item')
    subtotal=sum(x['quantity']*x['unit_cents'] for x in result)
    if subtotal>100000000000:raise HTTPException(422,'Document amount is too large')
    discount=number(payload.get('discount_cents',0),'discount',0,100000000000)
    if discount>subtotal:raise HTTPException(422,'Discount exceeds subtotal')
    tax_bps=number(payload.get('tax_bps',0),'tax rate',0,10000)
    taxes=((subtotal-discount)*tax_bps+5000)//10000
    return {'title':title,'body':body,'items_json':json.dumps(result),
            'subtotal_cents':subtotal,'discount_cents':discount,'tax_bps':tax_bps,
            'tax_cents':taxes,'total_cents':subtotal-discount+taxes,
            'due_date':date(payload.get('due_date',''))}


def activity(con,doc,action,actor,details=''):
    con.execute("""INSERT INTO agency_document_activity
       (id,workspace_id,event_id,doc_id,action,actor,details,created_at)
       VALUES (?,?,?,?,?,?,?,?)""",
       (ident(),doc['workspace_id'],doc['event_id'],doc['id'],action,actor,details,stamp()))


def document(con,row):
    doc=dict(row)
    doc['items']=json.loads(doc.pop('items_json'))
    payments=[dict(x) for x in con.execute("""SELECT id,amount_cents,method,note,recorded_at
      FROM agency_invoice_payments WHERE doc_id=? ORDER BY recorded_at,id""",(doc['id'],))]
    paid=sum(p['amount_cents'] for p in payments)
    doc['paid_cents']=paid
    doc['balance_cents']=max(0,doc['total_cents']-paid)
    doc['payments']=payments
    if doc['kind']=='invoice' and doc['status']=='sent':
        if paid>=doc['total_cents'] and doc['total_cents']>0:doc['status']='paid'
        elif paid>0:doc['status']='partial'
        elif doc['due_date'] and doc['due_date']<utc().date().isoformat():doc['status']='overdue'
    doc.pop('signer_ip_digest',None)
    return doc


def scoped_doc(con,wid,eid,did):
    row=con.execute("""SELECT * FROM agency_documents WHERE id=? AND workspace_id=? AND event_id=?""",
                    (did,wid,eid)).fetchone()
    if not row:raise HTTPException(404,'Document not found')
    return row


def scoped_portal(con,wid,eid):
    row=con.execute("SELECT * FROM agency_client_portals WHERE workspace_id=? AND event_id=?",(wid,eid)).fetchone()
    if not row:raise HTTPException(404,'Client portal not created')
    return row


def portal_public(row):
    return {'id':row['id'],'client_name':row['client_name'],'client_email':row['client_email'],
            'active':bool(row['active']),'url':'/client/'+row['access_token'],'created_at':row['created_at']}


def secured_session(con,request,token):
    portal=con.execute('SELECT * FROM agency_client_portals WHERE access_token=? AND active=1',(token,)).fetchone()
    if not portal:raise HTTPException(404,'Client portal unavailable')
    cookie=request.cookies.get(SESSION_COOKIE,'')
    if not cookie or len(cookie)>200:
        raise HTTPException(401,'Enter your client access code')
    session=con.execute("""SELECT portal_id FROM agency_client_sessions
      WHERE session_hash=? AND expires_at>?""",(htoken(cookie),stamp())).fetchone()
    if not session or session['portal_id']!=portal['id']:
        raise HTTPException(401,'Your client session expired. Enter your code again.')
    return portal


def safe_doc_for_client(con,row):
    doc=document(con,row)
    doc.pop('workspace_id',None)
    doc.pop('event_id',None)
    doc.pop('signer_consent',None)
    doc.pop('signer_name',None) if doc['status']=='draft' else None
    return doc


def install_agency(app,db,access,check_event,get_user):
    migrate(db)

    def event_scope(request,con,wid,eid,write=False):
        role=access(request,con,wid,write)
        event=check_event(con,wid,eid)
        return role,event

    @app.get('/api/w/{wid}/events/{eid}/agency')
    def organizer_dashboard(request:Request,wid:str,eid:str):
        with db() as con:
            role,event=event_scope(request,con,wid,eid)
            portal=con.execute('SELECT * FROM agency_client_portals WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
            docs=con.execute("""SELECT * FROM agency_documents WHERE workspace_id=? AND event_id=?
                        ORDER BY created_at DESC,id DESC""",(wid,eid)).fetchall()
            all_docs=[document(con,x) for x in docs]
            return {'event':{'id':eid,'name':event['name'],'date':event['start_date']},
                    'portal':portal_public(portal) if portal else None,
                    'documents':all_docs,'can_edit':role['role'] in ('owner','editor'),
                    'activity':[dict(x) for x in con.execute("""SELECT doc_id,action,actor,details,created_at
                      FROM agency_document_activity WHERE workspace_id=? AND event_id=?
                      ORDER BY created_at DESC LIMIT 70""",(wid,eid))]}

    @app.post('/api/w/{wid}/events/{eid}/agency/portal')
    def create_portal(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'client_name','client_email'}:
            raise HTTPException(422,'Client name and email required')
        name=text(payload['client_name'],150,'client name',True)
        email=text(payload['client_email'],255,'client email').lower()
        if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
            raise HTTPException(422,'Invalid client email')
        with db() as con:
            event_scope(request,con,wid,eid,True)
            if con.execute('SELECT 1 FROM agency_client_portals WHERE event_id=?',(eid,)).fetchone():
                raise HTTPException(409,'This event already has a client portal')
            pin,salt,digest=pin_pair()
            token=secrets.token_urlsafe(36)
            con.execute("""INSERT INTO agency_client_portals
              (id,workspace_id,event_id,client_name,client_email,access_token,pin_salt,pin_hash,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?)""",(ident(),wid,eid,name,email,token,salt,digest,stamp(),stamp()))
            return {'url':'/client/'+token,'access_code':pin,'client_name':name,
                    'note':'Share the client link and access code securely. The code is shown once only.'}

    @app.patch('/api/w/{wid}/events/{eid}/agency/portal')
    def update_portal(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or not payload or set(payload)-{'client_name','client_email','active'}:
            raise HTTPException(422,'Invalid portal settings')
        clean={}
        for key,v in payload.items():
            if key=='client_name':clean[key]=text(v,150,'client name',True)
            elif key=='client_email':
                email=text(v,255,'client email').lower()
                if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
                    raise HTTPException(422,'Invalid client email')
                clean[key]=email
            elif key=='active':
                if type(v) is not bool:raise HTTPException(422,'Invalid active flag')
                clean[key]=int(v)
        with db() as con:
            event_scope(request,con,wid,eid,True)
            p=scoped_portal(con,wid,eid)
            con.execute('UPDATE agency_client_portals SET '+','.join(k+'=?' for k in clean)+
                 ',updated_at=? WHERE id=?',(*clean.values(),stamp(),p['id']))
            if clean.get('active')==0:
                con.execute('DELETE FROM agency_client_sessions WHERE portal_id=?',(p['id'],))
            return portal_public(scoped_portal(con,wid,eid))

    @app.post('/api/w/{wid}/events/{eid}/agency/portal/rotate')
    def rotate_portal(request:Request,wid:str,eid:str):
        with db() as con:
            event_scope(request,con,wid,eid,True)
            p=scoped_portal(con,wid,eid)
            token=secrets.token_urlsafe(36)
            pin,salt,digest=pin_pair()
            con.execute("""UPDATE agency_client_portals SET access_token=?,pin_salt=?,pin_hash=?,
                      updated_at=? WHERE id=?""",(token,salt,digest,stamp(),p['id']))
            con.execute('DELETE FROM agency_client_sessions WHERE portal_id=?',(p['id'],))
            return {'url':'/client/'+token,'access_code':pin,
                    'note':'The former client URL, PIN and active sessions have been revoked.'}

    @app.post('/api/w/{wid}/events/{eid}/agency/documents')
    def create_document(request:Request,wid:str,eid:str,payload:dict):
        kind=payload.get('kind') if isinstance(payload,dict) else None
        if kind not in DOC_KINDS:raise HTTPException(422,'Choose proposal, contract or invoice')
        values=payload_content(payload,kind)
        with db() as con:
            _,ev=event_scope(request,con,wid,eid,True)
            workspace=con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()
            currency=workspace['currency'].upper()
            if not CURRENCY.fullmatch(currency):currency='CAD'
            count=con.execute('SELECT COUNT(*) FROM agency_documents WHERE workspace_id=? AND kind=?',(wid,kind)).fetchone()[0]
            # Random suffix avoids serial conflicts between concurrent writers.
            serial=kind[:3].upper()+'-'+str(count+1).zfill(4)+'-'+secrets.token_hex(2).upper()
            did=ident()
            con.execute("""INSERT INTO agency_documents
               (id,workspace_id,event_id,kind,serial,title,body,items_json,subtotal_cents,
                discount_cents,tax_bps,tax_cents,total_cents,currency,due_date,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (did,wid,eid,kind,serial,values['title'],values['body'],values['items_json'],
                values['subtotal_cents'],values['discount_cents'],values['tax_bps'],
                values['tax_cents'],values['total_cents'],currency,values['due_date'],
                'draft',stamp(),stamp()))
            row=scoped_doc(con,wid,eid,did)
            activity(con,row,'created',get_user(request,con)['id'])
            return document(con,row)

    @app.patch('/api/w/{wid}/events/{eid}/agency/documents/{did}')
    def edit_document(request:Request,wid:str,eid:str,did:str,payload:dict):
        with db() as con:
            event_scope(request,con,wid,eid,True)
            old=scoped_doc(con,wid,eid,did)
            if old['status']!='draft':
                raise HTTPException(409,'Sent or responded documents are locked. Create a revised document.')
            values=payload_content(payload,old['kind'])
            fields=list(values)
            con.execute("UPDATE agency_documents SET "+','.join(f+"=?" for f in fields)+
                        ",updated_at=? WHERE id=?",(*values.values(),stamp(),did))
            row=scoped_doc(con,wid,eid,did)
            activity(con,row,'edited',get_user(request,con)['id'])
            return document(con,row)

    @app.post('/api/w/{wid}/events/{eid}/agency/documents/{did}/send')
    def send_document(request:Request,wid:str,eid:str,did:str):
        with db() as con:
            event_scope(request,con,wid,eid,True)
            portal=scoped_portal(con,wid,eid)
            if not portal['active']:raise HTTPException(409,'Activate the client portal first')
            row=scoped_doc(con,wid,eid,did)
            if row['status']!='draft':raise HTTPException(409,'Only drafts can be published to the client')
            if row['kind']=='invoice' and row['total_cents']==0:
                raise HTTPException(422,'Invoice total must be greater than zero')
            con.execute("""UPDATE agency_documents SET status='sent',sent_at=?,updated_at=? WHERE id=?""",(stamp(),stamp(),did))
            updated=scoped_doc(con,wid,eid,did)
            activity(con,updated,'shared',get_user(request,con)['id'])
            return document(con,updated)

    @app.post('/api/w/{wid}/events/{eid}/agency/documents/{did}/void')
    def void_document(request:Request,wid:str,eid:str,did:str):
        with db() as con:
            event_scope(request,con,wid,eid,True)
            row=scoped_doc(con,wid,eid,did)
            if row['status'] in ('signed','approved','void','declined'):
                raise HTTPException(409,'Signed or responded documents cannot be voided')
            if con.execute('SELECT 1 FROM agency_invoice_payments WHERE doc_id=?',(did,)).fetchone():
                raise HTTPException(409,'Invoices with payment records cannot be voided')
            con.execute("UPDATE agency_documents SET status='void',updated_at=? WHERE id=?",(stamp(),did))
            activity(con,row,'voided',get_user(request,con)['id'])
            return {'ok':True}

    @app.post('/api/w/{wid}/events/{eid}/agency/documents/{did}/payments')
    def record_payment(request:Request,wid:str,eid:str,did:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'amount_cents','method','note'}:
            raise HTTPException(422,'Amount, method and note are required')
        amount=number(payload['amount_cents'],'payment amount',1,100000000000)
        method=text(payload['method'],70,'payment method',True)
        note=text(payload['note'],500,'payment note')
        # Keep financial balance updates serialized inside this DB transaction.
        with db() as con:
            event_scope(request,con,wid,eid,True)
            con.execute('BEGIN IMMEDIATE')
            row=scoped_doc(con,wid,eid,did)
            if row['kind']!='invoice' or row['status']!='sent':
                raise HTTPException(409,'Only sent, non-void invoices accept manual payment records')
            doc=document(con,row)
            if amount>doc['balance_cents']:
                raise HTTPException(409,'Payment exceeds the remaining invoice balance')
            con.execute("""INSERT INTO agency_invoice_payments
                (id,doc_id,workspace_id,event_id,amount_cents,method,note,recorded_by,recorded_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",(ident(),did,wid,eid,amount,method,note,
                              get_user(request,con)['id'],stamp()))
            activity(con,row,'payment_recorded',get_user(request,con)['id'],
                     str(amount)+' cents via '+method)
            return document(con,scoped_doc(con,wid,eid,did))

    @app.get('/api/w/{wid}/events/{eid}/agency/finance.csv')
    def finance_export(request:Request,wid:str,eid:str):
        import csv,io
        with db() as con:
            event_scope(request,con,wid,eid)
            rows=[document(con,x) for x in con.execute("""SELECT * FROM agency_documents
              WHERE workspace_id=? AND event_id=? AND kind='invoice' ORDER BY created_at""",(wid,eid))]
        output=io.StringIO()
        writer=csv.writer(output)
        writer.writerow(['Invoice','Title','Status','Currency','Subtotal cents','Discount cents',
                         'Tax cents','Total cents','Paid cents','Balance cents','Due date'])
        for d in rows:
            def cell(x):
                if isinstance(x,str) and x.lstrip().startswith(('=','+','-','@')):
                    return "'"+x
                return x
            writer.writerow([cell(d['serial']),cell(d['title']),d['status'],d['currency'],d['subtotal_cents'],
                             d['discount_cents'],d['tax_cents'],d['total_cents'],
                             d['paid_cents'],d['balance_cents'],d['due_date']])
        return Response('\ufeff'+output.getvalue(),media_type='text/csv',
               headers={'Content-Disposition':'attachment; filename="eventos-invoices.csv"',
                        'Cache-Control':'no-store'})

    @app.get('/client/{token}')
    def public_client_page(token:str):
        with db() as con:
            exists=con.execute('SELECT active FROM agency_client_portals WHERE access_token=?',(token,)).fetchone()
            if not exists or not exists['active']:raise HTTPException(404,'Client portal not found')
        return FileResponse(ROOT/'static'/'agency_client.html',headers={'Cache-Control':'no-store'})

    @app.post('/api/client-portal/{token}/unlock')
    def client_unlock(request:Request,response:Response,token:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'access_code'}:
            raise HTTPException(422,'Client access code is required')
        code=payload['access_code']
        if not isinstance(code,str) or not 8<=len(code)<=60:
            raise HTTPException(422,'Invalid client code')
        remote=request.client.host if request.client else 'unknown'
        key=htoken(token+'|'+remote)
        allowed=True;good=False
        with db() as con:
            portal=con.execute("SELECT * FROM agency_client_portals WHERE access_token=? AND active=1",(token,)).fetchone()
            if not portal:raise HTTPException(404,'Client portal not found')
            row=con.execute('SELECT window_at,attempts FROM agency_client_attempts WHERE attempt_key=?',(key,)).fetchone()
            start=utc()
            old=(datetime.fromisoformat(row['window_at']) if row else None)
            if old and start-old<timedelta(hours=1):
                allowed=row['attempts']<10
                if allowed:con.execute('UPDATE agency_client_attempts SET attempts=attempts+1 WHERE attempt_key=?',(key,))
            else:
                con.execute("""INSERT INTO agency_client_attempts VALUES (?,?,1)
                   ON CONFLICT(attempt_key) DO UPDATE SET window_at=excluded.window_at,attempts=1""",(key,stamp()))
            good=allowed and hmac.compare_digest(pin_hash(portal['pin_salt'],code.upper()),portal['pin_hash'])
            if good:
                session=secrets.token_urlsafe(38)
                con.execute('INSERT INTO agency_client_sessions VALUES (?,?,?)',
                    (htoken(session),portal['id'],(utc()+timedelta(hours=12)).isoformat(timespec='seconds')))
        if not allowed:raise HTTPException(429,'Too many attempts; try again later')
        if not good:raise HTTPException(401,'Incorrect client access code')
        response.set_cookie(SESSION_COOKIE,session,path='/api/client-portal',httponly=True,
                  secure=os.getenv('EVENTOS_SECURE_COOKIES','0')=='1',samesite='lax',max_age=43200)
        response.headers['Cache-Control']='no-store'
        return {'ok':True}

    @app.post('/api/client-portal/{token}/logout')
    def client_logout(request:Request,response:Response,token:str):
        cookie=request.cookies.get(SESSION_COOKIE,'')
        with db() as con:
            if cookie:con.execute('DELETE FROM agency_client_sessions WHERE session_hash=?',(htoken(cookie),))
        response.delete_cookie(SESSION_COOKIE,path='/api/client-portal')
        return {'ok':True}

    @app.get('/api/client-portal/{token}')
    def client_data(request:Request,token:str):
        with db() as con:
            portal=secured_session(con,request,token)
            e=con.execute('SELECT name,start_date,location FROM events WHERE id=? AND workspace_id=?',
                          (portal['event_id'],portal['workspace_id'])).fetchone()
            docs=con.execute("""SELECT * FROM agency_documents WHERE event_id=? AND workspace_id=?
                 AND status NOT IN ('draft','void') ORDER BY created_at DESC,id DESC""",
                (portal['event_id'],portal['workspace_id'])).fetchall()
            site=con.execute("SELECT slug,access_token FROM event_websites WHERE event_id=? AND published=1",
                             (portal['event_id'],)).fetchone()
            return {'client_name':portal['client_name'],
                    'event':{'name':e['name'],'start_date':e['start_date'],'location':e['location']},
                    'website_url':'/event/'+site['slug']+'/'+site['access_token'] if site else '',
                    'documents':[safe_doc_for_client(con,x) for x in docs]}

    @app.post('/api/client-portal/{token}/documents/{did}/respond')
    def client_respond(request:Request,token:str,did:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'decision','signer_name','consent'}:
            raise HTTPException(422,'Invalid response')
        decision=payload['decision']
        name=text(payload['signer_name'],150,'signer name',True)
        if type(payload['consent']) is not bool or not payload['consent']:
            raise HTTPException(422,'Please confirm you understand this action')
        with db() as con:
            portal=secured_session(con,request,token)
            con.execute('BEGIN IMMEDIATE')
            row=scoped_doc(con,portal['workspace_id'],portal['event_id'],did)
            if row['status']!='sent' or row['kind'] not in ('proposal','contract'):
                raise HTTPException(409,'Document is not awaiting a response')
            valid={'proposal':{'approve':'approved','decline':'declined'},
                   'contract':{'sign':'signed','decline':'declined'}}
            outcome=valid[row['kind']].get(decision)
            if not outcome:raise HTTPException(422,'Invalid decision for document')
            # Explicit typed-name electronic acknowledgement; NOT qualified e-sign.
            # Do not expose IP or session information to clients or exports.
            salt=os.getenv('EVENTOS_AUDIT_PEPPER','eventos-client-audit')
            ip=request.client.host if request.client else 'unknown'
            ip_digest=htoken(salt+'|'+ip)
            con.execute("""UPDATE agency_documents SET status=?,responded_at=?,
              signer_name=?,signer_consent=1,signer_ip_digest=?,updated_at=? WHERE id=?""",
              (outcome,stamp(),name,ip_digest,stamp(),did))
            activity(con,row,outcome,'client',name+' acknowledged and confirmed')
            return safe_doc_for_client(con,scoped_doc(con,portal['workspace_id'],portal['event_id'],did))


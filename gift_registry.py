"""EventOS gift registry: wishlist items, reservation management and cash-fund *pledges*.

This module NEVER processes payments. Amounts on fund items are unenforced pledges
and should never be labeled money received. Contact data stays organizer-only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from saas_access import limits
from event_gallery import binary_image
import cloud_media

MAX_ITEMS=120
MAX_RESERVATIONS=800


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text_field(payload,key,max_size,required=False):
    value=payload.get(key,'')
    if not isinstance(value,str) or len(value)>max_size or (required and not value.strip()):
        raise HTTPException(422,'Invalid '+key)
    return value.strip()


def url_field(payload,key):
    url=text_field(payload,key,500)
    if url:
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or re.search(r'''[\s<>"'\\]''',url)):
            raise HTTPException(422,'Only safe HTTPS links may be used')
    return url


def integer(payload,key,default,low,high):
    value=payload.get(key,default)
    if type(value) is not int or not low<=value<=high:
        raise HTTPException(422,key+' is outside allowed limits')
    return value


def migrate(db):
    cloud_media.migrate(db)
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS gift_registries(
          event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,
          access_token TEXT NOT NULL UNIQUE,
          enabled INTEGER NOT NULL DEFAULT 0,
          title TEXT NOT NULL DEFAULT 'Our Gift Registry',
          intro TEXT NOT NULL DEFAULT 'Thank you for celebrating with us.',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gift_registry_items(
          id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          category TEXT NOT NULL DEFAULT 'Other',
          kind TEXT NOT NULL CHECK(kind IN ('gift','fund')),
          quantity INTEGER NOT NULL DEFAULT 1,
          price_cents INTEGER NOT NULL DEFAULT 0,
          target_cents INTEGER NOT NULL DEFAULT 0,
          link_url TEXT NOT NULL DEFAULT '',
          image_url TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'Normal',
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gift_items_event ON gift_registry_items(event_id,archived,category);
        CREATE TABLE IF NOT EXISTS gift_reservations(
          id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          item_id TEXT NOT NULL REFERENCES gift_registry_items(id) ON DELETE CASCADE,
          donor_name TEXT NOT NULL,
          donor_email TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          quantity INTEGER NOT NULL DEFAULT 1,
          pledged_cents INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL CHECK(status IN ('active','completed','cancelled')),
          cancel_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gift_reservations_item ON gift_reservations(item_id,status);
        CREATE TABLE IF NOT EXISTS gift_registry_images(
          item_id TEXT PRIMARY KEY REFERENCES gift_registry_items(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          image BLOB NOT NULL,
          thumbnail BLOB NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)


def ensure(con,wid,eid):
    row=con.execute('SELECT * FROM gift_registries WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
    if row:return dict(row)
    con.execute('INSERT INTO gift_registries(event_id,workspace_id,access_token,updated_at) VALUES (?,?,?,?)',
        (eid,wid,secrets.token_urlsafe(30),now()))
    return dict(con.execute('SELECT * FROM gift_registries WHERE event_id=?',(eid,)).fetchone())


def guest_access(con,token):
    if not isinstance(token,str) or len(token)>160:
        raise HTTPException(404,'Registry unavailable')
    reg=con.execute('''SELECT g.*,e.name event_name,w.currency
        FROM gift_registries g JOIN events e ON e.id=g.event_id
        JOIN workspaces w ON w.id=g.workspace_id WHERE g.access_token=?''',(token,)).fetchone()
    if not reg or not reg['enabled']:
        raise HTTPException(404,'Registry unavailable')
    limits(con,reg['workspace_id'])
    return dict(reg)


def public_item(item):
    keys=('id','title','description','category','kind','quantity','price_cents','target_cents',
          'link_url','image_url','has_image','priority','reserved_quantity','pledged_cents','completed_quantity','completed_cents')
    return {k:item[k] for k in keys}


def item_rows(con,eid,all_items=False):
    predicate='' if all_items else 'AND i.archived=0'
    rows=con.execute('''SELECT i.*,
      COALESCE((SELECT SUM(r.quantity) FROM gift_reservations r
        WHERE r.item_id=i.id AND r.status IN ('active','completed')),0) AS reserved_quantity,
      COALESCE((SELECT SUM(r.pledged_cents) FROM gift_reservations r
        WHERE r.item_id=i.id AND r.status IN ('active','completed')),0) AS pledged_cents,
      COALESCE((SELECT SUM(r.quantity) FROM gift_reservations r
        WHERE r.item_id=i.id AND r.status='completed'),0) AS completed_quantity,
      COALESCE((SELECT SUM(r.pledged_cents) FROM gift_reservations r
        WHERE r.item_id=i.id AND r.status='completed'),0) AS completed_cents
      ,EXISTS(SELECT 1 FROM gift_registry_images im WHERE im.item_id=i.id) AS has_image
      FROM gift_registry_items i WHERE i.event_id=? '''+predicate+
      ''' ORDER BY i.archived,i.category COLLATE NOCASE,i.created_at DESC''',(eid,)).fetchall()
    return [dict(x) for x in rows]


def clean_item(data):
    if not isinstance(data,dict) or set(data)-{
        'title','description','category','kind','quantity','price_cents','target_cents',
        'link_url','image_url','priority','archived'}:
        raise HTTPException(422,'Invalid registry item')
    kind=data.get('kind','gift')
    if kind not in ('gift','fund'):raise HTTPException(422,'Choose gift or fund')
    priority=data.get('priority','Normal')
    if priority not in ('Normal','High'):raise HTTPException(422,'Invalid priority')
    title=text_field(data,'title',130,True)
    qty=integer(data,'quantity',1,1,100)
    price=integer(data,'price_cents',0,0,100_000_000)
    target=integer(data,'target_cents',0,0,100_000_000)
    if kind=='fund' and (target<100 or qty!=1):
        raise HTTPException(422,'Fund must have a positive target and quantity one')
    if kind=='gift' and target!=0:raise HTTPException(422,'Physical gifts cannot use a fund target')
    result={'title':title,'description':text_field(data,'description',1200),
        'category':text_field(data,'category',60) or 'Other',
        'kind':kind,'quantity':qty,'price_cents':price,'target_cents':target,
        'link_url':url_field(data,'link_url'),'image_url':url_field(data,'image_url'),
        'priority':priority}
    if 'archived' in data:
        if type(data['archived']) is not bool:raise HTTPException(422,'Invalid archive state')
        result['archived']=int(data['archived'])
    return result


def csv_cell(value):
    s=str(value if value is not None else '')
    return ("'"+s) if s.lstrip().startswith(('=','+','-','@')) else s


def install_registry(app,db,access,check_event):
    migrate(db)

    @app.get('/api/w/{wid}/events/{eid}/registry')
    def admin_registry(request:Request,wid:str,eid:str):
        with db() as con:
            role=access(request,con,wid)['role'];check_event(con,wid,eid)
            reg=ensure(con,wid,eid)
            reservations=[dict(x) for x in con.execute('''SELECT r.id,r.item_id,r.donor_name,r.donor_email,
                r.note,r.quantity,r.pledged_cents,r.status,r.created_at,r.updated_at,
                i.title item_title,i.kind FROM gift_reservations r
                JOIN gift_registry_items i ON i.id=r.item_id
                WHERE r.event_id=? ORDER BY r.created_at DESC LIMIT 800''',(eid,))]
            return {'settings':{key:reg[key] for key in ('enabled','title','intro')},
              'share_url':'/gifts/'+reg['access_token'],
              'currency':con.execute('SELECT currency FROM workspaces WHERE id=?',(wid,)).fetchone()[0],
              'items':item_rows(con,eid,True),'reservations':reservations,'can_edit':role in ('owner','editor')}

    @app.patch('/api/w/{wid}/events/{eid}/registry')
    def registry_settings(request:Request,wid:str,eid:str,data:dict):
        if not isinstance(data,dict) or not data or set(data)-{'enabled','title','intro'}:
            raise HTTPException(422,'Invalid registry settings')
        changes={}
        for key,value in data.items():
            if key=='enabled':
                if type(value) is not bool:raise HTTPException(422,'Enabled must be true or false')
                changes[key]=int(value)
            else:changes[key]=text_field(data,key,100 if key=='title' else 1200,key=='title')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            ensure(con,wid,eid)
            con.execute('UPDATE gift_registries SET '+','.join(key+'=?' for key in changes)+',updated_at=? WHERE event_id=?',
                (*changes.values(),now(),eid))
        return {'ok':True}

    @app.post('/api/w/{wid}/events/{eid}/registry/rotate-link')
    def rotate(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            ensure(con,wid,eid)
            token=secrets.token_urlsafe(30)
            con.execute('UPDATE gift_registries SET access_token=?,updated_at=? WHERE event_id=?',(token,now(),eid))
            return {'share_url':'/gifts/'+token}

    @app.post('/api/w/{wid}/events/{eid}/registry/items')
    def create_item(request:Request,wid:str,eid:str,data:dict):
        item=clean_item(data)
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            ensure(con,wid,eid)
            if con.execute('SELECT COUNT(*) FROM gift_registry_items WHERE event_id=?',(eid,)).fetchone()[0]>=MAX_ITEMS:
                raise HTTPException(409,'Maximum registry items reached')
            ident=str(uuid.uuid4())
            keys=['id','event_id','workspace_id',*item.keys(),'created_at']
            con.execute('INSERT INTO gift_registry_items('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',
                (ident,eid,wid,*item.values(),now()))
            return {'id':ident,'item':item}

    @app.post('/api/w/{wid}/events/{eid}/registry/items/{iid}/image')
    def upload_item_image(request:Request,wid:str,eid:str,iid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'content_type','data_base64'}:
            raise HTTPException(422,'Choose an image')
        full,thumb=binary_image(payload)
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            item=con.execute('SELECT id FROM gift_registry_items WHERE id=? AND event_id=? AND workspace_id=?',(iid,eid,wid)).fetchone()
            if not item:raise HTTPException(404,'Gift item not found')
            external=cloud_media.upload_pair(con,'gift_image',iid,wid,eid,full,thumb)
            con.execute('''INSERT INTO gift_registry_images(item_id,workspace_id,event_id,image,thumbnail,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
                image=excluded.image,thumbnail=excluded.thumbnail,updated_at=excluded.updated_at''',
                (iid,wid,eid,b'' if external else full,b'' if external else thumb,now()))
        return {'ok':True}

    @app.delete('/api/w/{wid}/events/{eid}/registry/items/{iid}/image')
    def delete_item_image(request:Request,wid:str,eid:str,iid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if not con.execute('DELETE FROM gift_registry_images WHERE item_id=? AND event_id=? AND workspace_id=?',(iid,eid,wid)).rowcount:
                raise HTTPException(404,'Item image not found')
            cloud_media.delete(con,'gift_image',iid,wid,eid)
        return {'ok':True}

    @app.get('/api/w/{wid}/events/{eid}/registry/items/{iid}/image')
    def organizer_item_image(request:Request,wid:str,eid:str,iid:str):
        with db() as con:
            access(request,con,wid);check_event(con,wid,eid)
            row=con.execute('SELECT thumbnail FROM gift_registry_images WHERE item_id=? AND event_id=? AND workspace_id=?',(iid,eid,wid)).fetchone()
            if not row:raise HTTPException(404,'Image not found')
            body=bytes(row['thumbnail'])
            if not body:body=cloud_media.read(con,'gift_image',iid,'thumb',wid,eid)
            return Response(body,media_type='image/jpeg',headers={'Cache-Control':'private,max-age=60','X-Content-Type-Options':'nosniff'})

    @app.patch('/api/w/{wid}/events/{eid}/registry/items/{iid}')
    def change_item(request:Request,wid:str,eid:str,iid:str,payload:dict):
        if not isinstance(payload,dict) or not payload:
            raise HTTPException(422,'Invalid registry item')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            original=con.execute('SELECT * FROM gift_registry_items WHERE id=? AND event_id=? AND workspace_id=?',(iid,eid,wid)).fetchone()
            if not original:raise HTTPException(404,'Gift not found')
            full={key:original[key] for key in ('title','description','category','kind','quantity','price_cents','target_cents','link_url','image_url','priority','archived')}
            full['archived']=bool(full['archived'])
            full.update(payload)
            data=clean_item(full)
            if data['kind']!=original['kind']:
                count=con.execute("SELECT COUNT(*) FROM gift_reservations WHERE item_id=? AND status IN ('active','completed')",(iid,)).fetchone()[0]
                if count:raise HTTPException(409,'Cancel reservations before changing gift type')
            saved=next(x for x in item_rows(con,eid,True) if x['id']==iid)
            if data['kind']=='gift' and data['quantity']<saved['reserved_quantity']:
                raise HTTPException(409,'Quantity cannot be lower than existing reservations')
            if data['kind']=='fund' and data['target_cents']<saved['pledged_cents']:
                raise HTTPException(409,'Fund target cannot be below existing pledges')
            columns=list(data)
            con.execute('UPDATE gift_registry_items SET '+','.join(k+'=?' for k in columns)+
                ' WHERE id=? AND event_id=? AND workspace_id=?',(*data.values(),iid,eid,wid))
        return {'ok':True}

    @app.delete('/api/w/{wid}/events/{eid}/registry/items/{iid}')
    def delete_item(request:Request,wid:str,eid:str,iid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if not con.execute('DELETE FROM gift_registry_items WHERE id=? AND event_id=? AND workspace_id=?',(iid,eid,wid)).rowcount:
                raise HTTPException(404,'Gift not found')
        return {'ok':True}

    @app.patch('/api/w/{wid}/events/{eid}/registry/reservations/{rid}')
    def organizer_reservation(request:Request,wid:str,eid:str,rid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'status'} or payload['status'] not in ('active','completed','cancelled'):
            raise HTTPException(422,'Invalid reservation status')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            previous=con.execute('SELECT * FROM gift_reservations WHERE id=? AND event_id=?',(rid,eid)).fetchone()
            if not previous:raise HTTPException(404,'Reservation not found')
            # Cancelled reservations cannot be resumed without a fresh capacity check.
            if previous['status']=='cancelled' and payload['status']!='cancelled':
                raise HTTPException(409,'Ask the guest to make a new reservation')
            con.execute('UPDATE gift_reservations SET status=?,updated_at=? WHERE id=? AND event_id=?',
                (payload['status'],now(),rid,eid))
            return {'ok':True}

    @app.get('/api/w/{wid}/events/{eid}/registry/export.csv')
    def export_csv(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid);check_event(con,wid,eid)
            rows=con.execute('''SELECT i.title,i.kind,r.donor_name,r.donor_email,r.note,
              r.quantity,r.pledged_cents,r.status,r.created_at FROM gift_reservations r
              JOIN gift_registry_items i ON i.id=r.item_id
              WHERE r.event_id=? ORDER BY i.title,r.created_at''',(eid,)).fetchall()
            output=io.StringIO()
            w=csv.writer(output)
            w.writerow(['Item','Type','Reserved by','Contact email','Message','Quantity','Pledge (cents)','Status','Date'])
            w.writerows([[csv_cell(x) for x in row] for row in rows])
            return Response('\ufeff'+output.getvalue(),media_type='text/csv',
                headers={'Content-Disposition':'attachment; filename="eventos-gift-registry.csv"',
                         'Cache-Control':'no-store'})

    @app.get('/api/registry/{token}')
    def public_registry(token:str):
        with db() as con:
            r=guest_access(con,token)
            return {'event_name':r['event_name'],'title':r['title'],'intro':r['intro'],'currency':r['currency'],
                    'items':[dict(public_item(x),image_path='/api/registry/'+token+'/items/'+x['id']+'/image' if x['has_image'] else '') for x in item_rows(con,r['event_id'])]}

    @app.get('/api/registry/{token}/items/{iid}/image')
    def public_item_image(token:str,iid:str):
        with db() as con:
            reg=guest_access(con,token)
            row=con.execute('''SELECT m.thumbnail FROM gift_registry_images m
                JOIN gift_registry_items i ON i.id=m.item_id
                WHERE m.item_id=? AND i.event_id=? AND i.archived=0''',(iid,reg['event_id'])).fetchone()
            if not row:raise HTTPException(404,'Image not found')
            body=bytes(row['thumbnail'])
            if not body:body=cloud_media.read(con,'gift_image',iid,'thumb',reg['workspace_id'],reg['event_id'])
            return Response(body,media_type='image/jpeg',
                headers={'Cache-Control':'private,max-age=60','X-Content-Type-Options':'nosniff'})

    @app.post('/api/registry/{token}/reserve')
    def reserve(token:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{
            'item_id','donor_name','donor_email','note','quantity','pledged_cents'}:
            raise HTTPException(422,'Invalid gift reservation')
        iid=text_field(payload,'item_id',64,True)
        name=text_field(payload,'donor_name',100,True)
        email=text_field(payload,'donor_email',255)
        note=text_field(payload,'note',400)
        if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
            raise HTTPException(422,'Enter a valid email')
        qty=integer(payload,'quantity',1,1,100)
        amount=integer(payload,'pledged_cents',0,0,100_000_000)
        with db() as con:
            # Serialize claims to protect the remaining count under concurrent requests.
            con.execute('BEGIN IMMEDIATE')
            registry=guest_access(con,token)
            item=con.execute('SELECT * FROM gift_registry_items WHERE id=? AND event_id=? AND archived=0',
                (iid,registry['event_id'])).fetchone()
            if not item:raise HTTPException(404,'Gift not found')
            active=con.execute("""SELECT COALESCE(SUM(quantity),0),COALESCE(SUM(pledged_cents),0)
              FROM gift_reservations WHERE item_id=? AND status IN ('active','completed')""",(iid,)).fetchone()
            if item['kind']=='gift':
                if amount!=0 or qty>item['quantity']-active[0]:
                    raise HTTPException(409,'Not enough gifts are available to reserve')
            else:
                if qty!=1 or amount<100 or amount>item['target_cents']-active[1]:
                    raise HTTPException(409,'Pledge must be at least $1 and within the remaining goal')
            if con.execute("SELECT COUNT(*) FROM gift_reservations WHERE event_id=?",(registry['event_id'],)).fetchone()[0]>=MAX_RESERVATIONS:
                raise HTTPException(429,'Maximum reservations reached')
            # No reliable email verification yet. Cap repeated claims by name/email.
            previous=con.execute('''SELECT COUNT(*) FROM gift_reservations WHERE event_id=?
              AND lower(donor_name)=lower(?) AND lower(donor_email)=lower(?) AND status IN ('active','completed')''',
                (registry['event_id'],name,email)).fetchone()[0]
            if previous>=12:raise HTTPException(429,'Reservation limit reached')
            secret=secrets.token_urlsafe(30)
            rid=str(uuid.uuid4())
            con.execute('''INSERT INTO gift_reservations(id,event_id,item_id,donor_name,donor_email,
                note,quantity,pledged_cents,status,cancel_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid,registry['event_id'],iid,name,email,note,qty,amount,'active',
                 hashlib.sha256(secret.encode()).hexdigest(),now(),now()))
            return {'reservation_id':rid,'manage_code':secret,'status':'reserved' if item['kind']=='gift' else 'pledged',
                    'message':'Gift reserved. Purchase outside EventOS.' if item['kind']=='gift'
                        else 'Pledge recorded. No payment has been collected.'}

    @app.post('/api/registry/{token}/cancel')
    def cancel(token:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'reservation_id','manage_code'}:
            raise HTTPException(422,'Invalid cancellation request')
        rid=text_field(payload,'reservation_id',70,True)
        secret=text_field(payload,'manage_code',130,True)
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            reg=guest_access(con,token)
            row=con.execute('SELECT * FROM gift_reservations WHERE id=? AND event_id=?',
                (rid,reg['event_id'])).fetchone()
            if not row or not secrets.compare_digest(row['cancel_hash'],hashlib.sha256(secret.encode()).hexdigest()):
                raise HTTPException(404,'Reservation not found')
            if row['status']=='completed':
                raise HTTPException(409,'Contact the organizer to change a completed gift')
            if row['status']=='cancelled':
                raise HTTPException(409,'Already cancelled')
            con.execute("UPDATE gift_reservations SET status='cancelled',updated_at=? WHERE id=?",
                (now(),rid))
            return {'ok':True}

    @app.get('/gifts/{token}')
    def public_page(token:str):
        from pathlib import Path
        with db() as con:guest_access(con,token)
        return FileResponse(Path(__file__).parent/'static'/'registry_public.html',
            headers={'Cache-Control':'no-store'})

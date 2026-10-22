"""Phase 11: invitation-scoped guest hub, optional PIN-protected seat finder and curated event updates.

Private invitation tokens grant access to that guest's pass and seating only.
A venue-wide name finder is opt-in, high-entropy URL + PIN protected and rate-limited.
No contact details, private schedules, or other guests' names are exposed publicly.
"""
from __future__ import annotations
import hashlib
import hmac
import io
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import qrcode
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from saas_access import limits, feature_enabled
from seating_charts import ensure as ensure_floor


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def marker_config(con,wid,eid):
    ensure_floor(con,wid,eid)
    return [dict(r) for r in con.execute("""SELECT kind,label,pos_x,pos_y,width,height
       FROM event_floor_elements WHERE workspace_id=? AND event_id=? AND visible=1
       ORDER BY kind""",(wid,eid))]


def table_config(con,wid,eid):
    return [dict(r) for r in con.execute("""SELECT id,name,shape,pos_x,pos_y FROM event_tables
       WHERE workspace_id=? AND event_id=? ORDER BY name COLLATE NOCASE""",(wid,eid))]


def timetable(con,eid):
    return [dict(r) for r in con.execute("""SELECT id,title,start_at,location,description
        FROM guest_itinerary WHERE event_id=? AND published=1
        ORDER BY start_at,id LIMIT 100""",(eid,))]


def news(con,eid):
    return [dict(r) for r in con.execute("""SELECT id,title,body,category,created_at FROM guest_updates
        WHERE event_id=? AND published=1 ORDER BY created_at DESC,id DESC LIMIT 30""",(eid,))]


def digest_pin(pin,salt):
    return hashlib.scrypt(pin.encode('utf-8'),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()


def validate_pin(pin):
    if not isinstance(pin,str) or not re.fullmatch(r'[0-9]{6,12}',pin):
        raise HTTPException(422,'Use a 6–12 digit numeric access PIN')
    return pin


def clean_text(payload,key,maxlen,required=True):
    value=payload.get(key)
    if not isinstance(value,str) or len(value)>maxlen or (required and not value.strip()) or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise HTTPException(422,'Invalid '+key.replace('_',' '))
    return value.strip()


def guest_access(con,token):
    if not isinstance(token,str) or len(token)>150:
        raise HTTPException(404,'Invitation not found')
    guest=con.execute("""SELECT g.id,g.event_id,g.workspace_id,g.name,g.rsvp,g.party_size,
       e.name event_name,e.start_date,e.end_date,e.location,e.status event_status
       FROM guests g JOIN events e ON e.id=g.event_id WHERE g.invitation_token=?""",(token,)).fetchone()
    if not guest:raise HTTPException(404,'Invitation not found')
    limits(con,guest['workspace_id'])
    if not feature_enabled(con,guest['workspace_id'],'operations'):
        raise HTTPException(404,'Guest experience unavailable')
    return dict(guest)


def guest_seat(con,guest):
    table=con.execute("""SELECT t.id,t.name,t.shape,t.pos_x,t.pos_y
       FROM event_seating s JOIN event_tables t ON t.id=s.table_id
       WHERE s.guest_id=? AND t.workspace_id=? AND t.event_id=?""",
       (guest['id'],guest['workspace_id'],guest['event_id'])).fetchone()
    return dict(table) if guest['rsvp']=='Confirmed' and table else None


def public_event(con,guest):
    wid,eid=guest['workspace_id'],guest['event_id']
    checkin=con.execute('SELECT checked_in_at FROM event_checkins WHERE guest_id=?',(guest['id'],)).fetchone()
    return {'event':{'name':guest['event_name'],'start_date':guest['start_date'],
                     'end_date':guest['end_date'],'location':guest['location']},
            'invitation':{'name':guest['name'],'rsvp':guest['rsvp'],'party_size':guest['party_size'],
                          'checked_in':bool(checkin)},
            'seat':guest_seat(con,guest),
            'floor':{'tables':table_config(con,wid,eid),'markers':marker_config(con,wid,eid)},
            'itinerary':timetable(con,eid),'updates':news(con,eid)}


def migrate(db):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS guest_event_settings(
          event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,
          finder_enabled INTEGER NOT NULL DEFAULT 0,
          finder_token TEXT NOT NULL UNIQUE,
          pin_salt TEXT NOT NULL DEFAULT '',
          pin_digest TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guest_itinerary(
          id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,title TEXT NOT NULL,start_at TEXT NOT NULL,
          location TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
          published INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_guest_itinerary_event ON guest_itinerary(event_id,start_at);
        CREATE TABLE IF NOT EXISTS guest_updates(
          id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'info',
          published INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_guest_updates_event ON guest_updates(event_id,created_at);
        CREATE TABLE IF NOT EXISTS seat_finder_attempts(
          id TEXT PRIMARY KEY,event_id TEXT NOT NULL,
          address_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_seat_finder_limit ON seat_finder_attempts(event_id,address_hash,created_at);
        """)


def settings(con,wid,eid):
    row=con.execute('SELECT * FROM guest_event_settings WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
    if not row:
        con.execute("""INSERT INTO guest_event_settings
             (event_id,workspace_id,finder_enabled,finder_token,updated_at)
             VALUES(?,?,0,?,?)""",(eid,wid,secrets.token_urlsafe(32),timestamp()))
        row=con.execute('SELECT * FROM guest_event_settings WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
    return dict(row)


def host_url(request):
    configured=os.getenv('EVENTOS_PUBLIC_BASE_URL','').strip().rstrip('/')
    return configured if configured.startswith('https://') else str(request.base_url).rstrip('/')


def qr_png(url):
    image=qrcode.make(url)
    buffer=io.BytesIO()
    image.save(buffer,format='PNG')
    return buffer.getvalue()


def install(app,db,access,check_event):
    migrate(db)

    @app.get('/api/w/{wid}/events/{eid}/guest-experience')
    def organizer_overview(request:Request,wid:str,eid:str):
        with db() as con:
            role=access(request,con,wid)
            e=check_event(con,wid,eid)
            s=settings(con,wid,eid)
            items=[dict(x) for x in con.execute("""SELECT id,title,start_at,location,description,published
                 FROM guest_itinerary WHERE workspace_id=? AND event_id=? ORDER BY start_at,id""",(wid,eid))]
            updates=[dict(x) for x in con.execute("""SELECT id,title,body,category,published,created_at
                 FROM guest_updates WHERE workspace_id=? AND event_id=? ORDER BY created_at DESC,id DESC""",(wid,eid))]
            return {'event_name':e['name'],'can_edit':role['role'] in ('owner','editor'),
                    'finder_enabled':bool(s['finder_enabled']),
                    'finder_pin_set':bool(s['pin_digest']),
                    'finder_url':'/seat-finder/'+s['finder_token'] if s['finder_enabled'] else '',
                    'itinerary':items,'updates':updates}

    @app.put('/api/w/{wid}/events/{eid}/guest-experience/finder')
    def configure_finder(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{'enabled','pin','rotate'} or type(payload.get('enabled')) is not bool:
            raise HTTPException(422,'Enter enabled status and optional PIN')
        enabled=payload['enabled'];rotate=payload.get('rotate',False)
        if type(rotate) is not bool:raise HTTPException(422,'Invalid rotation option')
        new_pin=payload.get('pin')
        if new_pin is not None:validate_pin(new_pin)
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            old=settings(con,wid,eid)
            if enabled and not (new_pin or old['pin_digest']):
                raise HTTPException(422,'Set an access PIN before enabling the finder')
            salt=secrets.token_hex(16) if new_pin else old['pin_salt']
            hash_value=digest_pin(new_pin,salt) if new_pin else old['pin_digest']
            new_token=secrets.token_urlsafe(32) if rotate else old['finder_token']
            con.execute("""UPDATE guest_event_settings SET finder_enabled=?,pin_salt=?,pin_digest=?,
                 finder_token=?,updated_at=? WHERE event_id=? AND workspace_id=?""",
                 (int(enabled),salt,hash_value,new_token,timestamp(),eid,wid))
            return {'enabled':enabled,'finder_url':'/seat-finder/'+new_token if enabled else '',
                    'pin_set':bool(hash_value),'rotated':rotate}

    @app.get('/api/w/{wid}/events/{eid}/guest-experience/finder-qr.png')
    def qr_finder(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid)
            check_event(con,wid,eid)
            s=settings(con,wid,eid)
            if not s['finder_enabled'] or not s['pin_digest']:
                raise HTTPException(409,'Enable the guest finder before generating its QR sign')
        return Response(qr_png(host_url(request)+'/seat-finder/'+quote(s['finder_token'],safe='')),
                        media_type='image/png',headers={'Cache-Control':'private, no-store'})

    @app.get('/api/w/{wid}/events/{eid}/guest-experience/finder-sign.pdf')
    def venue_finder_sign(request:Request,wid:str,eid:str):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.colors import HexColor
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase.pdfmetrics import stringWidth
        with db() as con:
            access(request,con,wid);event=check_event(con,wid,eid)
            s=settings(con,wid,eid)
            if not s['finder_enabled'] or not s['pin_digest']:
                raise HTTPException(409,'Enable the venue name finder before downloading signage')
            name=event['name']
        image=qr_png(host_url(request)+'/seat-finder/'+quote(s['finder_token'],safe=''))
        out=io.BytesIO()
        W,H=A4
        document=canvas.Canvas(out,pagesize=A4,pageCompression=1)
        document.setTitle('Find your table — '+name)
        green=HexColor('#285844')
        document.setFillColor(HexColor('#f5f8f0'));document.rect(0,0,W,H,fill=1,stroke=0)
        document.setFillColor(green);document.roundRect(35,H-180,W-70,145,18,fill=1,stroke=0)
        document.setFillColor(HexColor('#dceadd'));document.setFont('Helvetica-Bold',12)
        document.drawCentredString(W/2,H-80,'WELCOME TO')
        title=str(name).encode('latin-1','replace').decode('latin-1')
        while len(title)>5 and stringWidth(title,'Helvetica-Bold',23)>W-95:title=title[:-2]+'…'
        document.setFont('Helvetica-Bold',23)
        document.setFillColor(HexColor('#ffffff'))
        document.drawCentredString(W/2,H-120,title)
        document.setFont('Helvetica-Bold',25);document.setFillColor(green)
        document.drawCentredString(W/2,H-235,'FIND YOUR SEAT')
        document.setFillColor(HexColor('#ffffff'));document.roundRect(W/2-151,H-558,302,302,15,fill=1,stroke=0)
        document.drawImage(ImageReader(io.BytesIO(image)),W/2-139,H-546,width=278,height=278,mask='auto')
        document.setFillColor(green);document.setFont('Helvetica-Bold',14)
        document.drawCentredString(W/2,H-594,'1. SCAN THE QR CODE')
        document.setFont('Helvetica',11);document.setFillColor(HexColor('#536e60'))
        document.drawCentredString(W/2,H-620,'2. Enter the full name on your invitation')
        document.drawCentredString(W/2,H-645,'3. Enter the access PIN provided by your host')
        document.setStrokeColor(HexColor('#ccdacc'))
        document.line(52,123,W-52,123)
        document.setFont('Helvetica',11)
        document.drawCentredString(W/2,99,'Cannot find your name? Ask our welcome desk for assistance.')
        document.setFont('Helvetica',9)
        document.drawCentredString(W/2,72,'The venue PIN is supplied separately and is never printed on this sign.')
        document.setFillColor(HexColor('#788f7f'));document.drawCentredString(W/2,40,'EventOS  •  Guest wayfinding')
        document.save()
        return Response(out.getvalue(),media_type='application/pdf',headers={
            'Content-Disposition':'attachment; filename="eventos-find-my-seat.pdf"',
            'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/api/w/{wid}/events/{eid}/guest-experience/itinerary')
    def add_itinerary(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{'title','start_at','location','description','published'}:
            raise HTTPException(422,'Invalid itinerary item')
        title=clean_text(payload,'title',120)
        start=clean_text(payload,'start_at',32)
        if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d',start):
            raise HTTPException(422,'Use local YYYY-MM-DDTHH:MM event time')
        try:datetime.strptime(start,'%Y-%m-%dT%H:%M')
        except ValueError:raise HTTPException(422,'Invalid itinerary date/time')
        location=clean_text(payload,'location',160,False)
        description=clean_text(payload,'description',600,False)
        published=payload.get('published',False)
        if type(published) is not bool:raise HTTPException(422,'Invalid publishing status')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if con.execute('SELECT COUNT(*) FROM guest_itinerary WHERE event_id=?',(eid,)).fetchone()[0]>=100:
                raise HTTPException(409,'Maximum 100 itinerary items')
            iid=str(uuid.uuid4())
            con.execute("""INSERT INTO guest_itinerary VALUES(?,?,?,?,?,?,?,?,?)""",
                 (iid,eid,wid,title,start,location,description,int(published),timestamp()))
            return {'id':iid,'title':title,'start_at':start,'location':location,
                    'description':description,'published':published}

    @app.patch('/api/w/{wid}/events/{eid}/guest-experience/itinerary/{iid}')
    def publish_itinerary(request:Request,wid:str,eid:str,iid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'published'} or type(payload['published']) is not bool:
            raise HTTPException(422,'Only itinerary publication status can be changed')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            n=con.execute("""UPDATE guest_itinerary SET published=?
                 WHERE id=? AND workspace_id=? AND event_id=?""",
                 (int(payload['published']),iid,wid,eid)).rowcount
            if not n:raise HTTPException(404,'Itinerary item not found')
            return {'ok':True}

    @app.delete('/api/w/{wid}/events/{eid}/guest-experience/itinerary/{iid}')
    def delete_itinerary(request:Request,wid:str,eid:str,iid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            n=con.execute("""DELETE FROM guest_itinerary WHERE id=? AND workspace_id=? AND event_id=?""",
                          (iid,wid,eid)).rowcount
            if not n:raise HTTPException(404,'Itinerary item not found')
            return {'ok':True}

    @app.post('/api/w/{wid}/events/{eid}/guest-experience/updates')
    def add_update(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{'title','body','category','published'}:
            raise HTTPException(422,'Invalid event update')
        title=clean_text(payload,'title',120)
        body=clean_text(payload,'body',1000)
        category=payload.get('category','info')
        if category not in ('info','important','transport'):raise HTTPException(422,'Invalid update category')
        published=payload.get('published',False)
        if type(published) is not bool:raise HTTPException(422,'Invalid publishing status')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if con.execute('SELECT COUNT(*) FROM guest_updates WHERE event_id=?',(eid,)).fetchone()[0]>=100:
                raise HTTPException(409,'Maximum 100 guest notices')
            uid=str(uuid.uuid4())
            con.execute('INSERT INTO guest_updates VALUES(?,?,?,?,?,?,?,?)',
                (uid,eid,wid,title,body,category,int(published),timestamp()))
            return {'id':uid,'title':title,'body':body,'category':category,'published':published}

    @app.patch('/api/w/{wid}/events/{eid}/guest-experience/updates/{uid}')
    def publish_update(request:Request,wid:str,eid:str,uid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'published'} or type(payload['published']) is not bool:
            raise HTTPException(422,'Only notice publication status can be changed')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            n=con.execute("""UPDATE guest_updates SET published=?
                WHERE id=? AND workspace_id=? AND event_id=?""",
                (int(payload['published']),uid,wid,eid)).rowcount
            if not n:raise HTTPException(404,'Guest notice not found')
            return {'ok':True}

    @app.delete('/api/w/{wid}/events/{eid}/guest-experience/updates/{uid}')
    def delete_update(request:Request,wid:str,eid:str,uid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            n=con.execute('DELETE FROM guest_updates WHERE id=? AND event_id=? AND workspace_id=?',(uid,eid,wid)).rowcount
            if not n:raise HTTPException(404,'Guest notice not found')
            return {'ok':True}

    @app.get('/api/guest/{token}')
    def guest_portal(token:str):
        with db() as con:
            guest=guest_access(con,token)
            return public_event(con,guest)

    @app.get('/api/guest/{token}/calendar.ics')
    def guest_calendar(token:str):
        with db() as con:
            guest=guest_access(con,token)
            date=(guest['start_date'] or '')[:10]
            if not re.fullmatch(r'\d{4}-\d\d-\d\d',date):raise HTTPException(409,'Event date is not configured')
            day=datetime.strptime(date,'%Y-%m-%d').date()
            end=day+timedelta(days=1)
            def safe(x):return str(x or '').replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n').replace('\r','')
            lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//EventOS//Guest Experience//EN',
                'BEGIN:VEVENT','UID:'+guest['id']+'@eventos.example',
                'DTSTAMP:'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                'DTSTART;VALUE=DATE:'+day.strftime('%Y%m%d'),
                'DTEND;VALUE=DATE:'+end.strftime('%Y%m%d'),
                'SUMMARY:'+safe(guest['event_name']),'LOCATION:'+safe(guest['location']),
                'DESCRIPTION:Event invitation. Visit your private guest link for event times and seating.',
                'END:VEVENT','END:VCALENDAR','']
        return Response('\r\n'.join(lines),media_type='text/calendar',
            headers={'Content-Disposition':'attachment; filename="eventos-event.ics"',
                     'Cache-Control':'private, no-store'})

    @app.get('/guest/{token}')
    def guest_page(token:str):
        return FileResponse(os.path.join(os.path.dirname(__file__),'static','guest_experience.html'),
                            headers={'Cache-Control':'no-store'})

    def finder_session(con,token):
        if not isinstance(token,str) or len(token)>150:raise HTTPException(404,'Finder not available')
        s=con.execute('SELECT * FROM guest_event_settings WHERE finder_token=?',(token,)).fetchone()
        if not s or not s['finder_enabled'] or not s['pin_digest']:
            raise HTTPException(404,'Finder not available')
        limits(con,s['workspace_id'])
        if not feature_enabled(con,s['workspace_id'],'operations'):
            raise HTTPException(404,'Finder not available')
        event=con.execute('SELECT name,location FROM events WHERE id=? AND workspace_id=?',
                          (s['event_id'],s['workspace_id'])).fetchone()
        if not event:raise HTTPException(404,'Finder not available')
        return dict(s),dict(event)

    @app.get('/api/seat-finder/{token}/info')
    def seat_finder_info(token:str):
        with db() as con:
            settings_row,event=finder_session(con,token)
            return {'event_name':event['name'],'location':event['location']}

    @app.post('/api/seat-finder/{token}/lookup')
    def seat_finder_lookup(request:Request,token:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'name','pin'}:
            raise HTTPException(422,'Enter your full invitation name and venue PIN')
        name=clean_text(payload,'name',160)
        if len(name)<3:raise HTTPException(422,'Enter the full name on your invitation')
        # All requests consume a per-token/IP slot, including incorrect names.
        pin=payload.get('pin')
        if not isinstance(pin,str) or len(pin)>32:raise HTTPException(422,'Invalid PIN')
        with db() as con:
            s,event=finder_session(con,token)
            ip=request.client.host if request.client else 'unknown'
            address_hash=hashlib.sha256((s['event_id']+'|'+ip).encode()).hexdigest()
            cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(timespec='seconds')
            con.execute('DELETE FROM seat_finder_attempts WHERE created_at<?',(cutoff,))
            count=con.execute("""SELECT COUNT(*) FROM seat_finder_attempts WHERE event_id=?
                AND address_hash=? AND created_at>=?""",(s['event_id'],address_hash,cutoff)).fetchone()[0]
            if count>=12:raise HTTPException(429,'Too many searches. Ask the welcome desk for help.')
            con.execute('INSERT INTO seat_finder_attempts VALUES(?,?,?,?)',
                        (str(uuid.uuid4()),s['event_id'],address_hash,timestamp()))
            # Commit every lookup attempt even when the supplied PIN/name is wrong.
            # Raising inside the transaction would roll back the counter.
            accepted=hmac.compare_digest(digest_pin(pin,s['pin_salt']),s['pin_digest'])
            # Match the primary invitation OR individual names the party chose to
            # provide in RSVP. A non-unique name must be resolved by venue staff.
            import production_mariadb
            sqlite_guest_clause="""OR EXISTS(SELECT 1 FROM json_each(COALESCE(r.party_json,'{}'),'$.guests') j
                      WHERE lower(trim(json_extract(j.value,'$.name')))=lower(?))"""
            maria_guest_clause="""OR EXISTS(SELECT 1 FROM JSON_TABLE(COALESCE(r.party_json,'{}'),'$.guests[*]'
                      COLUMNS(guest_name VARCHAR(255) PATH '$.name')) AS j
                      WHERE lower(trim(j.guest_name))=lower(?))"""
            guest_clause=maria_guest_clause if production_mariadb.enabled() else sqlite_guest_clause
            matches=con.execute("""SELECT g.id,g.name FROM guests g
                LEFT JOIN rsvp_responses r ON r.guest_id=g.id
                WHERE g.event_id=? AND g.workspace_id=? AND g.rsvp='Confirmed'
                  AND (lower(trim(g.name))=lower(?)
                    {guest_clause})
                LIMIT 2""".replace('{guest_clause}',guest_clause),(s['event_id'],s['workspace_id'],name,name)).fetchall() if accepted else []
            found=dict(matches[0]) if len(matches)==1 else None
            seat=guest_seat(con,{'id':found['id'],'rsvp':'Confirmed',
                                 'workspace_id':s['workspace_id'],'event_id':s['event_id']}) if found else None
            floor={'tables':table_config(con,s['workspace_id'],s['event_id']),
                   'markers':marker_config(con,s['workspace_id'],s['event_id'])} if found else None
            event_name=event['name']
        if not found:
            raise HTTPException(404,'No seating assignment found. Check the full invitation name and PIN or ask the welcome desk.')
        return {'table':seat,'floor':floor,'event_name':event_name,'assigned':bool(seat)}

    @app.get('/seat-finder/{token}')
    def seat_finder_page(token:str):
        return FileResponse(os.path.join(os.path.dirname(__file__),'static','guest_experience.html'),
                            headers={'Cache-Control':'no-store'})

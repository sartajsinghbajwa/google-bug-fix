"""EventOS communications, seating, staff check-in and operations reporting.

All organizer mutations enforce workspace membership. RSVP bearer tokens expose only
a check-in QR (never a write capability). External emails are opt-in: sending needs
SMTP settings and an explicit organizer request or configured authenticated job.
"""
from __future__ import annotations

import csv
import io
import os
import re
import secrets
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

import qrcode
from saas_access import enforce as saas_enforce
from seating_charts import install as install_seating_charts, ensure as ensure_floor
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse


def utcnow():
    return datetime.now(timezone.utc)


def time_text():
    return utcnow().isoformat(timespec='seconds')


def valid_future(value):
    if not isinstance(value, str):
        raise HTTPException(422, 'Use an ISO date and time with timezone')
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(422, 'Invalid scheduled date')
    if value.tzinfo is None or value <= utcnow():
        raise HTTPException(422, 'Scheduled time must be in the future and include a timezone')
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')


def plain(value, limit, label):
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise HTTPException(422, 'Invalid ' + label)
    return value.strip()


def mail_enabled():
    return all(os.getenv(k) for k in ('EVENTOS_SMTP_HOST','EVENTOS_SMTP_USER','EVENTOS_SMTP_PASSWORD','EVENTOS_SMTP_FROM'))


def send_email(recipient, subject, body, *, display_name=None, reply_to=None):
    if not mail_enabled():
        raise RuntimeError('SMTP delivery is not configured')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', recipient or ''):
        raise ValueError('Guest email is missing or invalid')
    message = EmailMessage()
    message['To'] = recipient
    sender=os.environ['EVENTOS_SMTP_FROM']
    if display_name:
        if not isinstance(display_name,str) or len(display_name)>100 or any(c in display_name for c in '\r\n'):
            raise ValueError('Invalid sender display name')
        # The authenticated From address is not replaced by an unverified workspace domain.
        if '<' in sender and sender.endswith('>'):
            sender=sender[sender.rfind('<')+1:-1]
        message['From']=formataddr((display_name,sender))
    else:
        message['From']=sender
    if reply_to:
        if not isinstance(reply_to,str) or not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+',reply_to):
            raise ValueError('Invalid reply-to address')
        message['Reply-To']=reply_to
    message['Subject'] = subject
    message.set_content(body)
    host = os.environ['EVENTOS_SMTP_HOST']
    port = int(os.getenv('EVENTOS_SMTP_PORT', '465'))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context()) as server:
            server.login(os.environ['EVENTOS_SMTP_USER'], os.environ['EVENTOS_SMTP_PASSWORD'])
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=12) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(os.environ['EVENTOS_SMTP_USER'], os.environ['EVENTOS_SMTP_PASSWORD'])
            server.send_message(message)


def migrate(db):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS event_tables (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          name TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 500),
          UNIQUE(event_id,name));
        CREATE TABLE IF NOT EXISTS event_seating (
          guest_id TEXT PRIMARY KEY REFERENCES guests(id) ON DELETE CASCADE,
          table_id TEXT NOT NULL REFERENCES event_tables(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS event_checkin_codes (
          guest_id TEXT PRIMARY KEY REFERENCES guests(id) ON DELETE CASCADE,
          code TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS event_checkins (
          guest_id TEXT PRIMARY KEY REFERENCES guests(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL, event_id TEXT NOT NULL,
          attendees INTEGER NOT NULL, checked_in_at TEXT NOT NULL, staff_id TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_event_checkins ON event_checkins(workspace_id,event_id);
        CREATE TABLE IF NOT EXISTS event_messages (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          guest_id TEXT NOT NULL REFERENCES guests(id) ON DELETE CASCADE,
          channel TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
          status TEXT NOT NULL, scheduled_at TEXT, sent_at TEXT, error TEXT DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_event_messages_due ON event_messages(status,scheduled_at);
        """)
        columns={x['name'] for x in con.execute("PRAGMA table_info(event_tables)")}
        for key,ddl in [('pos_x','REAL NOT NULL DEFAULT 50'),('pos_y','REAL NOT NULL DEFAULT 50'),('shape',"TEXT NOT NULL DEFAULT 'round'")]:
            if key not in columns:
                con.execute("ALTER TABLE event_tables ADD COLUMN "+key+" "+ddl)


def qr_png(url):
    img=qrcode.make(url)
    out=io.BytesIO()
    img.save(out,format='PNG')
    return out.getvalue()


def details(con, wid, eid):
    guests=con.execute("""SELECT g.id,g.name,g.email,g.phone,g.party_size,g.rsvp,g.invitation_token,
        g.group_name,g.category,r.party_json, t.id table_id,t.name table_name,t.capacity,
        c.checked_in_at,c.attendees checked_in_count, m.code checkin_code
        FROM guests g
        LEFT JOIN rsvp_responses r ON r.guest_id=g.id
        LEFT JOIN event_seating s ON s.guest_id=g.id
        LEFT JOIN event_tables t ON t.id=s.table_id
        LEFT JOIN event_checkins c ON c.guest_id=g.id
        LEFT JOIN event_checkin_codes m ON m.guest_id=g.id
        WHERE g.workspace_id=? AND g.event_id=? ORDER BY g.name COLLATE NOCASE""",(wid,eid)).fetchall()
    import json
    result=[]
    for g in guests:
        v=dict(g)
        try: party=json.loads(v.pop('party_json') or '{}')
        except (ValueError,TypeError): party={}
        v['attending']=int(party.get('total') or v['party_size']) if v['rsvp']=='Confirmed' else 0
        v['seats']=v['attending'] if v['rsvp']=='Confirmed' else int(v['party_size'])
        v['attendee_names']=[str(item.get('name','')).strip() for item in party.get('guests',[])
                             if isinstance(item,dict) and isinstance(item.get('name'),str) and item['name'].strip()][:100]
        result.append(v)
    return result


def table_report(con,wid,eid,guests):
    items=[dict(x) for x in con.execute('SELECT id,name,capacity,pos_x,pos_y,shape FROM event_tables WHERE workspace_id=? AND event_id=? ORDER BY name COLLATE NOCASE',(wid,eid))]
    for item in items:
        sitting=[g for g in guests if g['table_id']==item['id']]
        item['allocated']=sum(g['seats'] for g in sitting)
        item['guest_count']=len(sitting)
    return items


def redacted_error(exc):
    # Never expose SMTP credentials, host names, raw TLS errors or user data.
    return 'Delivery failed; check SMTP credentials, recipient and provider logs'


def render_message(guest,event,kind,custom_subject='',custom_body='',origin=''):
    name=guest['name']
    event_name=event['name']
    url=origin+'/rsvp/'+quote(guest['invitation_token'],safe='')
    date=event['start_date'][:10]
    subjects={'invitation':'Invitation to {event}','reminder':'RSVP reminder for {event}',
              'announcement':'Update for {event}','confirmation':'RSVP confirmed for {event}'}
    bodies={
        'invitation':'Hello {guest},\n\nYou are invited to {event} on {date}.\nPlease RSVP using your private invitation: {link}\n',
        'reminder':'Hello {guest},\n\nA friendly reminder to reply for {event}.\nYour RSVP link: {link}\n',
        'announcement':'Hello {guest},\n\nThere is an event update for {event}.\nYou can view your invitation here: {link}\n',
        'confirmation':'Hello {guest},\n\nThank you for your RSVP to {event}.\nYour invitation: {link}\n',
    }
    mapping={'guest':name,'event':event_name,'date':date,'link':url}
    def substitute(template):
        for key,value in mapping.items():
            template=template.replace('{'+key+'}',value)
        return template
    return substitute(custom_subject or subjects[kind]),substitute(custom_body or bodies[kind])


def install_operations(app,db,access,check_event,get_user,uid):
    migrate(db)
    install_seating_charts(app,db,access,check_event,details,table_report)

    def guest_in(con,wid,eid,gid):
        g=con.execute('SELECT * FROM guests WHERE id=? AND workspace_id=? AND event_id=?',(gid,wid,eid)).fetchone()
        if not g:
            raise HTTPException(404,'Guest not found in this event')
        return dict(g)

    def code_for(con,gid):
        row=con.execute('SELECT code FROM event_checkin_codes WHERE guest_id=?',(gid,)).fetchone()
        if row:return row['code']
        code=secrets.token_urlsafe(24)
        con.execute('INSERT INTO event_checkin_codes(guest_id,code) VALUES (?,?)',(gid,code))
        return code

    def host_url(request):
        # Request base_url is generated by the app / trusted proxy; no arbitrary
        # user-injected address is accepted for outbound links.
        override=os.getenv('EVENTOS_PUBLIC_BASE_URL','').strip().rstrip('/')
        if override.startswith('https://'):return override
        return str(request.base_url).rstrip('/')

    @app.get('/api/w/{wid}/events/{eid}/operations')
    def overview(request:Request,wid:str,eid:str):
        with db() as con:
            role=access(request,con,wid)
            event=check_event(con,wid,eid)
            gs=details(con,wid,eid)
            tables=table_report(con,wid,eid,gs)
            msg=con.execute("""SELECT status,COUNT(*) AS n FROM event_messages
                WHERE workspace_id=? AND event_id=? GROUP BY status""",(wid,eid)).fetchall()
            checkins=sum(g['checked_in_count'] or 0 for g in gs)
            confirmed=sum(g['attending'] for g in gs)
            return {'event':dict(event),'role':role['role'],'email_configured':mail_enabled(),
                    'cron_configured':bool(os.getenv('EVENTOS_CRON_TOKEN')),
                    'guests':gs,'tables':tables,'floor_elements':ensure_floor(con,wid,eid),
                    'metrics':{'invitations':len(gs),'pending':sum(g['rsvp']=='Pending' for g in gs),
                               'confirmed_attendees':confirmed,'checked_in':checkins,
                               'remaining':max(0,confirmed-checkins),
                               'seated':sum(g['seats'] for g in gs if g['table_id']),
                               'messages':{x['status']:x['n'] for x in msg}}}

    @app.post('/api/w/{wid}/events/{eid}/tables')
    def add_table(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'name','capacity'}:
            raise HTTPException(422,'Provide table name and capacity')
        name=plain(payload['name'],80,'table name')
        cap=payload['capacity']
        if not name or type(cap) is not int or not 1<=cap<=500:
            raise HTTPException(422,'Capacity must be from 1 to 500')
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            if con.execute('SELECT COUNT(*) FROM event_tables WHERE event_id=?',(eid,)).fetchone()[0]>=100:
                raise HTTPException(422,'Maximum 100 tables per event')
            if con.execute('SELECT 1 FROM event_tables WHERE event_id=? AND name=?',(eid,name)).fetchone():
                raise HTTPException(409,'Table name already exists')
            tid=uid()
            count=con.execute('SELECT COUNT(*) FROM event_tables WHERE event_id=?',(eid,)).fetchone()[0]
            x=round(15+(count%5)*17,2)
            y=round(18+(count//5%5)*18,2)
            con.execute('INSERT INTO event_tables(id,workspace_id,event_id,name,capacity,pos_x,pos_y,shape) VALUES (?,?,?,?,?,?,?,?)',(tid,wid,eid,name,cap,x,y,'round'))
            return {'id':tid,'name':name,'capacity':cap,'pos_x':x,'pos_y':y,'shape':'round'}

    @app.put('/api/w/{wid}/events/{eid}/tables/{tid}/layout')
    def table_layout(request:Request,wid:str,eid:str,tid:str,payload:dict):
        import math
        if not isinstance(payload,dict) or set(payload)!={'x','y','shape'}:
            raise HTTPException(422,'Expected table coordinates and shape')
        x,y,shape=payload['x'],payload['y'],payload['shape']
        if any(type(n) not in (float,int) or not math.isfinite(n) or not 6<=n<=94 for n in (x,y)) or shape not in ('round','rectangle'):
            raise HTTPException(422,'Invalid table geometry')
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            changed=con.execute('UPDATE event_tables SET pos_x=?,pos_y=?,shape=? WHERE id=? AND workspace_id=? AND event_id=?',(round(x,2),round(y,2),shape,tid,wid,eid)).rowcount
            if not changed:raise HTTPException(404,'Table not found')
        return {'id':tid,'pos_x':round(x,2),'pos_y':round(y,2),'shape':shape}

    @app.delete('/api/w/{wid}/events/{eid}/tables/{tid}')
    def remove_table(request:Request,wid:str,eid:str,tid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            found=con.execute('SELECT id FROM event_tables WHERE id=? AND workspace_id=? AND event_id=?',(tid,wid,eid)).fetchone()
            if not found:raise HTTPException(404,'Table not found')
            con.execute('DELETE FROM event_tables WHERE id=?',(tid,))
        return {'ok':True}

    @app.put('/api/w/{wid}/events/{eid}/seating/{gid}')
    def assign(request:Request,wid:str,eid:str,gid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'table_id'} or (payload['table_id'] is not None and not isinstance(payload['table_id'],str)):
            raise HTTPException(422,'Choose a table or clear the assignment')
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            guest_in(con,wid,eid,gid)
            tid=payload['table_id']
            if tid is None:
                con.execute('DELETE FROM event_seating WHERE guest_id=?',(gid,))
                return {'ok':True,'table_id':None}
            table=con.execute('SELECT * FROM event_tables WHERE id=? AND workspace_id=? AND event_id=?',(tid,wid,eid)).fetchone()
            if not table:raise HTTPException(404,'Table not found')
            gs=details(con,wid,eid)
            available=table['capacity']-sum(g['seats'] for g in gs if g['table_id']==tid and g['id']!=gid)
            needed=next(g['seats'] for g in gs if g['id']==gid)
            if needed>available:raise HTTPException(409,f'Only {max(0,available)} seats remain at this table')
            con.execute('INSERT INTO event_seating VALUES (?,?) ON CONFLICT(guest_id) DO UPDATE SET table_id=excluded.table_id',(gid,tid))
            return {'ok':True,'table_id':tid}

    @app.get('/api/rsvp/{token}/checkin-qr.png')
    def guest_qr(request:Request,token:str):
        with db() as con:
            g=con.execute('SELECT id,rsvp FROM guests WHERE invitation_token=?',(token,)).fetchone()
            if not g:raise HTTPException(404,'Invitation not found')
            if g['rsvp']!='Confirmed':raise HTTPException(409,'Check-in pass is available after confirming your RSVP')
            code=code_for(con,g['id'])
        return Response(qr_png(host_url(request)+'/checkin/'+quote(code,safe='')),
                        media_type='image/png',headers={'Cache-Control':'no-store'})

    @app.get('/api/w/{wid}/events/{eid}/guests/{gid}/checkin-qr.png')
    def staff_qr(request:Request,wid:str,eid:str,gid:str):
        with db() as con:
            access(request,con,wid)
            check_event(con,wid,eid)
            guest_in(con,wid,eid,gid)
            code=code_for(con,gid)
        return Response(qr_png(host_url(request)+'/checkin/'+quote(code,safe='')),
                        media_type='image/png',headers={'Cache-Control':'no-store'})

    def from_code(con,code):
        if not isinstance(code,str) or len(code)>100:
            raise HTTPException(404,'Pass not found')
        r=con.execute('''SELECT g.*,e.name event_name,e.start_date,
                    c.checked_in_at,c.attendees checked_in_count
                    FROM event_checkin_codes x JOIN guests g ON g.id=x.guest_id
                    JOIN events e ON e.id=g.event_id
                    LEFT JOIN event_checkins c ON c.guest_id=g.id
                    WHERE x.code=?''',(code,)).fetchone()
        if not r:raise HTTPException(404,'Pass not found')
        return dict(r)

    @app.get('/api/checkin/{code}')
    def checkin_lookup(request:Request,code:str):
        with db() as con:
            guest=from_code(con,code)
            access(request,con,guest['workspace_id'])
            party=next(x for x in details(con,guest['workspace_id'],guest['event_id']) if x['id']==guest['id'])
            return {'guest_id':guest['id'],'guest_name':guest['name'],
                    'event_name':guest['event_name'],'rsvp':guest['rsvp'],
                    'attending':party['attending'],'checked_in_at':guest['checked_in_at'],
                    'checked_in_count':guest['checked_in_count']}

    @app.post('/api/checkin/{code}')
    def checkin(request:Request,code:str):
        with db() as con:
            guest=from_code(con,code)
            access(request,con,guest['workspace_id'],True)
            who=get_user(request,con)
            if guest['rsvp']!='Confirmed':
                raise HTTPException(409,'Only confirmed invitations may check in')
            if guest['checked_in_at']:
                raise HTTPException(409,'Invitation already checked in')
            party=next(x for x in details(con,guest['workspace_id'],guest['event_id']) if x['id']==guest['id'])
            attending=party['attending']
            con.execute('INSERT INTO event_checkins VALUES (?,?,?,?,?,?)',(
                guest['id'],guest['workspace_id'],guest['event_id'],attending,time_text(),who['id']))
            return {'ok':True,'name':guest['name'],'attendees':attending}

    @app.post('/api/w/{wid}/events/{eid}/checkins/{gid}')
    def staff_checkin(request:Request,wid:str,eid:str,gid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            guest=guest_in(con,wid,eid,gid)
            if guest['rsvp']!='Confirmed':
                raise HTTPException(409,'Only confirmed invitations may check in')
            if con.execute('SELECT 1 FROM event_checkins WHERE guest_id=?',(gid,)).fetchone():
                raise HTTPException(409,'Invitation already checked in')
            attending=next(g['attending'] for g in details(con,wid,eid) if g['id']==gid)
            who=get_user(request,con)
            con.execute('INSERT INTO event_checkins VALUES (?,?,?,?,?,?)',
                (gid,wid,eid,attending,time_text(),who['id']))
            return {'ok':True,'name':guest['name'],'attendees':attending}

    @app.delete('/api/w/{wid}/events/{eid}/checkins/{gid}')
    def undo_checkin(request:Request,wid:str,eid:str,gid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            guest_in(con,wid,eid,gid)
            con.execute('DELETE FROM event_checkins WHERE guest_id=? AND workspace_id=? AND event_id=?',(gid,wid,eid))
        return {'ok':True}

    @app.get('/checkin/{code}')
    def checkin_page(code:str):
        return FileResponse(os.path.join(os.path.dirname(__file__),'static','checkin.html'),
                            headers={'Cache-Control':'no-store'})

    def queue_one(con,wid,eid,g,channel,kind,subject,body,when):
        mid=uid()
        status='scheduled' if when else 'prepared'
        con.execute('''INSERT INTO event_messages
            (id,workspace_id,event_id,guest_id,channel,kind,subject,body,status,scheduled_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (mid,wid,eid,g['id'],channel,kind,subject,body,status,when,time_text()))
        return mid

    @app.get('/api/w/{wid}/events/{eid}/messages')
    def messages(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid)
            check_event(con,wid,eid)
            result=[dict(x) for x in con.execute('''SELECT m.id,m.guest_id,g.name guest_name,g.phone,
                m.channel,m.kind,m.subject,m.body,m.status,m.scheduled_at,m.sent_at,m.error,m.created_at
                FROM event_messages m JOIN guests g ON g.id=m.guest_id
                WHERE m.workspace_id=? AND m.event_id=? ORDER BY m.created_at DESC LIMIT 300''',(wid,eid))]
            for item in result:
                if item['channel']=='whatsapp' and item['status'] in ('prepared','opened'):
                    number=re.sub(r'\D','',item.pop('phone') or '')
                    item['share_url']='https://wa.me/'+number+'?text='+quote(item['body'],safe='')
                else:
                    item.pop('phone',None)
                item.pop('body',None)
            return {'messages':result,'email_configured':mail_enabled(),
                    'scheduler_configured':bool(os.getenv('EVENTOS_CRON_TOKEN'))}

    @app.post('/api/w/{wid}/events/{eid}/messages')
    def compose(request:Request,wid:str,eid:str,payload:dict):
        required={'guest_ids','channel','kind'}
        if not isinstance(payload,dict) or not required.issubset(payload) or set(payload)-required-{'subject','body','schedule_at'}:
            raise HTTPException(422,'Invalid delivery request')
        ids=payload['guest_ids'];channel=payload['channel'];kind=payload['kind']
        if not isinstance(ids,list) or not 1<=len(ids)<=25 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
            raise HTTPException(422,'Choose 1–25 unique guests')
        if channel not in ('email','whatsapp') or kind not in ('invitation','reminder','announcement','confirmation'):
            raise HTTPException(422,'Unsupported message channel or kind')
        subject=plain(payload.get('subject',''),160,'email subject')
        body=plain(payload.get('body',''),2000,'message body')
        when=valid_future(payload['schedule_at']) if payload.get('schedule_at') else None
        if when and channel!='email':raise HTTPException(422,'Only email may be scheduled')
        if when and not os.getenv('EVENTOS_CRON_TOKEN'):
            raise HTTPException(409,'Scheduled delivery requires a configured scheduler token and external cron')
        with db() as con:
            access(request,con,wid,True)
            event=dict(check_event(con,wid,eid))
            placeholders=','.join('?' for _ in ids)
            guests=[dict(x) for x in con.execute(f'''SELECT * FROM guests WHERE workspace_id=? AND event_id=? AND id IN ({placeholders})''',(wid,eid,*ids))]
            if len(guests)!=len(ids):raise HTTPException(404,'One or more guests were not found in this event')
            if kind=='reminder' and any(g['rsvp'] not in ('Pending','Maybe') for g in guests):
                raise HTTPException(422,'RSVP reminders may only target pending or maybe guests')
            if kind=='confirmation' and any(g['rsvp']!='Confirmed' for g in guests):
                raise HTTPException(422,'RSVP confirmations may only target confirmed guests')
            if channel=='email':
                if not mail_enabled():raise HTTPException(409,'Configure SMTP in Render before sending email')
                if any(not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',g['email'] or '') for g in guests):
                    raise HTTPException(422,'All selected guests must have valid email addresses')
            elif any(not re.sub(r'\D','',g['phone'] or '') for g in guests):
                raise HTTPException(422,'All selected guests need international-format phone numbers for WhatsApp')
            base=host_url(request)
            from branding_studio import public_brand
            brand=public_brand(con,wid)
            out=[]
            for g in guests:
                sub,msg=render_message(g,event,kind,subject,body,base)
                if brand and channel=='email':
                    if brand['name']:sub=brand['name']+' · '+sub
                    if brand['footer_text']:msg+='\n\n'+brand['footer_text']
                ident=queue_one(con,wid,eid,g,channel,kind,sub,msg,when)
                item={'id':ident,'guest_id':g['id'],'guest_name':g['name'],'status':'scheduled' if when else 'prepared'}
                if channel=='whatsapp':
                    # Opening a share link is NOT proof of WhatsApp delivery.
                    number=re.sub(r'\D','',g['phone'])
                    item['share_url']='https://wa.me/'+number+'?text='+quote(msg,safe='')
                out.append(item)
        return {'created':out}

    @app.post('/api/w/{wid}/events/{eid}/messages/{mid}/send')
    def dispatch_one(request:Request,wid:str,eid:str,mid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            row=con.execute("""SELECT m.*,g.email FROM event_messages m
                JOIN guests g ON g.id=m.guest_id
                WHERE m.id=? AND m.workspace_id=? AND m.event_id=?""",(mid,wid,eid)).fetchone()
            if not row:raise HTTPException(404,'Message not found')
            if row['channel']!='email':raise HTTPException(422,'Only email can be sent by the server')
            if row['status'] not in ('prepared','failed'):raise HTTPException(409,'Message has already been sent or scheduled')
            if not mail_enabled():raise HTTPException(409,'SMTP is not configured')
            # Lock the row to prevent repeated clicks from sending it twice.
            changed=con.execute("UPDATE event_messages SET status='sending',error='' WHERE id=? AND status IN ('prepared','failed')",(mid,)).rowcount
            if changed!=1:raise HTTPException(409,'Message already processing')
            from branding_studio import public_brand
            brand=public_brand(con,wid)
        try:
            if brand:
                send_email(row['email'],row['subject'],row['body'],
                           display_name=brand['email_name'] or brand['name'] or 'Your event planner',
                           reply_to=brand['reply_to'] or None)
            else:send_email(row['email'],row['subject'],row['body'])
        except Exception as exc:
            with db() as con:
                con.execute("UPDATE event_messages SET status='failed',error=? WHERE id=?",(redacted_error(exc),mid))
            return {'ok':False,'status':'failed','detail':'Email provider did not accept the message'}
        with db() as con:
            con.execute("UPDATE event_messages SET status='sent',sent_at=?,error='' WHERE id=?",(time_text(),mid))
        return {'ok':True,'status':'sent'}

    @app.post('/api/w/{wid}/events/{eid}/messages/{mid}/shared')
    def share_mark(request:Request,wid:str,eid:str,mid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            item=con.execute("SELECT channel,status FROM event_messages WHERE id=? AND workspace_id=? AND event_id=?",(mid,wid,eid)).fetchone()
            if not item:raise HTTPException(404,'Message not found')
            if item['channel']!='whatsapp' or item['status']!='prepared':
                raise HTTPException(409,'This message cannot be marked opened')
            con.execute("UPDATE event_messages SET status='opened',sent_at=? WHERE id=?",(time_text(),mid))
        return {'ok':True,'status':'opened'}

    @app.get('/api/w/{wid}/events/{eid}/report.csv')
    def report(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid)
            check_event(con,wid,eid)
            guests=details(con,wid,eid)
            rows=[]
            for g in guests:
                rows.append([g['name'],g['rsvp'],g['party_size'],g['attending'],g['group_name'],g['category'],
                             g['table_name'] or '',g['checked_in_count'] or 0,g['checked_in_at'] or ''])
        def safe(x):
            v=str(x)
            return "'"+v if v.lstrip().startswith(('=','+','-','@')) else v
        stream=io.StringIO()
        writer=csv.writer(stream)
        writer.writerow(['Guest','RSVP','Reserved','Attending','Group','Category','Table','Checked in','Checked in at'])
        writer.writerows([[safe(x) for x in row] for row in rows])
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv',
            headers={'Content-Disposition':'attachment; filename="eventos-event-operations.csv"',
                     'Cache-Control':'no-store'})

    @app.get('/api/w/{wid}/operations/summary')
    def workspace_ops(request:Request,wid:str):
        with db() as con:
            access(request,con,wid)
            events=[dict(x) for x in con.execute("""SELECT e.id,e.name,e.start_date,e.status,
                  COUNT(g.id) invitations,
                  SUM(CASE WHEN g.rsvp='Confirmed' THEN COALESCE(
                    (SELECT json_extract(r.party_json,'$.total') FROM rsvp_responses r WHERE r.guest_id=g.id),
                    g.party_size) ELSE 0 END) attending,
                  SUM(CASE WHEN g.rsvp='Pending' THEN 1 ELSE 0 END) pending
                  FROM events e LEFT JOIN guests g ON g.event_id=e.id
                  WHERE e.workspace_id=? GROUP BY e.id ORDER BY e.start_date DESC""",(wid,))]
            for e in events:
                e['attending']=e['attending'] or 0
                e['pending']=e['pending'] or 0
                r=con.execute('''SELECT COALESCE(SUM(attendees),0) FROM event_checkins
                                  WHERE workspace_id=? AND event_id=?''',(wid,e['id'])).fetchone()
                e['checked_in']=r[0]
            return {'events':events}

    @app.get('/api/w/{wid}/team')
    def team(request:Request,wid:str):
        with db() as con:
            access(request,con,wid)
            return [dict(x) for x in con.execute('''SELECT u.id,u.full_name,u.email,m.role FROM memberships m
                JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.role,u.email''',(wid,))]

    @app.post('/api/w/{wid}/team')
    def add_member(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'email','role'}:
            raise HTTPException(422,'Provide member email and role')
        email=plain(payload['email'],255,'email').lower()
        role=payload['role']
        if role not in ('editor','viewer'):
            raise HTTPException(422,'Choose editor or viewer')
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':raise HTTPException(403,'Only owners manage team roles')
            user=con.execute('SELECT id FROM users WHERE lower(email)=?',(email,)).fetchone()
            if not user:raise HTTPException(404,'That user must create an EventOS account first')
            if con.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(wid,user['id'])).fetchone():
                raise HTTPException(409,'That user already belongs to this workspace')
            saas_enforce(con,wid,'members')
            con.execute('INSERT INTO memberships VALUES (?,?,?)',(wid,user['id'],role))
            return {'added':True,'role':role}

    @app.patch('/api/w/{wid}/team/{member_id}')
    def update_member(request:Request,wid:str,member_id:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'role'} or payload['role'] not in ('editor','viewer','owner'):
            raise HTTPException(422,'Choose a valid role')
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':raise HTTPException(403,'Only owners manage team roles')
            prior=con.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,member_id)).fetchone()
            if not prior:raise HTTPException(404,'Team member not found')
            if prior['role']=='owner' and payload['role']!='owner':
                owners=con.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id=? AND role='owner'",(wid,)).fetchone()[0]
                if owners<2:raise HTTPException(409,'A workspace must retain at least one owner')
            con.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(payload['role'],wid,member_id))
            return {'updated':True}

    @app.delete('/api/w/{wid}/team/{member_id}')
    def remove_member(request:Request,wid:str,member_id:str):
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':raise HTTPException(403,'Only owners manage team roles')
            prior=con.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,member_id)).fetchone()
            if not prior:raise HTTPException(404,'Team member not found')
            if prior['role']=='owner':
                owners=con.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id=? AND role='owner'",(wid,)).fetchone()[0]
                if owners<2:raise HTTPException(409,'A workspace must retain at least one owner')
            con.execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?',(wid,member_id))
            return {'removed':True}

    @app.post('/api/system/dispatch-due')
    def dispatch_due(request:Request):
        key=os.getenv('EVENTOS_CRON_TOKEN','')
        provided=request.headers.get('X-EventOS-Job-Token','')
        if not key or not secrets.compare_digest(provided,key):
            raise HTTPException(403,'Scheduler not configured or token invalid')
        if not mail_enabled():raise HTTPException(409,'SMTP not configured')
        with db() as con:
            due=[dict(x) for x in con.execute("""SELECT m.id,m.workspace_id,m.subject,m.body,g.email FROM event_messages m
                JOIN guests g ON g.id=m.guest_id JOIN events e ON e.id=m.event_id
                WHERE m.channel='email' AND m.status='scheduled'
                AND m.scheduled_at<=? AND e.status NOT IN ('Completed','Cancelled')
                ORDER BY m.scheduled_at LIMIT 20""",(time_text(),))]
            if due:
                con.executemany("UPDATE event_messages SET status='sending' WHERE id=? AND status='scheduled'",[(x['id'],) for x in due])
            from branding_studio import public_brand
            brands={x['workspace_id']:public_brand(con,x['workspace_id']) for x in due}
        sent=failed=0
        for item in due:
            try:
                brand=brands.get(item['workspace_id'])
                if brand:
                    send_email(item['email'],item['subject'],item['body'],
                               display_name=brand['email_name'] or brand['name'] or 'Your event planner',
                               reply_to=brand['reply_to'] or None)
                else:send_email(item['email'],item['subject'],item['body'])
                with db() as con:
                    con.execute("UPDATE event_messages SET status='sent',sent_at=?,error='' WHERE id=?",(time_text(),item['id']))
                sent+=1
            except Exception as exc:
                with db() as con:
                    con.execute("UPDATE event_messages SET status='failed',error=? WHERE id=?",(redacted_error(exc),item['id']))
                failed+=1
        return {'processed':len(due),'sent':sent,'failed':failed}

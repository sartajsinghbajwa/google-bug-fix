"""EventOS per-event public websites.

Unlisted token URLs, draft preview guarded by organizer authentication, a
validated section editor, cover image upload, RSVP-token handoff and event data
bridges. Does not publish personal guest lists or donor details.
"""
from __future__ import annotations

import io
import os
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from pathlib import Path

from saas_access import limits, feature_enabled
from event_gallery import binary_image
import cloud_media

TEMPLATES={'garden','classic','modern','night','minimal'}
SECTIONS=('story','schedule','venue','stay','faq','rsvp','gallery','gifts')
HEX=re.compile(r'^#[0-9A-Fa-f]{6}$')
SLUG=re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
DEFAULTS={
 'template':'garden','accent':'#658976','title':'','subtitle':'',
 'welcome':'We cannot wait to celebrate with you.','story_title':'Our story',
 'story':'','dress_code':'','parking':'','travel':'','contact':'',
 'footer':'Made with love','section_order':list(SECTIONS),
 'sections':{x:True for x in SECTIONS},
 'accommodations':[],'faq':[]
}
SITE_ROOT=Path(__file__).resolve().parent/'static'/'event_site.html'


def now():
 return datetime.now(timezone.utc).isoformat(timespec='seconds')


def slugify(value):
 s=re.sub(r'[^a-z0-9]+','-',value.lower()).strip('-')
 return s[:64].rstrip('-') or 'celebration'


def migrate(db):
 cloud_media.migrate(db)
 with db() as con:
  con.executescript("""
    CREATE TABLE IF NOT EXISTS event_websites(
      event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
      workspace_id TEXT NOT NULL,
      access_token TEXT NOT NULL UNIQUE,
      slug TEXT NOT NULL,
      published INTEGER NOT NULL DEFAULT 0,
      content_json TEXT NOT NULL DEFAULT '{}',
      cover BLOB NOT NULL DEFAULT X'',
      cover_thumb BLOB NOT NULL DEFAULT X'',
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_event_sites_token ON event_websites(access_token);
  """)


def ensure(con,wid,eid,event=None):
 row=con.execute('SELECT * FROM event_websites WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
 if row:return dict(row)
 if event is None:
  event=con.execute('SELECT name FROM events WHERE id=? AND workspace_id=?',(eid,wid)).fetchone()
 if not event:raise HTTPException(404,'Event not found')
 con.execute("""INSERT INTO event_websites(event_id,workspace_id,access_token,slug,content_json,updated_at)
     VALUES(?,?,?,?,?,?)""",(eid,wid,secrets.token_urlsafe(27),slugify(event['name']),
                        json.dumps(DEFAULTS),now()))
 return dict(con.execute('SELECT * FROM event_websites WHERE event_id=?',(eid,)).fetchone())


def safe_text(value,limit,label):
 if not isinstance(value,str) or len(value)>limit:
  raise HTTPException(422,'Invalid '+label)
 return value.strip()


def https_link(value,label):
 v=safe_text(value,500,label)
 if v:
  u=urlsplit(v)
  if (u.scheme!='https' or not u.hostname or u.username or u.password
      or any(x in v for x in ('<','>','"',"'",'\\','\n','\r',' '))):
   raise HTTPException(422,'Only safe HTTPS '+label+' links are supported')
 return v


def valid_content(value):
 if not isinstance(value,dict) or set(value)!=set(DEFAULTS):
  raise HTTPException(422,'Invalid website content fields')
 obj={}
 if not isinstance(value['template'],str) or value['template'] not in TEMPLATES:raise HTTPException(422,'Invalid website theme')
 obj['template']=value['template']
 if not isinstance(value['accent'],str) or not HEX.fullmatch(value['accent']):
  raise HTTPException(422,'Invalid accent color')
 obj['accent']=value['accent'].lower()
 for key,cap in [('title',140),('subtitle',200),('welcome',500),
                 ('story_title',100),('story',3000),('dress_code',500),
                 ('parking',1000),('travel',1000),('contact',200),('footer',180)]:
  obj[key]=safe_text(value[key],cap,key)
 enabled=value['sections']
 if not isinstance(enabled,dict) or set(enabled)!=set(SECTIONS) or any(type(v) is not bool for v in enabled.values()):
  raise HTTPException(422,'Invalid website sections')
 obj['sections']={s:enabled[s] for s in SECTIONS}
 order=value['section_order']
 if not isinstance(order,list) or len(order)!=len(SECTIONS) or not all(isinstance(x,str) for x in order) or set(order)!=set(SECTIONS):
  raise HTTPException(422,'Invalid website section order')
 obj['section_order']=order
 hotels=value['accommodations']
 if not isinstance(hotels,list) or len(hotels)>12:raise HTTPException(422,'Maximum 12 accommodations')
 out=[]
 for hotel in hotels:
  if not isinstance(hotel,dict) or set(hotel)!={'name','address','note','url'}:
   raise HTTPException(422,'Invalid accommodation')
  out.append({'name':safe_text(hotel['name'],110,'hotel name'),
              'address':safe_text(hotel['address'],230,'hotel address'),
              'note':safe_text(hotel['note'],350,'hotel note'),
              'url':https_link(hotel['url'],'hotel')})
  if not out[-1]['name']:raise HTTPException(422,'Accommodation needs a name')
 obj['accommodations']=out
 faqs=value['faq']
 if not isinstance(faqs,list) or len(faqs)>20:raise HTTPException(422,'Maximum 20 questions')
 out=[]
 for item in faqs:
  if not isinstance(item,dict) or set(item)!={'question','answer'}:
   raise HTTPException(422,'Invalid FAQ')
  q=safe_text(item['question'],180,'question')
  a=safe_text(item['answer'],1000,'answer')
  if not q or not a:raise HTTPException(422,'Question and answer are required')
  out.append({'question':q,'answer':a})
 obj['faq']=out
 return obj


def site_link(site):
 return '/event/'+site['slug']+'/'+site['access_token']


def record_site(con,site,include_private=False):
 eid=site['event_id']
 e=con.execute('SELECT id,name,type,start_date,end_date,location,status FROM events WHERE id=? AND workspace_id=?',
               (eid,site['workspace_id'])).fetchone()
 if not e:raise HTTPException(404,'Event not found')
 d=json.loads(site['content_json'])
 defaults={**DEFAULTS,**d}
 defaults['sections']={**DEFAULTS['sections'],**(d.get('sections') or {})}
 event_public=dict(e)
 if not include_private:
  # Disabled sections must not reveal their draft content through the JSON API.
  if not defaults['sections']['venue']:
   event_public['location']=''
   for field in ('dress_code','parking','travel','contact'):defaults[field]=''
  if not defaults['sections']['stay']:defaults['accommodations']=[]
  if not defaults['sections']['faq']:defaults['faq']=[]
  if not defaults['sections']['story']:
   defaults['story']=''
   defaults['story_title']=''
 out={
  'event':event_public,
  'content':defaults,
  'url':site_link(site),
  'published':bool(site['published']),
  'has_cover':bool(site['cover']) or bool(con.execute("SELECT 1 FROM cloud_media_objects WHERE entity_kind='event_site_cover' AND entity_id=?",(eid,)).fetchone()),
  'schedule':[dict(x) for x in con.execute("""SELECT title,start_at,end_at,location
      FROM schedule_items WHERE event_id=? AND workspace_id=? ORDER BY start_at LIMIT 80""",(eid,site['workspace_id']))]
      if include_private or defaults['sections']['schedule'] else [],
 }
 if defaults['sections']['gallery'] and feature_enabled(con,site['workspace_id'],'gallery'):
  g=con.execute('SELECT access_token FROM event_galleries WHERE event_id=? AND enabled=1',(eid,)).fetchone()
  if g:out['gallery_url']='/photos/'+g['access_token']
 if defaults['sections']['gifts'] and feature_enabled(con,site['workspace_id'],'registry'):
  r=con.execute('SELECT access_token FROM gift_registries WHERE event_id=? AND enabled=1',(eid,)).fetchone()
  if r:out['registry_url']='/gifts/'+r['access_token']
 from branding_studio import public_brand
 out['brand']=public_brand(con,site['workspace_id'])
 if include_private:
  out['can_edit']=True
  out['workspace_id']=site['workspace_id']
 return out


def public_access(con,slug,token):
 if not isinstance(token,str) or len(token)>160:
  raise HTTPException(404,'Event website unavailable')
 row=con.execute('SELECT * FROM event_websites WHERE access_token=? AND slug=?',(token,slug)).fetchone()
 if not row:raise HTTPException(404,'Event website unavailable')
 limits(con,row['workspace_id'])
 return dict(row)


def install_event_websites(app,db,access,check_event):
 migrate(db)

 @app.get('/api/w/{wid}/events/{eid}/website')
 def organizer_site(request:Request,wid:str,eid:str):
  with db() as con:
   role=access(request,con,wid)
   e=check_event(con,wid,eid)
   site=ensure(con,wid,eid,e)
   details=record_site(con,site,True)
   details['can_edit']=role['role'] in ('owner','editor')
   return details

 @app.put('/api/w/{wid}/events/{eid}/website')
 def save(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'content','slug'}:
   raise HTTPException(422,'Expected website content and slug')
  d=valid_content(payload['content'])
  slug=safe_text(payload['slug'],64,'site URL').lower()
  if not SLUG.fullmatch(slug):raise HTTPException(422,'Use lowercase letters, numbers and hyphens for the URL')
  with db() as con:
   access(request,con,wid,True);e=check_event(con,wid,eid);ensure(con,wid,eid,e)
   con.execute("""UPDATE event_websites SET content_json=?,slug=?,updated_at=?
                   WHERE event_id=? AND workspace_id=?""",(json.dumps(d),slug,now(),eid,wid))
   return record_site(con,con.execute('SELECT * FROM event_websites WHERE event_id=?',(eid,)).fetchone(),True)

 @app.patch('/api/w/{wid}/events/{eid}/website/publish')
 def publish(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'published'} or type(payload['published']) is not bool:
   raise HTTPException(422,'Published must be true or false')
  with db() as con:
   access(request,con,wid,True);event=check_event(con,wid,eid);ensure(con,wid,eid,event)
   con.execute('UPDATE event_websites SET published=?,updated_at=? WHERE event_id=? AND workspace_id=?',
               (int(payload['published']),now(),eid,wid))
  return {'ok':True,'published':payload['published']}

 @app.post('/api/w/{wid}/events/{eid}/website/rotate-link')
 def rotate(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid,True);e=check_event(con,wid,eid);ensure(con,wid,eid,e)
   token=secrets.token_urlsafe(27)
   con.execute('UPDATE event_websites SET access_token=?,updated_at=? WHERE event_id=? AND workspace_id=?',
               (token,now(),eid,wid))
   row=con.execute('SELECT * FROM event_websites WHERE event_id=?',(eid,)).fetchone()
   return {'url':site_link(row)}

 @app.post('/api/w/{wid}/events/{eid}/website/cover')
 def upload_cover(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'content_type','data_base64'}:
   raise HTTPException(422,'Choose a JPG, PNG or WebP image')
  full,thumb=binary_image(payload)
  with db() as con:
   access(request,con,wid,True);e=check_event(con,wid,eid);ensure(con,wid,eid,e)
   external=cloud_media.upload_pair(con,'event_site_cover',eid,wid,eid,full,thumb)
   con.execute('UPDATE event_websites SET cover=?,cover_thumb=?,updated_at=? WHERE event_id=? AND workspace_id=?',
               (b'' if external else full,b'' if external else thumb,now(),eid,wid))
  return {'ok':True}

 @app.delete('/api/w/{wid}/events/{eid}/website/cover')
 def delete_cover(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   if not con.execute('UPDATE event_websites SET cover=?,cover_thumb=?,updated_at=? WHERE event_id=? AND workspace_id=?',
               (b'',b'',now(),eid,wid)).rowcount:
    raise HTTPException(404,'Website not found')
   cloud_media.delete(con,'event_site_cover',eid,wid,eid)
  return {'ok':True}

 def cover_response(con,site):
  data=bytes(site['cover'])
  if not data:
   data=cloud_media.read(con,'event_site_cover',site['event_id'],'full',site['workspace_id'],site['event_id'])
  return Response(data,media_type='image/jpeg',headers={
   'Cache-Control':'private, max-age=60','X-Content-Type-Options':'nosniff'})

 @app.get('/api/w/{wid}/events/{eid}/website/qr.png')
 def website_qr(request:Request,wid:str,eid:str):
  import qrcode
  with db() as con:
   access(request,con,wid);event=check_event(con,wid,eid)
   site=ensure(con,wid,eid,event)
   base=os.getenv('EVENTOS_PUBLIC_BASE_URL','').strip().rstrip('/')
   if not base.startswith('https://'):
    base=str(request.base_url).rstrip('/')
   target=base+site_link(site)
  image=qrcode.make(target)
  out=io.BytesIO()
  image.save(out,format='PNG')
  return Response(out.getvalue(),media_type='image/png',
                  headers={'Cache-Control':'no-store','Content-Disposition':'inline; filename="event-website-qr.png"'})

 @app.get('/api/w/{wid}/events/{eid}/website/cover')
 def staff_cover(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid);check_event(con,wid,eid)
   row=con.execute('SELECT * FROM event_websites WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
   if not row:raise HTTPException(404,'Website not found')
   return cover_response(con,row)

 @app.get('/api/event-site/{slug}/{token}')
 def public_site(request:Request,slug:str,token:str,preview:bool=False):
  with db() as con:
   row=public_access(con,slug,token)
   if not row['published']:
    if not preview:raise HTTPException(404,'Event website is not published')
    access(request,con,row['workspace_id'])
   return record_site(con,row)

 @app.get('/api/event-site/{slug}/{token}/cover')
 def public_cover(request:Request,slug:str,token:str,preview:bool=False):
  with db() as con:
   row=public_access(con,slug,token)
   if not row['published']:
    if not preview:raise HTTPException(404,'Event website is not published')
    access(request,con,row['workspace_id'])
   return cover_response(con,row)

 @app.post('/api/event-site/{slug}/{token}/rsvp-link')
 def verify_invitation(slug:str,token:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'invitation_token'}:
   raise HTTPException(422,'Please provide an invitation code')
  guest_token=payload['invitation_token']
  if not isinstance(guest_token,str) or not 12<=len(guest_token)<=180 or not re.fullmatch(r'[A-Za-z0-9_-]+',guest_token):
   raise HTTPException(422,'Invalid invitation code')
  with db() as con:
   row=public_access(con,slug,token)
   if not row['published']:raise HTTPException(404,'Event website unavailable')
   g=con.execute('SELECT 1 FROM guests WHERE invitation_token=? AND event_id=?',
                 (guest_token,row['event_id'])).fetchone()
   if not g:raise HTTPException(404,'Invitation code does not match this event')
   return {'rsvp_url':'/rsvp/'+guest_token}

 @app.get('/event/{slug}/{token}')
 def html_site(request:Request,slug:str,token:str,preview:bool=False):
  with db() as con:
   row=public_access(con,slug,token)
   if not row['published']:
    if not preview:raise HTTPException(404,'Event website is not published')
    access(request,con,row['workspace_id'])
  return FileResponse(SITE_ROOT,headers={'Cache-Control':'no-store'})

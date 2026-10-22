"""Editable event floor elements and printable seating chart PDFs.

All endpoints use existing EventOS workspace RBAC. Posters intentionally
contain guest names/table assignments, but never addresses, phones, emails
or invitation tokens. Charts are staff-only downloads, never public links.
"""
import io
import math
import re
import uuid
from fastapi import HTTPException, Request, Response

DEFAULTS=(
 ('stage','Stage',50.0,11.0,35.0,10.0),
 ('entrance','Entrance',50.0,92.0,21.0,8.0),
 ('dancefloor','Dance floor',50.0,50.0,23.0,23.0),
)
KINDS=('stage','entrance','dancefloor','custom')
MAX_MARKERS=16


def init(db):
 with db() as con:
  con.executescript("""
    CREATE TABLE IF NOT EXISTS event_floor_elements (
      id TEXT PRIMARY KEY,
      workspace_id TEXT NOT NULL,
      event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('stage','entrance','dancefloor','custom')),
      label TEXT NOT NULL,
      pos_x REAL NOT NULL,
      pos_y REAL NOT NULL,
      width REAL NOT NULL,
      height REAL NOT NULL,
      visible INTEGER NOT NULL DEFAULT 1
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_floor_unique_base
    ON event_floor_elements(event_id,kind) WHERE kind != 'custom';
    CREATE INDEX IF NOT EXISTS idx_floor_event ON event_floor_elements(workspace_id,event_id);
  """)


def ensure(con,wid,eid):
 if not con.execute('SELECT 1 FROM event_floor_elements WHERE workspace_id=? AND event_id=? LIMIT 1',(wid,eid)).fetchone():
  for kind,label,x,y,width,height in DEFAULTS:
   con.execute("""INSERT OR IGNORE INTO event_floor_elements(id,workspace_id,event_id,kind,label,pos_x,pos_y,width,height,visible)
                  VALUES(?,?,?,?,?,?,?,?,?,1)""",
               (str(uuid.uuid4()),wid,eid,kind,label,x,y,width,height))
 return [dict(x) for x in con.execute("""SELECT id,kind,label,pos_x,pos_y,width,height,visible
      FROM event_floor_elements WHERE workspace_id=? AND event_id=?
      ORDER BY CASE kind WHEN 'stage' THEN 1 WHEN 'entrance' THEN 2 WHEN 'dancefloor' THEN 3 ELSE 4 END,label COLLATE NOCASE""",(wid,eid))]


def num(n,minv,maxv,name):
 if type(n) not in (int,float) or not math.isfinite(n) or not minv<=n<=maxv:
  raise HTTPException(422,'Invalid '+name)
 return round(float(n),2)


def safe_label(name):
 if not isinstance(name,str) or not 1<=len(name.strip())<=50 or any(ord(x)<32 for x in name):
  raise HTTPException(422,'Marker label should be 1–50 characters')
 return name.strip()


def pdf_report(event,tables,guests,elements):
 from reportlab.pdfgen import canvas
 from reportlab.lib.colors import HexColor
 from reportlab.lib.pagesizes import landscape,A3
 from reportlab.pdfbase import pdfmetrics
 from reportlab.pdfbase.ttfonts import TTFont
 from reportlab.pdfbase.pdfmetrics import registerFont
 from reportlab.lib.utils import simpleSplit
 import os
 regular,bold='Helvetica','Helvetica-Bold'
 for candidate in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']:
  if os.path.isfile(candidate):
   try:
    if 'SeatUnicode' not in pdfmetrics.getRegisteredFontNames():registerFont(TTFont('SeatUnicode',candidate))
    regular='SeatUnicode'
    bold=regular
   except Exception:pass
   break
 out=io.BytesIO()
 W,H=landscape(A3)
 pdf=canvas.Canvas(out,pagesize=(W,H),pageCompression=1)
 pdf.setTitle('EventOS Seating Chart — '+event['name'])
 pdf.setAuthor('EventOS')
 ink=HexColor('#26473F');green=HexColor('#377960');muted=HexColor('#6B8275')
 light=HexColor('#EDF4EB');cream=HexColor('#FAFAF5')
 def label(s,max_width,font=regular,size=10):
  s=str(s or '')
  while s and pdfmetrics.stringWidth(s,font,size)>max_width:s=s[:-1]
  return s if s else ''
 def text(x,y,val,size=10,color=ink,font=regular):
  pdf.setFillColor(color);pdf.setFont(font,size);pdf.drawString(x,y,str(val))
 def page_head(section):
  pdf.setFillColor(cream);pdf.rect(0,0,W,H,fill=1,stroke=0)
  pdf.setFillColor(green);pdf.roundRect(40,H-116,W-80,76,14,fill=1,stroke=0)
  text(62,H-71,'EVENTOS  /  '+section.upper(),11,HexColor('#CFE8D6'),bold)
  text(62,H-100,label(event['name'],W-125,bold,22),22,HexColor('#FFFFFF'),bold)
 def decorate_footer(number):
  pdf.setStrokeColor(HexColor('#DFE9DF'));pdf.line(40,52,W-40,52)
  text(42,33,'EventOS  |  Display this chart at the venue entrance',9,muted)
  text(W-128,33,'Page '+str(number),9,muted)
 # Page 1 floor map
 page_head('Venue floor plan')
 mapx,mapy=48,130
 mapw,maph=W-355,H-275
 pdf.setFillColor(HexColor('#FFFFFF'));pdf.setStrokeColor(HexColor('#D5E2D7'))
 pdf.roundRect(mapx,mapy,mapw,maph,14,fill=1,stroke=1)
 def center_position(x,y):
  return mapx+mapw*x/100,mapy+maph*(1-y/100)
 for marker in elements:
  if not marker['visible']:continue
  x,y=center_position(marker['pos_x'],marker['pos_y'])
  w=mapw*marker['width']/100;h=maph*marker['height']/100
  kind=marker['kind']
  bg=HexColor('#DCE9E1') if kind=='entrance' else HexColor('#E7E2D4') if kind=='stage' else HexColor('#F1EBDC') if kind=='dancefloor' else HexColor('#DEE9EF')
  pdf.setFillColor(bg);pdf.setStrokeColor(HexColor('#9CB5A7'))
  pdf.roundRect(x-w/2,y-h/2,w,h,8,fill=1,stroke=1)
  name=label(marker['label'],w-12,bold,10)
  text(x-pdfmetrics.stringWidth(name,bold,10)/2,y-3,name,10,ink,bold)
 for table in tables:
  x,y=center_position(table['pos_x'],table['pos_y'])
  is_rect=table['shape']=='rectangle'
  pdf.setFillColor(HexColor('#E2EDE5'))
  pdf.setStrokeColor(green);pdf.setLineWidth(1.7)
  if is_rect:
   w,h=mapw*.135,maph*.082
   pdf.roundRect(x-w/2,y-h/2,w,h,10,fill=1,stroke=1)
  else:
   w=h=64
   pdf.circle(x,y,32,fill=1,stroke=1)
  pdf.setLineWidth(1)
  name=label(table['name'],w-8,bold,8.4)
  text(x-pdfmetrics.stringWidth(name,bold,8.4)/2,y+2,name,8.4,ink,bold)
  cap=str(table['allocated'])+' / '+str(table['capacity'])
  text(x-pdfmetrics.stringWidth(cap,regular,7.5)/2,y-13,cap,7.5,muted)
 # map legend
 text(mapx,mapy-23,'◯  ROUND TABLE      ▭  RECTANGULAR TABLE      •  MOVEABLE VENUE FEATURES',9,muted)
 x=W-275
 text(x,H-159,'TABLE DIRECTORY',12,green,bold)
 text(x,H-177,str(len(tables))+' tables  ·  '+str(sum(t['allocated'] for t in tables))+' seats assigned',9,muted)
 y=H-207
 for t in sorted(tables,key=lambda v:v['name'].casefold()):
  if y<138:
   text(x,y,'See full guest index on following pages →',8,muted);break
  pdf.setFillColor(HexColor('#E9F2E9'));pdf.roundRect(x-3,y-8,250,31,6,fill=1,stroke=0)
  text(x+7,y+6,label(t['name'],157,bold,10),10,ink,bold)
  text(x+176,y+6,str(t['allocated'])+'/'+str(t['capacity'])+' seats',9,green)
  y-=38
 unassigned=[g for g in guests if not g['table_id'] and g['rsvp']=='Confirmed']
 text(x,104,'Unassigned confirmed invitations: '+str(len(unassigned)),9,HexColor('#A65B49') if unassigned else muted)
 decorate_footer(1)
 pdf.showPage()
 # Guest-facing alphabetical directory — no RSVP codes, email, phone, invite tokens.
 rows=[]
 for g in guests:
  if not g['table_id'] or g['rsvp']=='Declined':continue
  names=[g['name']]
  known={g['name'].casefold()}
  for name in g.get('attendee_names',[]):
   if name.casefold() not in known:
    names.append(name)
    known.add(name.casefold())
  rows.extend((name,g['table_name'],g['seats'] if index==0 else 1) for index,name in enumerate(names))
 rows.sort(key=lambda x:(x[0].casefold(),x[1].casefold()))
 count_per_col=24;columns=3;per_page=count_per_col*columns
 if not rows:rows=[('No guests assigned yet','—',0)]
 for offset in range(0,len(rows),per_page):
  page=offset//per_page+2
  page_head('Find your seat  /  Alphabetical guest list')
  colw=(W-100)/3
  total=min(per_page,len(rows)-offset)
  for col in range(columns):
   x=48+col*colw
   text(x,H-155,'GUEST / INVITATION',10,green,bold)
   text(x+colw-111,H-155,'TABLE',10,green,bold)
   for line in range(count_per_col):
    idx=offset+col*count_per_col+line
    if idx>=len(rows):break
    name,table,seats=rows[idx]
    y=H-181-line*23
    if line%2==0:
     pdf.setFillColor(HexColor('#EAF1E9'));pdf.roundRect(x-3,y-5,colw-11,22,3,fill=1,stroke=0)
    text(x+6,y+2,label(name,colw-129,regular,9),9,ink)
    text(x+colw-108,y+2,label(table,91,regular,9),9,green)
  decorate_footer(page)
  pdf.showPage()
 pdf.save()
 out.seek(0)
 return out.getvalue()


def install(app,db,access,check_event,details,table_report):
 init(db)

 @app.get('/api/w/{wid}/events/{eid}/seating/floor')
 def get_floor(request:Request,wid:str,eid:str):
  with db() as con:
   access(request,con,wid);check_event(con,wid,eid)
   return {'elements':ensure(con,wid,eid)}

 @app.put('/api/w/{wid}/events/{eid}/seating/floor/{element_id}')
 def save_floor(request:Request,wid:str,eid:str,element_id:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'label','x','y','width','height','visible'}:
   raise HTTPException(422,'Provide marker label, geometry and visibility')
  name=safe_label(payload['label'])
  x=num(payload['x'],4,96,'horizontal position')
  y=num(payload['y'],4,96,'vertical position')
  width=num(payload['width'],7,75,'width')
  height=num(payload['height'],5,55,'height')
  visible=payload['visible']
  if type(visible) is not bool:raise HTTPException(422,'Invalid visibility')
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   ensure(con,wid,eid)
   count=con.execute("""UPDATE event_floor_elements SET label=?,pos_x=?,pos_y=?,width=?,height=?,visible=?
     WHERE workspace_id=? AND event_id=? AND id=?""",
     (name,x,y,width,height,int(visible),wid,eid,element_id)).rowcount
   if not count:raise HTTPException(404,'Floor element not found')
   return {'id':element_id,'label':name,'pos_x':x,'pos_y':y,'width':width,'height':height,'visible':int(visible)}

 @app.post('/api/w/{wid}/events/{eid}/seating/floor')
 def add_custom_marker(request:Request,wid:str,eid:str,payload:dict):
  if not isinstance(payload,dict) or set(payload)!={'label'}:
   raise HTTPException(422,'Provide marker label')
  name=safe_label(payload['label'])
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   existing=ensure(con,wid,eid)
   if len(existing)>=MAX_MARKERS:raise HTTPException(409,'Maximum 16 floor markers')
   marker_id=str(uuid.uuid4())
   con.execute("""INSERT INTO event_floor_elements(id,workspace_id,event_id,kind,label,pos_x,pos_y,width,height,visible)
       VALUES(?,?,?,'custom',?,50,75,20,8,1)""",(marker_id,wid,eid,name))
   return {'id':marker_id,'kind':'custom','label':name,'pos_x':50,'pos_y':75,'width':20,'height':8,'visible':1}

 @app.delete('/api/w/{wid}/events/{eid}/seating/floor/{element_id}')
 def delete_custom_marker(request:Request,wid:str,eid:str,element_id:str):
  with db() as con:
   access(request,con,wid,True);check_event(con,wid,eid)
   row=con.execute('SELECT kind FROM event_floor_elements WHERE id=? AND workspace_id=? AND event_id=?',(element_id,wid,eid)).fetchone()
   if not row:raise HTTPException(404,'Floor element not found')
   if row['kind']!='custom':raise HTTPException(409,'Hide a built-in venue marker instead')
   con.execute('DELETE FROM event_floor_elements WHERE id=?',(element_id,))
   return {'ok':True}

 @app.get('/api/w/{wid}/events/{eid}/seating/chart.pdf')
 def download_seat_chart(request:Request,wid:str,eid:str,inline:bool=False):
  with db() as con:
   access(request,con,wid)
   event=check_event(con,wid,eid)
   gs=details(con,wid,eid)
   tables=table_report(con,wid,eid,gs)
   markers=ensure(con,wid,eid)
   content=pdf_report(event,tables,gs,markers)
  return Response(content,media_type='application/pdf',headers={
   'Content-Disposition':('inline' if inline else 'attachment')+'; filename="eventos-seating-chart.pdf"',
   'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

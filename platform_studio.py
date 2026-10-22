"""Operator-only website/SEO, package, and user editor for EventOS."""
from __future__ import annotations
import html, os, re, uuid
from datetime import datetime,timezone
from urllib.parse import urlsplit
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse, PlainTextResponse

SITE_DEFAULTS={
 'site_name':'Honestly Zen Event Planning',
 'seo_title':'Honestly Zen Event Planning | DIY Party Planning & Alberta Vendors',
 'seo_description':'Plan weddings, birthdays and parties yourself with budgets, invitations, RSVPs and Alberta vendors. Compare quotes and get advice from the Honestly Zen community.',
 'seo_keywords':'DIY party planning Alberta, Alberta party vendors, wedding planning Alberta, birthday party planner',
 'hero_title':'Plan the party. Own the moment.',
 'hero_description':'Your DIY party-planning home: build a budget, create invitations, compare Alberta vendors and get helpful advice from a community of hosts.',
 'logo_url':'','favicon_url':'','social_image_url':'',
 'accent_color':'#23473d','robots_index':'1',
}
SITE_LIMIT={k:(2048 if k.endswith('_url') else 350 if k in ('seo_description','hero_description') else 170) for k in SITE_DEFAULTS}
PLAN_DEFAULTS={
 'free':('Starter',3,120,2),
 'pro':('Pro',10,1000,5),
 'business':('Business',60,10000,25)
}

def utc():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def migrate(db):
 with db() as con:
  con.executescript('''
   CREATE TABLE IF NOT EXISTS platform_site_settings (
    `key` VARCHAR(191) PRIMARY KEY, value TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS platform_packages (
    id TEXT PRIMARY KEY,label TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
    max_events INTEGER NOT NULL,max_guests INTEGER NOT NULL,max_members INTEGER NOT NULL,
    stripe_monthly_price TEXT NOT NULL DEFAULT '',stripe_yearly_price TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
  ''')
  for slug,(label,events,guests,members) in PLAN_DEFAULTS.items():
   con.execute('''INSERT OR IGNORE INTO platform_packages
    (id,label,max_events,max_guests,max_members,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?)''',(slug,label,events,guests,members,utc(),utc()))


def site_settings(con):
 data=dict(SITE_DEFAULTS)
 for row in con.execute('SELECT `key`,value FROM platform_site_settings'):
  if row['key'] in data:data[row['key']]=row['value']
 # Upgrade old untouched branding values without overwriting custom admin branding.
 if data['site_name']=='EventOS':data['site_name']=SITE_DEFAULTS['site_name']
 if data['seo_title']=='EventOS | Plan Your Own Party, Find Alberta Vendors & Get Inspired':data['seo_title']=SITE_DEFAULTS['seo_title']
 return data


def packages(con,only_active=False):
 sql='SELECT * FROM platform_packages'+(' WHERE is_active=1' if only_active else '')+' ORDER BY id'
 return [dict(x) for x in con.execute(sql)]


def require_admin(request,con,get_user):
 user=get_user(request,con)
 if user['platform_role']!='admin':raise HTTPException(403,'Platform administrator required')
 return user


def valid_site(payload):
 if not isinstance(payload,dict) or not payload or set(payload)-set(SITE_DEFAULTS):
  raise HTTPException(422,'Unrecognized website setting')
 clean={}
 for k,v in payload.items():
  if not isinstance(v,str) or len(v)>SITE_LIMIT[k] or '\x00' in v:
   raise HTTPException(422,'Invalid '+k)
  v=v.strip()
  if k=='accent_color' and not re.fullmatch(r'#[a-fA-F0-9]{6}',v):raise HTTPException(422,'Invalid color')
  if k=='robots_index' and v not in ('0','1'):raise HTTPException(422,'Invalid search indexing value')
  if k.endswith('_url') and v:
   p=urlsplit(v)
   if not ((p.scheme=='https' and p.netloc and not p.username and not p.password) or
           (v.startswith('/') and not v.startswith('//') and '\\' not in v)):
    raise HTTPException(422,'Image URLs must be HTTPS or root-relative')
  clean[k]=v
 return clean


def validate_package(payload,edit=False):
 allowed={'id','label','description','max_events','max_guests','max_members','stripe_monthly_price','stripe_yearly_price','is_active'}
 if not isinstance(payload,dict) or not payload or set(payload)-allowed:raise HTTPException(422,'Invalid package fields')
 slug=payload.get('id','')
 if not edit and (not isinstance(slug,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,30}',slug)):
  raise HTTPException(422,'Package ID: 2–31 lowercase letters, numbers, - or _')
 clean={}
 for k,v in payload.items():
  if k=='id':continue
  if k in ('max_events','max_guests','max_members'):
   if type(v)!=int or not 1<=v<=1000000:raise HTTPException(422,'Invalid package limit')
  elif k=='is_active':
   if type(v)!=bool:raise HTTPException(422,'Invalid package status')
   v=int(v)
  elif k.startswith('stripe_'):
   if not isinstance(v,str) or v and not re.fullmatch(r'price_[A-Za-z0-9]{8,120}',v):
    raise HTTPException(422,'Invalid Stripe recurring price identifier')
  else:
   if not isinstance(v,str) or not v.strip() or len(v)> (160 if k=='description' else 70):
    if k!='description' or v!='':raise HTTPException(422,'Invalid '+k)
  clean[k]=v
 return clean


def render_landing(con,html_file,base_url):
 from pathlib import Path
 data=site_settings(con)
 raw=Path(html_file).read_text(encoding='utf-8')
 esc=lambda s:html.escape(s,quote=True)
 raw=re.sub(r'<title>.*?</title>','<title>'+esc(data['seo_title'])+'</title>',raw,count=1,flags=re.I|re.S)
 for attr,value in [('description',data['seo_description']),('keywords',data['seo_keywords'])]:
  if attr=='description':
   raw=re.sub(r'<meta name="description"[^>]*>', '<meta name="description" content="'+esc(value)+'">',raw,count=1)
  else:raw=raw.replace('</head>','<meta name="keywords" content="'+esc(value)+'">\n</head>',1)
 for prop,value in [('og:title',data['seo_title']),('og:description',data['seo_description'])]:
  raw=re.sub(r'<meta property="'+prop+'"[^>]*>', '<meta property="'+prop+'" content="'+esc(value)+'">',raw,count=1)
 if data['social_image_url']:
  raw=raw.replace('</head>','<meta property="og:image" content="'+esc(data['social_image_url'])+'">\n</head>',1)
 if base_url.startswith('https://'):
  raw=raw.replace('</head>','<link rel="canonical" href="'+esc(base_url.rstrip('/')+'/')+'">\n</head>',1)
 if data['accent_color']:
  raw=raw.replace('</head>', '<style>:root{--forest:'+esc(data['accent_color'])+';--forest-2:'+esc(data['accent_color'])+'}</style>\n</head>',1)
 if data['favicon_url']:
  raw=raw.replace('</head>','<link rel="icon" href="'+esc(data['favicon_url'])+'">\n</head>',1)
 if data['robots_index']=='0':
  raw=raw.replace('</head>','<meta name="robots" content="noindex,nofollow">\n</head>',1)
 if data['logo_url']:
  raw=raw.replace('<span class="logo-shape" aria-hidden="true">✳</span>',
                  '<img class="brand-uploaded-logo" src="'+esc(data['logo_url'])+'" alt="">',1)
 if data['site_name']:
  raw=raw.replace('aria-label="EventOS home"','aria-label="'+esc(data['site_name'])+' home"',1)
 raw=raw.replace('<!-- EVENTOS_HERO_TITLE -->',esc(data['hero_title']),1)
 raw=raw.replace('<!-- EVENTOS_HERO_DESCRIPTION -->',esc(data['hero_description']),1)
 return HTMLResponse(raw,headers={'Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff'})


def install(app,db,get_user):
 migrate(db)
 from phase7_billing import audit

 @app.get('/api/public/site-brand')
 def public_site_brand():
  with db() as con:
   settings=site_settings(con)
  return {'site_name':settings['site_name'], 'logo_url':settings['logo_url'],
          'favicon_url':settings['favicon_url']}

 @app.get('/api/platform/studio')
 def read(request:Request):
  with db() as con:
   require_admin(request,con,get_user)
   return {'site':site_settings(con),'packages':packages(con),
           'accounts':con.execute('SELECT COUNT(*) FROM users').fetchone()[0],
           'billing_secret_configured':bool(os.environ.get('EVENTOS_STRIPE_SECRET_KEY')),
           'invoice_secret_configured':bool(os.environ.get('EVENTOS_CONNECT_SECRET_KEY') and os.environ.get('EVENTOS_AGENCY_INVOICE_WEBHOOK_SECRET'))}

 @app.patch('/api/platform/site-settings')
 def save_site(request:Request,payload:dict):
  clean=valid_site(payload)
  with db() as con:
   actor=require_admin(request,con,get_user)
   for key,value in clean.items():
    con.execute('''INSERT INTO platform_site_settings(`key`,value,updated_at) VALUES(?,?,?)
       ON CONFLICT(`key`) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
       (key,value,utc()))
   audit(con,actor['id'],'website.settings','site','main')
   return {'site':site_settings(con)}

 @app.post('/api/platform/packages')
 def create_package(request:Request,payload:dict):
  clean=validate_package(payload)
  required={'label','max_events','max_guests','max_members'}
  if not required.issubset(clean):raise HTTPException(422,'Package name and quotas are required')
  slug=payload['id']
  with db() as con:
   actor=require_admin(request,con,get_user)
   if con.execute('SELECT id FROM platform_packages WHERE id=?',(slug,)).fetchone():
    raise HTTPException(409,'Package identifier already exists')
   stamp=utc();defaults={'description':'','stripe_monthly_price':'','stripe_yearly_price':'','is_active':1}
   fields={**defaults,**clean}
   con.execute('''INSERT INTO platform_packages(id,label,description,max_events,max_guests,max_members,
      stripe_monthly_price,stripe_yearly_price,is_active,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',tuple([slug]+[fields[k] for k in ('label','description','max_events','max_guests','max_members','stripe_monthly_price','stripe_yearly_price','is_active')]+[stamp,stamp]))
   audit(con,actor['id'],'package.created','package',slug)
   return {'packages':packages(con)}

 @app.patch('/api/platform/packages/{slug}')
 def edit_package(request:Request,slug:str,payload:dict):
  clean=validate_package(payload,True)
  if not clean:raise HTTPException(422,'No settings changed')
  with db() as con:
   actor=require_admin(request,con,get_user)
   if not con.execute('SELECT id FROM platform_packages WHERE id=?',(slug,)).fetchone():raise HTTPException(404,'Package not found')
   if slug=='free' and (clean.get('is_active',1)==0 or clean.get('stripe_monthly_price') or clean.get('stripe_yearly_price')):
    raise HTTPException(422,'Starter package must remain available without Stripe pricing')
   clean['updated_at']=utc()
   con.execute('UPDATE platform_packages SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),slug))
   audit(con,actor['id'],'package.updated','package',slug)
   return {'packages':packages(con)}

 @app.patch('/api/platform/users/{user_id}/details')
 def user_details(request:Request,user_id:str,payload:dict):
  allowed={'full_name','email','email_verified'}
  if not isinstance(payload,dict) or not payload or set(payload)-allowed:raise HTTPException(422,'Invalid user details')
  clean={}
  for k,v in payload.items():
   if k=='email_verified':
    if type(v)!=bool:raise HTTPException(422,'Invalid verification flag')
    clean[k]=int(v)
   elif not isinstance(v,str) or not v.strip() or len(v)>255:
    raise HTTPException(422,'Invalid '+k)
   else:
    clean[k]=v.strip().lower() if k=='email' else v.strip()
    if k=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',clean[k]):raise HTTPException(422,'Invalid email')
  with db() as con:
   actor=require_admin(request,con,get_user)
   if actor['id']==user_id and ('email_verified' in clean or 'email' in clean):
    raise HTTPException(409,'Use your account settings to change your own identity')
   target=con.execute('SELECT id FROM users WHERE id=?',(user_id,)).fetchone()
   if not target:raise HTTPException(404,'User not found')
   try:
    con.execute('UPDATE users SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),user_id))
   except Exception as exc:
    # Do not accidentally leak database constraint errors to clients.
    raise HTTPException(409,'This user email is already in use or the update was rejected') from exc
   audit(con,actor['id'],'user.details','user',user_id)
  return {'ok':True}

 @app.get('/robots.txt')
 def robots(request:Request):
  from public_seo import robots_txt
  with db() as con:settings=site_settings(con)
  return robots_txt(request,settings)

 @app.get('/sitemap.xml')
 def sitemap(request:Request):
  from public_seo import xml_sitemap
  with db() as con:
   settings=site_settings(con)
   return xml_sitemap(con,request,settings)

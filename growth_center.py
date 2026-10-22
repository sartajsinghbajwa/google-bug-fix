"""EventOS Growth Center: notifications, alerts, blog, marketing tags and vendor insights.
Additive schema. Public tracking identifiers, never payment or account secrets.
"""
from __future__ import annotations
import re, uuid, json
from datetime import datetime,timezone
from fastapi import Request,HTTPException
from urllib.parse import urlsplit

STAMP=lambda:datetime.now(timezone.utc).isoformat(timespec='seconds')
UID=lambda:uuid.uuid4().hex
TABLES={
'growth_notifications':'''CREATE TABLE IF NOT EXISTS growth_notifications (
 id VARCHAR(64) PRIMARY KEY, target_user VARCHAR(191) NOT NULL DEFAULT '', audience VARCHAR(24) NOT NULL DEFAULT 'all',
 title VARCHAR(180) NOT NULL, body TEXT NOT NULL, link_url VARCHAR(300) NOT NULL DEFAULT '',
 created_at VARCHAR(40) NOT NULL, expires_at VARCHAR(40) NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1)''',
'growth_notification_reads':'''CREATE TABLE IF NOT EXISTS growth_notification_reads (
 notification_id VARCHAR(64) NOT NULL, user_id VARCHAR(191) NOT NULL, read_at VARCHAR(40) NOT NULL,
 PRIMARY KEY(notification_id,user_id))''',
'growth_alerts':'''CREATE TABLE IF NOT EXISTS growth_alerts (
 id VARCHAR(64) PRIMARY KEY, title VARCHAR(160) NOT NULL, body TEXT NOT NULL,
 severity VARCHAR(20) NOT NULL, audience VARCHAR(24) NOT NULL DEFAULT 'all', is_active INTEGER NOT NULL DEFAULT 1,
 starts_at VARCHAR(40) NOT NULL DEFAULT '', ends_at VARCHAR(40) NOT NULL DEFAULT '', created_at VARCHAR(40) NOT NULL)''',
'growth_articles':'''CREATE TABLE IF NOT EXISTS growth_articles (
 id VARCHAR(64) PRIMARY KEY, slug VARCHAR(150) NOT NULL UNIQUE, title VARCHAR(200) NOT NULL,
 category VARCHAR(50) NOT NULL, summary VARCHAR(500) NOT NULL, body TEXT NOT NULL,
 cover_url VARCHAR(400) NOT NULL DEFAULT '', status VARCHAR(20) NOT NULL DEFAULT 'draft',
 author_id VARCHAR(191) NOT NULL, published_at VARCHAR(40) NOT NULL DEFAULT '', updated_at VARCHAR(40) NOT NULL)''',
'growth_tracking':'''CREATE TABLE IF NOT EXISTS growth_tracking (
 config_key VARCHAR(50) PRIMARY KEY, config_value VARCHAR(200) NOT NULL)''',
}
INDEXES=('CREATE INDEX IF NOT EXISTS idx_growth_notice ON growth_notifications(created_at)',
 'CREATE INDEX IF NOT EXISTS idx_growth_article_published ON growth_articles(status,published_at)',)
AUDIENCES={'all','personal','business','vendors'}
CATEGORIES={'News','Planning tips','Weddings','Birthdays','Anniversaries','DIY guides','Vendor stories','Local Alberta','Platform updates'}

def migrate(db):
 with db() as con:
  for sql in TABLES.values():con.execute(sql)
  for sql in INDEXES:con.execute(sql)

def req(payload,allowed,required=()):
 if not isinstance(payload,dict) or set(payload)-set(allowed) or not set(required).issubset(payload):raise HTTPException(422,'Invalid fields')

def s(p,k,low=0,high=300):
 v=p.get(k,'')
 if not isinstance(v,str) or not low<=len(v.strip())<=high:raise HTTPException(422,'Invalid '+k)
 return v.strip()

def flag(v):
 if type(v) is not bool:raise HTTPException(422,'Expected true or false')
 return int(v)

def secure_url(v):
 if v and not (re.fullmatch(r'/api/public/platform-media/[a-f0-9]{32}',v) or (v.startswith('https://') and urlsplit(v).netloc and '@' not in urlsplit(v).netloc)):
  raise HTTPException(422,'Upload a cover or use a secure HTTPS image')
 return v

def safe_link(v):
 if v and not (v.startswith('/app') or v.startswith('/news') or v.startswith('/discover/vendors')):raise HTTPException(422,'Only internal links allowed')
 if '//' in v or '\\' in v:raise HTTPException(422,'Invalid link')
 return v

def is_admin(u):
 if u.get('platform_role')!='admin':raise HTTPException(403,'Platform administrator required')

def get_roles(con,u):
 if u['platform_role']=='admin':return {'all','business','personal','vendors'}
 types={r['type'] for r in con.execute('SELECT DISTINCT w.type FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))}
 roles={'all'}|types
 if con.execute('SELECT id FROM market_listings WHERE owner_id=? LIMIT 1',(u['id'],)).fetchone():roles.add('vendors')
 return roles

def notify_user(con,user_id,title,body,link_url='/app'):
 # Call inside existing transaction; vendor/customer private notifications do not leak across users.
 notice_id=UID()
 con.execute('INSERT INTO growth_notifications(id,target_user,audience,title,body,link_url,created_at) VALUES(?,?,?,?,?,?,?)',
             (notice_id,user_id,'all',title[:180],body[:900],safe_link(link_url),STAMP()))
 from email_notifications import enqueue_user
 category='contracts' if 'contract' in title.lower() else 'vendors'
 enqueue_user(con,user_id,category,'EventOS: '+title,'You have an update in a vendor or contract conversation. Sign in to review it.',link_url,dedupe='growth:'+notice_id)

def install(app,db,get_user,mariadb=False):
 if not mariadb:migrate(db)
 def items_for(con,u,table):
  roles=get_roles(con,u)
  clauses=','.join('?' for _ in roles)
  if table=='growth_notifications':
   return [dict(x) for x in con.execute('''SELECT n.*,CASE WHEN r.user_id IS NULL THEN 0 ELSE 1 END AS is_read
    FROM growth_notifications n LEFT JOIN growth_notification_reads r ON r.notification_id=n.id AND r.user_id=?
    WHERE n.is_active=1 AND (n.expires_at='' OR n.expires_at>?) AND
    (n.target_user=? OR (n.target_user='' AND n.audience IN ('''+clauses+''')))
    ORDER BY n.created_at DESC LIMIT 80''',(u['id'],STAMP(),u['id'],*roles))]
  return [dict(x) for x in con.execute('SELECT * FROM growth_alerts WHERE is_active=1 AND audience IN ('+clauses+") AND (starts_at='' OR starts_at<=?) AND (ends_at='' OR ends_at>?) ORDER BY created_at DESC LIMIT 5",(*roles,STAMP(),STAMP()))]

 @app.get('/api/growth/inbox')
 def inbox(request:Request):
  with db() as con:
   u=get_user(request,con);notices=items_for(con,u,'growth_notifications')
   return {'items':notices,'unread':sum(not x['is_read'] for x in notices),'alerts':items_for(con,u,'growth_alerts')}

 @app.post('/api/growth/inbox/{nid}/read')
 def mark_read(request:Request,nid:str):
  with db() as con:
   u=get_user(request,con);visible={n['id'] for n in items_for(con,u,'growth_notifications')}
   if nid not in visible:raise HTTPException(404,'Notification not available')
   con.execute('INSERT INTO growth_notification_reads(notification_id,user_id,read_at) VALUES(?,?,?) ON CONFLICT(notification_id,user_id) DO UPDATE SET read_at=excluded.read_at',(nid,u['id'],STAMP()))
   return {'ok':True}

 @app.get('/api/growth/vendor/dashboard')
 def vendor_dashboard(request:Request):
  with db() as con:
   u=get_user(request,con)
   listings=[dict(r) for r in con.execute('''SELECT l.*,
    (SELECT COUNT(*) FROM market_quotes q WHERE q.listing_id=l.id) quote_count,
    (SELECT COUNT(*) FROM market_quotes q WHERE q.listing_id=l.id AND q.status IN ('accepted','contracted','booked','completed')) booked_count,
    (SELECT COUNT(*) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') reviews_count,
    (SELECT AVG(r.rating) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') avg_rating
    FROM market_listings l WHERE l.owner_id=? ORDER BY l.updated_at DESC''',(u['id'],))]
   quotes=[dict(q) for q in con.execute('''SELECT q.id,q.listing_id,q.event_date,q.offer_amount,q.budget,q.status,q.created_at,q.updated_at,
    l.business_name FROM market_quotes q JOIN market_listings l ON q.listing_id=l.id
    WHERE l.owner_id=? ORDER BY q.updated_at DESC LIMIT 100''',(u['id'],))]
   reviews=[dict(r) for r in con.execute('''SELECT r.id,r.listing_id,r.rating,r.title,r.body,r.status,r.created_at
    FROM market_reviews r JOIN market_listings l ON l.id=r.listing_id WHERE l.owner_id=? ORDER BY r.created_at DESC LIMIT 60''',(u['id'],))]
   return {'listings':listings,'quotes':quotes,'reviews':reviews,'totals':{'listings':len(listings),'published':sum(l['publication_status']=='published' for l in listings),'pending':sum(l['publication_status']=='pending' for l in listings),'quotes':sum(l['quote_count'] for l in listings),'bookings':sum(l['booked_count'] for l in listings),'published_reviews':sum(l['reviews_count'] for l in listings)}}

 @app.get('/api/growth/blog')
 def blog(category:str='',q:str='',page:int=1):
  if category and category not in CATEGORIES:raise HTTPException(422,'Invalid category')
  if not 1<=page<=100 or len(q)>100:raise HTTPException(422,'Invalid search')
  where=["status='published'"];params=[]
  if category:where.append('category=?');params.append(category)
  if q:where.append('(title LIKE ? OR summary LIKE ?)');params+=['%'+q+'%']*2
  with db() as con:
   rows=con.execute('SELECT id,slug,title,category,summary,cover_url,published_at FROM growth_articles WHERE '+' AND '.join(where)+' ORDER BY published_at DESC LIMIT 20 OFFSET ?',(*params,(page-1)*20)).fetchall()
   return {'items':[dict(r) for r in rows],'categories':sorted(CATEGORIES),'page':page}

 @app.get('/api/growth/blog/{slug}')
 def blog_article(slug:str):
  with db() as con:r=con.execute("SELECT slug,title,category,summary,body,cover_url,published_at FROM growth_articles WHERE slug=? AND status='published'",(slug,)).fetchone()
  if not r:raise HTTPException(404,'Article not available')
  return dict(r)

 @app.get('/api/growth/public-alerts')
 def public_alerts():
  with db() as con:
   return {'items':[dict(r) for r in con.execute('''SELECT title,body,severity FROM growth_alerts
    WHERE is_active=1 AND audience='all' AND (starts_at='' OR starts_at<=?) AND (ends_at='' OR ends_at>?)
    ORDER BY created_at DESC LIMIT 3''',(STAMP(),STAMP()))]}

 @app.get('/api/growth/public-settings')
 def public_settings():
  with db() as con:
   row={r['config_key']:r['config_value'] for r in con.execute('SELECT config_key,config_value FROM growth_tracking')}
  return {'ga4_id':row.get('ga4_id',''),'meta_pixel_id':row.get('meta_pixel_id',''),'analytics_consent_required':True}

 @app.get('/api/growth/admin/overview')
 def overview(request:Request):
  with db() as con:
   is_admin(get_user(request,con))
   counts={tbl:con.execute('SELECT COUNT(*) FROM '+tbl).fetchone()[0] for tbl in ('growth_articles','growth_alerts','growth_notifications','market_listings','market_quotes','market_reviews')}
   articles=[dict(r) for r in con.execute('SELECT id,slug,title,category,status,published_at,updated_at FROM growth_articles ORDER BY updated_at DESC LIMIT 100')]
   alerts=[dict(r) for r in con.execute('SELECT * FROM growth_alerts ORDER BY created_at DESC LIMIT 40')]
   notices=[dict(r) for r in con.execute('SELECT id,audience,target_user,title,body,link_url,created_at,is_active FROM growth_notifications WHERE target_user=\'\' ORDER BY created_at DESC LIMIT 30')]
   settings={r['config_key']:r['config_value'] for r in con.execute('SELECT config_key,config_value FROM growth_tracking')}
   return {'counts':counts,'articles':articles,'alerts':alerts,'notifications':notices,'tracking':settings,'categories':sorted(CATEGORIES)}

 @app.put('/api/growth/admin/tracking')
 def tracking(request:Request,payload:dict):
  req(payload,('ga4_id','meta_pixel_id'))
  if not payload:raise HTTPException(422,'No settings')
  ga=s(payload,'ga4_id',0,32);pixel=s(payload,'meta_pixel_id',0,32)
  if ga and not re.fullmatch(r'G-[A-Z0-9]{5,20}',ga):raise HTTPException(422,'Use a valid GA4 measurement ID')
  if pixel and not re.fullmatch(r'[0-9]{8,25}',pixel):raise HTTPException(422,'Use numeric Meta Pixel ID')
  with db() as con:
   is_admin(get_user(request,con))
   for k,v in (('ga4_id',ga),('meta_pixel_id',pixel)):
    con.execute('INSERT INTO growth_tracking(config_key,config_value) VALUES(?,?) ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value',(k,v))
  return {'ok':True}

 @app.post('/api/growth/admin/notifications')
 def announce(request:Request,payload:dict):
  req(payload,('audience','title','body','link_url','expires_at'),('audience','title','body'))
  audience=s(payload,'audience');
  if audience not in AUDIENCES:raise HTTPException(422,'Invalid audience')
  title=s(payload,'title',3,180);body=s(payload,'body',5,1200);url=safe_link(s(payload,'link_url',0,300));expiry=s(payload,'expires_at',0,40)
  if expiry:
   try:
    if datetime.fromisoformat(expiry)<=datetime.now(timezone.utc):raise ValueError()
   except (ValueError,TypeError):raise HTTPException(422,'Invalid future expiration')
  with db() as con:
   is_admin(get_user(request,con));nid=UID()
   con.execute('INSERT INTO growth_notifications(id,target_user,audience,title,body,link_url,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',(nid,'',audience,title,body,url,STAMP(),expiry))
   from email_notifications import opt_in_announcement
   emails=opt_in_announcement(con,audience,'EventOS announcement: '+title,
       'A new platform announcement is available in your notification center.','announcement:'+nid)
  return {'id':nid,'opted_in_email_queued':emails}

 @app.patch('/api/growth/admin/notifications/{nid}')
 def notice_toggle(request:Request,nid:str,payload:dict):
  req(payload,('is_active',),('is_active',));active=flag(payload['is_active'])
  with db() as con:
   is_admin(get_user(request,con))
   if not con.execute('SELECT id FROM growth_notifications WHERE id=? AND target_user=\'\'',(nid,)).fetchone():raise HTTPException(404,'Not found')
   con.execute('UPDATE growth_notifications SET is_active=? WHERE id=?',(active,nid))
  return {'ok':True}

 @app.post('/api/growth/admin/alerts')
 def alert_create(request:Request,payload:dict):
  req(payload,('audience','title','body','severity','ends_at'),('audience','title','body','severity'))
  audience=s(payload,'audience');severity=s(payload,'severity')
  if audience not in AUDIENCES or severity not in {'info','warning','critical'}:raise HTTPException(422,'Invalid audience or severity')
  end=s(payload,'ends_at',0,40)
  if end:
   try:
    if datetime.fromisoformat(end)<=datetime.now(timezone.utc):raise ValueError()
   except (TypeError,ValueError):raise HTTPException(422,'Expiration must be in the future')
  with db() as con:
   is_admin(get_user(request,con));ident=UID()
   con.execute('INSERT INTO growth_alerts(id,title,body,severity,audience,ends_at,created_at) VALUES(?,?,?,?,?,?,?)',(ident,s(payload,'title',3,160),s(payload,'body',5,1200),severity,audience,end,STAMP()))
   from email_notifications import opt_in_announcement
   emails=opt_in_announcement(con,audience,'EventOS platform alert: '+s(payload,'title',3,160),
       'An EventOS service alert is available. Sign in or visit the site for details.','alert:'+ident)
  return {'id':ident,'opted_in_email_queued':emails}

 @app.patch('/api/growth/admin/alerts/{aid}')
 def alert_toggle(request:Request,aid:str,payload:dict):
  req(payload,('is_active',),('is_active',));active=flag(payload['is_active'])
  with db() as con:
   is_admin(get_user(request,con))
   if not con.execute('SELECT id FROM growth_alerts WHERE id=?',(aid,)).fetchone():raise HTTPException(404,'Not found')
   con.execute('UPDATE growth_alerts SET is_active=? WHERE id=?',(active,aid))
  return {'ok':True}

 def validated_article(payload,partial=False):
  fields={'title','slug','summary','body','cover_url','category','status'}
  req(payload,fields,() if partial else ('title','summary','body','category'))
  result={}
  for field in payload:
   if field=='title':result[field]=s(payload,field,5,200)
   elif field=='summary':result[field]=s(payload,field,10,500)
   elif field=='body':result[field]=s(payload,field,40,20000)
   elif field=='cover_url':result[field]=secure_url(s(payload,field,0,400))
   elif field=='category':
    result[field]=s(payload,field)
    if result[field] not in CATEGORIES:raise HTTPException(422,'Invalid category')
   elif field=='status':
    result[field]=s(payload,field)
    if result[field] not in ('draft','published'):raise HTTPException(422,'Invalid status')
   elif field=='slug':
    result[field]=s(payload,field)
    if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',result[field]):raise HTTPException(422,'Invalid slug')
  return result

 @app.post('/api/growth/admin/blog')
 def create_article(request:Request,payload:dict):
  clean=validated_article(payload);slug=clean.get('slug') or re.sub('-+','-',re.sub('[^a-z0-9]+','-',clean['title'].lower())).strip('-')[:140]
  with db() as con:
   u=get_user(request,con);is_admin(u)
   if con.execute('SELECT id FROM growth_articles WHERE slug=?',(slug,)).fetchone():raise HTTPException(409,'Slug already in use')
   ident=UID();stamp=STAMP();status=clean.get('status','draft')
   con.execute('INSERT INTO growth_articles VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,slug,clean['title'],clean['category'],clean['summary'],clean['body'],clean.get('cover_url',''),status,u['id'],stamp if status=='published' else '',stamp))
  return {'id':ident,'slug':slug}

 @app.get('/api/growth/admin/blog/{aid}')
 def admin_article(request:Request,aid:str):
  with db() as con:
   is_admin(get_user(request,con));r=con.execute('SELECT * FROM growth_articles WHERE id=?',(aid,)).fetchone()
  if not r:raise HTTPException(404,'Not found')
  return dict(r)

 @app.patch('/api/growth/admin/blog/{aid}')
 def edit_article(request:Request,aid:str,payload:dict):
  clean=validated_article(payload,True)
  if not clean:raise HTTPException(422,'No changes')
  with db() as con:
   is_admin(get_user(request,con));r=con.execute('SELECT * FROM growth_articles WHERE id=?',(aid,)).fetchone()
   if not r:raise HTTPException(404,'Not found')
   if clean.get('status')=='published' and not r['published_at']:clean['published_at']=STAMP()
   clean['updated_at']=STAMP()
   con.execute('UPDATE growth_articles SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),aid))
  return {'ok':True}

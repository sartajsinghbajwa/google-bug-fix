"""EventOS Party Hub — Alberta-first vendor directory, requests, community and trends.

Additive schema, no external payment execution and no certified e-signature claim.
MariaDB schema must be prepared with the WHC installer before restarting.
"""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
import base64, io
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from growth_center import notify_user
from fastapi import HTTPException, Request

CITIES = ('Airdrie','Beaumont','Brooks','Calgary','Camrose','Chestermere','Cold Lake',
          'Edmonton','Fort Saskatchewan','Grande Prairie','Lacombe','Leduc','Lethbridge',
          'Lloydminster','Medicine Hat','Red Deer','Spruce Grove','St. Albert','Wetaskiwin')
CATEGORIES = ('Venues & Banquet Halls','Catering & Food Trucks','Cakes & Desserts',
              'Photography','Videography','Decor & Floral','Event Rentals & Tents',
              'Entertainment & DJs','Live Music','Beauty & Hair','Wedding Officiants',
              'Event Coordination','Kids Entertainment','Balloons & Party Supplies',
              'Invitations & Printing','Transportation','Photo Booths','Audio Visual & Lighting',
              'Bartending & Beverages','Staffing & Security','Cultural & Specialty Services',
              'Gifts & Favours','Other Party Services')
EVENT_TYPES=('Birthday','Wedding','Anniversary','Baby Shower','Bridal Shower','Engagement',
             'Graduation','Retirement','Corporate Party','Holiday Party','Family Gathering',
             'Kids Party','Fundraiser','Cultural Celebration','Other Event')
TOPICS=('Planning Help','Budget & Savings','Venues','Catering & Cakes','Decor & Themes',
        'Invitations & RSVP','Guest Etiquette','Seating & Floor Plans','Entertainment',
        'Kids & Family Parties','Weddings & Celebrations','Vendor Experiences',
        'DIY & Tutorials','Accessibility & Inclusion','Local Alberta Recommendations','Other')
CITY_SET=set(CITIES)
NOW=lambda:datetime.now(timezone.utc).isoformat(timespec='seconds')
newid=lambda:uuid.uuid4().hex

TABLES={
'market_listings':"""CREATE TABLE IF NOT EXISTS market_listings (
 id VARCHAR(64) PRIMARY KEY, owner_id VARCHAR(191) NOT NULL,
 business_name VARCHAR(140) NOT NULL, category VARCHAR(90) NOT NULL,
 description TEXT NOT NULL, city VARCHAR(100) NOT NULL, other_community VARCHAR(120) NOT NULL DEFAULT '',
 service_cities TEXT NOT NULL, province VARCHAR(10) NOT NULL DEFAULT 'AB', country VARCHAR(10) NOT NULL DEFAULT 'CA',
 price_mode VARCHAR(20) NOT NULL, starting_price DECIMAL(12,2) NOT NULL DEFAULT 0,
 website VARCHAR(400) NOT NULL DEFAULT '', image_url VARCHAR(400) NOT NULL DEFAULT '', video_url VARCHAR(400) NOT NULL DEFAULT '', public_email VARCHAR(200) NOT NULL DEFAULT '',
 verification_status VARCHAR(24) NOT NULL DEFAULT 'unverified', publication_status VARCHAR(24) NOT NULL DEFAULT 'pending',
 created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL)""",
'market_listing_details':"""CREATE TABLE IF NOT EXISTS market_listing_details (
 listing_id VARCHAR(64) PRIMARY KEY, highlights_json TEXT NOT NULL,
 inclusions_json TEXT NOT NULL, packages_json TEXT NOT NULL, faqs_json TEXT NOT NULL,
 lead_time VARCHAR(100) NOT NULL DEFAULT '', booking_note VARCHAR(500) NOT NULL DEFAULT '',
 updated_at VARCHAR(40) NOT NULL)""",
'market_listing_photos':"""CREATE TABLE IF NOT EXISTS market_listing_photos (
 id VARCHAR(64) PRIMARY KEY, listing_id VARCHAR(64) NOT NULL,
 image_url VARCHAR(400) NOT NULL, created_at VARCHAR(40) NOT NULL)""",
'market_verification_requests':"""CREATE TABLE IF NOT EXISTS market_verification_requests (
 id VARCHAR(64) PRIMARY KEY, listing_id VARCHAR(64) NOT NULL, owner_id VARCHAR(191) NOT NULL,
 business_website VARCHAR(400) NOT NULL DEFAULT '', notes TEXT NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'pending', reviewed_by VARCHAR(191) NOT NULL DEFAULT '',
 submitted_at VARCHAR(40) NOT NULL, reviewed_at VARCHAR(40) NOT NULL DEFAULT '')""",
'market_saves':"""CREATE TABLE IF NOT EXISTS market_saves (
 id VARCHAR(64) PRIMARY KEY, listing_id VARCHAR(64) NOT NULL, workspace_id VARCHAR(191) NOT NULL,
 crm_vendor_id VARCHAR(191) NOT NULL, created_at VARCHAR(40) NOT NULL,
 UNIQUE (listing_id,workspace_id))""",
'market_quotes':"""CREATE TABLE IF NOT EXISTS market_quotes (
 id VARCHAR(64) PRIMARY KEY, listing_id VARCHAR(64) NOT NULL,
 workspace_id VARCHAR(191) NOT NULL, event_id VARCHAR(191) NOT NULL DEFAULT '', requester_id VARCHAR(191) NOT NULL,
 brief TEXT NOT NULL, event_date VARCHAR(30) NOT NULL DEFAULT '', budget DECIMAL(12,2) NOT NULL DEFAULT 0,
 offer_amount DECIMAL(12,2) NOT NULL DEFAULT 0, status VARCHAR(24) NOT NULL DEFAULT 'requested',
 created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL)""",
'market_quote_messages':"""CREATE TABLE IF NOT EXISTS market_quote_messages (
 id VARCHAR(64) PRIMARY KEY, quote_id VARCHAR(64) NOT NULL, author_id VARCHAR(191) NOT NULL,
 message_body TEXT NOT NULL, offer_amount DECIMAL(12,2) NOT NULL DEFAULT 0, created_at VARCHAR(40) NOT NULL)""",
'market_contracts':"""CREATE TABLE IF NOT EXISTS market_contracts (
 id VARCHAR(64) PRIMARY KEY, quote_id VARCHAR(64) NOT NULL UNIQUE,
 terms TEXT NOT NULL, total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
 document_hash VARCHAR(64) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'draft',
 host_name VARCHAR(160) NOT NULL DEFAULT '', host_signed_at VARCHAR(40) NOT NULL DEFAULT '',
 vendor_name VARCHAR(160) NOT NULL DEFAULT '', vendor_signed_at VARCHAR(40) NOT NULL DEFAULT '',
 created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL)""",
'market_reviews':"""CREATE TABLE IF NOT EXISTS market_reviews (
 id VARCHAR(64) PRIMARY KEY, listing_id VARCHAR(64) NOT NULL, quote_id VARCHAR(64) NOT NULL UNIQUE,
 reviewer_id VARCHAR(191) NOT NULL, rating INTEGER NOT NULL, title VARCHAR(130) NOT NULL,
 body TEXT NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'pending', created_at VARCHAR(40) NOT NULL)""",
'party_posts':"""CREATE TABLE IF NOT EXISTS party_posts (
 id VARCHAR(64) PRIMARY KEY, author_id VARCHAR(191) NOT NULL, author_role VARCHAR(30) NOT NULL,
 category VARCHAR(90) NOT NULL, event_type VARCHAR(80) NOT NULL,
 city VARCHAR(100) NOT NULL, title VARCHAR(160) NOT NULL, body TEXT NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'published', created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL)""",
'party_replies':"""CREATE TABLE IF NOT EXISTS party_replies (
 id VARCHAR(64) PRIMARY KEY, post_id VARCHAR(64) NOT NULL, author_id VARCHAR(191) NOT NULL,
 author_role VARCHAR(30) NOT NULL, body TEXT NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'published', created_at VARCHAR(40) NOT NULL)""",
'party_reports':"""CREATE TABLE IF NOT EXISTS party_reports (
 id VARCHAR(64) PRIMARY KEY, reporter_id VARCHAR(191) NOT NULL,
 target_type VARCHAR(20) NOT NULL, target_id VARCHAR(64) NOT NULL,
 reason VARCHAR(300) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'open', created_at VARCHAR(40) NOT NULL,
 UNIQUE (reporter_id,target_type,target_id))""",
'party_post_saves':"""CREATE TABLE IF NOT EXISTS party_post_saves (
 post_id VARCHAR(64) NOT NULL, user_id VARCHAR(191) NOT NULL, created_at VARCHAR(40) NOT NULL,
 PRIMARY KEY(post_id,user_id))""",
'party_reply_helpful':"""CREATE TABLE IF NOT EXISTS party_reply_helpful (
 reply_id VARCHAR(64) NOT NULL, user_id VARCHAR(191) NOT NULL, created_at VARCHAR(40) NOT NULL,
 PRIMARY KEY(reply_id,user_id))""",
'party_accepted_answers':"""CREATE TABLE IF NOT EXISTS party_accepted_answers (
 post_id VARCHAR(64) PRIMARY KEY, reply_id VARCHAR(64) NOT NULL,
 accepted_by VARCHAR(191) NOT NULL, created_at VARCHAR(40) NOT NULL)""",
'party_trends':"""CREATE TABLE IF NOT EXISTS party_trends (
 id VARCHAR(64) PRIMARY KEY, author_id VARCHAR(191) NOT NULL,
 event_type VARCHAR(80) NOT NULL, category VARCHAR(90) NOT NULL, city VARCHAR(100) NOT NULL DEFAULT '',
 title VARCHAR(160) NOT NULL, summary TEXT NOT NULL, source_url VARCHAR(400) NOT NULL DEFAULT '',
 status VARCHAR(24) NOT NULL DEFAULT 'draft', created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL)"""
}
INDEXES=(
 'CREATE INDEX IF NOT EXISTS idx_market_listing_public ON market_listings(publication_status,category,city)',
 'CREATE INDEX IF NOT EXISTS idx_market_owner ON market_listings(owner_id)',
 'CREATE INDEX IF NOT EXISTS idx_market_quote_workspace ON market_quotes(workspace_id,updated_at)',
 'CREATE INDEX IF NOT EXISTS idx_market_quote_listing ON market_quotes(listing_id,updated_at)',
 'CREATE INDEX IF NOT EXISTS idx_market_messages_quote ON market_quote_messages(quote_id,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_market_reviews_pub ON market_reviews(listing_id,status)',
 'CREATE INDEX IF NOT EXISTS idx_party_feed ON party_posts(status,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_party_replies ON party_replies(post_id,created_at)',
 'CREATE INDEX IF NOT EXISTS idx_party_trends_pub ON party_trends(status,created_at)',
)

def migrate(db):
    with db() as con:
        for stmt in TABLES.values(): con.execute(stmt)
        for stmt in INDEXES: con.execute(stmt)
        columns={r['name'] for r in con.execute('PRAGMA table_info(market_listings)')}
        if 'video_url' not in columns:con.execute("ALTER TABLE market_listings ADD COLUMN video_url VARCHAR(400) NOT NULL DEFAULT ''")

def require(payload,allowed,required=()):
    if not isinstance(payload,dict) or set(payload)-set(allowed) or not set(required).issubset(payload):
        raise HTTPException(422,'Invalid fields')

def string(payload,name,minlen=0,maxlen=300):
    v=payload.get(name,'')
    if not isinstance(v,str) or not minlen<=len(v.strip())<=maxlen:raise HTTPException(422,'Invalid '+name)
    return v.strip()

def choice(value,items,name):
    if value not in items:raise HTTPException(422,'Invalid '+name)
    return value

def price(v):
    if type(v) not in (int,float) or not 0<=v<=10000000:raise HTTPException(422,'Invalid amount')
    return round(v,2)

def link(v):
    if v and (not v.startswith('https://') or not urlsplit(v).netloc or '@' in urlsplit(v).netloc):
        raise HTTPException(422,'Use an https:// website URL')
    return v

def photo_link(v):
    if re.fullmatch(r'/static/vendor_media/v_[0-9a-f]{32}\.webp',v or ''):return v
    return link(v)

def video_link(v):
    if not v:return ''
    v=link(v);p=urlsplit(v);host=(p.hostname or '').lower()
    if host in ('youtu.be','www.youtu.be'):
        if not re.fullmatch(r'/[A-Za-z0-9_-]{11}',p.path):raise HTTPException(422,'Invalid YouTube link')
    elif host in ('youtube.com','www.youtube.com','m.youtube.com'):
        from urllib.parse import parse_qs
        qs=parse_qs(p.query)
        if p.path!='/watch' or not re.fullmatch(r'[A-Za-z0-9_-]{11}',(qs.get('v') or [''])[0]):
            raise HTTPException(422,'Use a YouTube video link')
    elif host in ('vimeo.com','www.vimeo.com'):
        if not re.fullmatch(r'/[0-9]{5,15}',p.path):raise HTTPException(422,'Invalid Vimeo link')
    else:raise HTTPException(422,'Only YouTube and Vimeo video links are supported')
    return v

def listing(con,lid,require_published=True):
    r=con.execute('SELECT * FROM market_listings WHERE id=?',(lid,)).fetchone()
    if not r or (require_published and r['publication_status']!='published'):raise HTTPException(404,'Listing not available')
    return dict(r)

def vendor_view(row):
    safe=['id','business_name','category','description','city','other_community','province','country',
          'price_mode','starting_price','website','image_url','video_url','public_email','verification_status','created_at']
    result={k:row[k] for k in safe}
    try:result['service_cities']=json.loads(row['service_cities'])
    except (ValueError,TypeError):result['service_cities']=[]
    return result

def detail_view(con,lid):
    row=con.execute('SELECT * FROM market_listing_details WHERE listing_id=?',(lid,)).fetchone()
    default={'highlights':[],'inclusions':[],'packages':[],'faqs':[],'lead_time':'','booking_note':''}
    if row:
        for key in ('highlights','inclusions','packages','faqs'):
            try:default[key]=json.loads(row[key+'_json'])
            except (ValueError,TypeError):pass
        default.update(lead_time=row['lead_time'],booking_note=row['booking_note'])
    default['photos']=[dict(r) for r in con.execute('SELECT id,image_url FROM market_listing_photos WHERE listing_id=? ORDER BY created_at,id LIMIT 8',(lid,))]
    return default

def clean_listing_details(payload):
    require(payload,('highlights','inclusions','packages','faqs','lead_time','booking_note'))
    if not payload:raise HTTPException(422,'Add at least one listing detail')
    cleaned={}
    for k in ('highlights','inclusions','packages','faqs'):
        if k not in payload:continue
        value=payload[k]
        if not isinstance(value,list) or len(value)> (5 if k=='packages' else 12):
            raise HTTPException(422,'Maximum number of '+k+' exceeded')
        if k in ('highlights','inclusions'):
            if any(not isinstance(v,str) or not 2<=len(v.strip())<=180 for v in value):raise HTTPException(422,'Invalid '+k)
            cleaned[k]=[v.strip() for v in value]
        else:
            entries=[]
            for row in value:
                if not isinstance(row,dict) or set(row)-({'name','description','price'} if k=='packages' else {'question','answer'}):raise HTTPException(422,'Invalid '+k)
                if k=='packages':
                    name=row.get('name','');description=row.get('description','');amount=row.get('price',0)
                    if not isinstance(name,str) or not 2<=len(name.strip())<=90 or not isinstance(description,str) or not 4<=len(description.strip())<=500:raise HTTPException(422,'Package name and description are required')
                    entries.append({'name':name.strip(),'description':description.strip(),'price':price(amount)})
                else:
                    question=row.get('question','');answer=row.get('answer','')
                    if not isinstance(question,str) or not 6<=len(question.strip())<=160 or not isinstance(answer,str) or not 8<=len(answer.strip())<=600:raise HTTPException(422,'FAQ question and answer are required')
                    entries.append({'question':question.strip(),'answer':answer.strip()})
            cleaned[k]=entries
    for k,n in (('lead_time',100),('booking_note',500)):
        if k in payload:cleaned[k]=string(payload,k,0,n)
    return cleaned

def admin(user):
    if user['platform_role']!='admin':raise HTTPException(403,'Platform admin access required')

def role(con,user):
    if user['platform_role']=='admin':return 'Platform admin'
    if con.execute("SELECT 1 FROM market_listings WHERE owner_id=? AND verification_status='verified' LIMIT 1",(user['id'],)).fetchone():return 'Verified vendor'
    if con.execute('SELECT 1 FROM market_listings WHERE owner_id=? LIMIT 1',(user['id'],)).fetchone():return 'Vendor'
    if con.execute("SELECT 1 FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? AND w.type='business' LIMIT 1",(user['id'],)).fetchone():return 'Business planner'
    return 'Host'

def quote_access(con,quote,user,write=False):
    seller=listing(con,quote['listing_id'],False)['owner_id']==user['id']
    member=con.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(quote['workspace_id'],user['id'])).fetchone()
    buyer=bool(member and (not write or member['role'] in ('owner','editor')))
    if not seller and not buyer:raise HTTPException(404,'Request not found')
    return seller,buyer

def get_quote(con,qid):
    r=con.execute('SELECT * FROM market_quotes WHERE id=?',(qid,)).fetchone()
    if not r:raise HTTPException(404,'Quote request not found')
    return dict(r)

def save_crm(con,lid,wid,eid,check_event):
    item=listing(con,lid)
    row=con.execute('SELECT crm_vendor_id FROM market_saves WHERE listing_id=? AND workspace_id=?',(lid,wid)).fetchone()
    if row:vid=row['crm_vendor_id']
    else:
        vid=newid()
        con.execute('INSERT INTO vendors(id,workspace_id,name,category,email,phone,notes) VALUES(?,?,?,?,?,?,?)',
                    (vid,wid,item['business_name'],item['category'],item['public_email'],'','Marketplace listing: '+lid))
        con.execute('INSERT INTO market_saves VALUES(?,?,?,?,?)',(newid(),lid,wid,vid,NOW()))
    if eid:
        check_event(con,wid,eid)
        existing=con.execute('SELECT id FROM event_vendors WHERE workspace_id=? AND event_id=? AND vendor_id=?',(wid,eid,vid)).fetchone()
        if not existing:
            con.execute('INSERT INTO event_vendors(id,workspace_id,event_id,vendor_id,scope,status,committed) VALUES(?,?,?,?,?,?,?)',
                        (newid(),wid,eid,vid,'Marketplace shortlist','Shortlisted',0))
    return vid

def install(app,db,get_user,access,check_event,mariadb=False):
    if not mariadb:migrate(db)

    @app.get('/api/party/meta')
    def meta():
        return {'cities':CITIES,'categories':CATEGORIES,'event_types':EVENT_TYPES,'topics':TOPICS,
                'province':'Alberta','province_code':'AB','country_code':'CA',
                'coverage_note':'All 19 Alberta cities, plus selectable Other Alberta community for rural areas and towns.'}

    @app.get('/api/party/vendors')
    def marketplace(city:str='',category:str='',q:str='',page:int=1):
        if city and city not in CITY_SET and city!='Other Alberta community':raise HTTPException(422,'Unknown city')
        if category and category not in CATEGORIES:raise HTTPException(422,'Unknown category')
        if not 1<=page<=100:raise HTTPException(422,'Invalid page')
        if len(q)>100:raise HTTPException(422,'Search is too long')
        with db() as con:
            filters=["l.publication_status='published'"]
            params=[]
            if city:
                if city=='Other Alberta community':filters.append("l.city='Other Alberta community'")
                else:
                    filters.append("(l.city=? OR l.service_cities LIKE ?)")
                    params.extend([city,'%"'+city+'"%'])
            if category:filters.append('l.category=?');params.append(category)
            if q:filters.append('(l.business_name LIKE ? OR l.description LIKE ?)');params.extend(['%'+q+'%']*2)
            where=' AND '.join(filters)
            # Promotion only applies to confirmed, paid, currently active campaigns;
            # an unpublished listing is never made public by sponsorship.
            try:
                con.execute('SELECT id FROM monetization_orders LIMIT 1')
                sponsor_sql="""CASE WHEN EXISTS (SELECT 1 FROM monetization_orders mo
                   JOIN monetization_offers mf ON mf.id=mo.offer_id
                   WHERE mo.listing_id=l.id AND mf.kind='sponsorship'
                     AND mo.status='active' AND mo.verified_at<>''
                     AND mo.starts_at<=? AND mo.ends_at>=?) THEN 1 ELSE 0 END"""
                stamp=NOW()
                extra_params=[stamp,stamp]
            except Exception:
                sponsor_sql='0';extra_params=[]  # Allows standalone older test fixtures.
            rows=con.execute('''SELECT l.*,
              (SELECT COUNT(*) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') AS review_count,
              (SELECT AVG(r.rating) FROM market_reviews r WHERE r.listing_id=l.id AND r.status='published') AS rating,
              '''+sponsor_sql+''' AS is_sponsored
              FROM market_listings l WHERE '''+where+' ORDER BY is_sponsored DESC,l.created_at DESC LIMIT 24 OFFSET ?',tuple(extra_params+params+[(page-1)*24])).fetchall()
            return {'items':[{**vendor_view(r),'review_count':r['review_count'],
                     'rating':round(float(r['rating'] or 0),1), 'is_sponsored':bool(r['is_sponsored'])} for r in rows],'page':page}

    @app.get('/api/party/vendors/mine')
    def my_listings(request:Request):
        with db() as con:
            u=get_user(request,con)
            return [dict(r) for r in con.execute("SELECT * FROM market_listings WHERE owner_id=? AND publication_status<>'deleted' ORDER BY created_at DESC",(u['id'],))]

    @app.post('/api/party/vendors')
    def create_listing(request:Request,payload:dict):
        fields={'business_name','category','description','city','other_community','service_cities','price_mode','starting_price','website','image_url','video_url','public_email'}
        require(payload,fields,('business_name','category','description','city','price_mode'))
        kind=choice(payload['category'],CATEGORIES,'category')
        city=choice(payload['city'],(*CITIES,'Other Alberta community'),'city')
        other=string(payload,'other_community',2 if city=='Other Alberta community' else 0,120)
        areas=payload.get('service_cities',[city] if city!='Other Alberta community' else [])
        if not isinstance(areas,list) or len(areas)>19 or any(a not in CITY_SET for a in areas):raise HTTPException(422,'Invalid service cities')
        mode=choice(payload['price_mode'],('starting_at','fixed','quote'),'pricing')
        amount=price(payload.get('starting_price',0))
        if mode!='quote' and amount<=0:raise HTTPException(422,'Provide a valid price in CAD')
        if mode=='quote':amount=0
        email=string(payload,'public_email',0,200)
        if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Invalid email')
        with db() as con:
            u=get_user(request,con)
            count=con.execute('SELECT COUNT(*) FROM market_listings WHERE owner_id=?',(u['id'],)).fetchone()[0]
            if count>=5:raise HTTPException(409,'Maximum 5 listings per owner')
            ident=newid();date=NOW()
            con.execute('''INSERT INTO market_listings(id,owner_id,business_name,category,description,city,other_community,service_cities,
            price_mode,starting_price,website,image_url,video_url,public_email,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (ident,u['id'],string(payload,'business_name',2,140),kind,string(payload,'description',40,3000),
             city,other,json.dumps(sorted(set(areas))),mode,amount,link(string(payload,'website',0,400)),link(string(payload,'image_url',0,400)),video_link(string(payload,'video_url',0,400)),email,date,date))
            return {'id':ident,'publication_status':'pending','verification_status':'unverified'}

    @app.patch('/api/party/vendors/{lid}')
    def edit_listing(request:Request,lid:str,payload:dict):
        allowed=('business_name','category','description','city','other_community','service_cities',
                 'price_mode','starting_price','website','image_url','video_url','public_email')
        require(payload,allowed)
        if not payload:raise HTTPException(422,'No updates submitted')
        clean={}
        for key,value in payload.items():
            if key in ('business_name','description'):
                clean[key]=string(payload,key,2 if key=='business_name' else 40,140 if key=='business_name' else 3000)
            elif key=='category':clean[key]=choice(value,CATEGORIES,'category')
            elif key=='city':clean[key]=choice(value,(*CITIES,'Other Alberta community'),'city')
            elif key=='other_community':clean[key]=string(payload,key,0,120)
            elif key=='service_cities':
                if not isinstance(value,list) or len(value)>19 or any(a not in CITY_SET for a in value):
                    raise HTTPException(422,'Invalid service cities')
                clean[key]=json.dumps(sorted(set(value)))
            elif key=='price_mode':clean[key]=choice(value,('quote','fixed','starting_at'),'pricing')
            elif key=='starting_price':clean[key]=price(value)
            elif key in ('website','image_url'):clean[key]=(photo_link if key=='image_url' else link)(string(payload,key,0,400))
            elif key=='video_url':clean[key]=video_link(string(payload,key,0,400))
            elif key=='public_email':
                clean[key]=string(payload,key,0,200)
                if clean[key] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',clean[key]):raise HTTPException(422,'Invalid email')
        with db() as con:
            u=get_user(request,con);r=listing(con,lid,False)
            if r['owner_id']!=u['id']:raise HTTPException(404,'Listing not found')
            mode=clean.get('price_mode',r['price_mode'])
            amount=clean.get('starting_price',float(r['starting_price']))
            if mode!='quote' and amount<=0:raise HTTPException(422,'Provide the starting or fixed price')
            if mode=='quote':clean['starting_price']=0
            city=clean.get('city',r['city'])
            community=clean.get('other_community',r['other_community'])
            if city=='Other Alberta community' and len(community)<2:raise HTTPException(422,'Enter your Alberta community')
            clean['publication_status']='pending';clean['updated_at']=NOW()
            if any(key in clean for key in ('business_name','website')):
                clean['verification_status']='unverified'
            con.execute('UPDATE market_listings SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),lid))
            return {'ok':True,'publication_status':'pending'}

    @app.post('/api/party/vendors/{lid}/image')
    async def upload_listing_image(request:Request,lid:str,payload:dict):
        # Browser supplies a base64-encoded image. Decode and normalize server-side;
        # never store user-supplied filenames, SVG, metadata, or embedded HTML.
        require(payload,('image_data',),('image_data',))
        # Authenticate and check ownership before any expensive decode.
        with db() as con:
            user=get_user(request,con)
            owned=listing(con,lid,False)
            if owned['owner_id']!=user['id']:raise HTTPException(404,'Listing not available')
        b64=payload['image_data']
        if not isinstance(b64,str) or len(b64)>6_000_000:
            raise HTTPException(413,'Maximum image size is 3 MB')
        try:data=base64.b64decode(b64,validate=True)
        except (ValueError,TypeError):raise HTTPException(422,'Invalid image encoding')
        if not data or len(data)>3_000_000:raise HTTPException(413,'Maximum image size is 3 MB')
        try:
            im=Image.open(io.BytesIO(data))
            if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height>12_000_000:
                raise ValueError('Unsupported image size or format')
            im=ImageOps.exif_transpose(im)
            im.thumbnail((1600,1200))
            out=io.BytesIO();im.convert('RGB').save(out,format='WEBP',quality=82,method=4)
        except (ValueError,UnidentifiedImageError,OSError,Image.DecompressionBombError):
            raise HTTPException(422,'Upload a valid JPG, PNG or WebP image')
        with db() as con:
            u=get_user(request,con);entry=listing(con,lid,False)
            if entry['owner_id']!=u['id']:raise HTTPException(404,'Listing not available')
            folder=Path(__file__).resolve().parent/'static'/'vendor_media'
            folder.mkdir(mode=0o755,parents=True,exist_ok=True)
            filename='v_'+newid()+'.webp';target=folder/filename
            target.write_bytes(out.getvalue())
            try:
                con.execute("UPDATE market_listings SET image_url=?,publication_status='pending',updated_at=? WHERE id=?",('/static/vendor_media/'+filename,NOW(),lid))
            except Exception:
                target.unlink(missing_ok=True);raise
            old=entry['image_url']
            if old.startswith('/static/vendor_media/v_'):
                previous=folder/old.split('/')[-1]
                if previous!=target:previous.unlink(missing_ok=True)
            return {'image_url':'/static/vendor_media/'+filename,'publication_status':'pending'}

    @app.post('/api/party/vendors/{lid}/verification')
    def request_verification(request:Request,lid:str,payload:dict):
        require(payload,('business_website','notes'),('notes',))
        with db() as con:
            u=get_user(request,con);r=listing(con,lid,False)
            if r['owner_id']!=u['id']:raise HTTPException(404,'Listing not found')
            if con.execute("SELECT 1 FROM market_verification_requests WHERE listing_id=? AND status='pending'",(lid,)).fetchone():raise HTTPException(409,'Verification already pending')
            con.execute('INSERT INTO market_verification_requests(id,listing_id,owner_id,business_website,notes,submitted_at) VALUES(?,?,?,?,?,?)',
                        (newid(),lid,u['id'],link(string(payload,'business_website',0,400)),string(payload,'notes',20,1600),NOW()))
            con.execute("UPDATE market_listings SET verification_status='pending' WHERE id=?",(lid,))
            return {'ok':True,'status':'pending'}

    @app.get('/api/party/vendors/{lid}')
    def vendor_detail(lid:str):
        with db() as con:
            item=listing(con,lid)
            reviews=[dict(r) for r in con.execute('''SELECT r.rating,r.title,r.body,r.created_at,u.full_name AS reviewer
                FROM market_reviews r JOIN users u ON u.id=r.reviewer_id
                WHERE r.listing_id=? AND r.status='published' ORDER BY r.created_at DESC LIMIT 30''',(lid,))]
            return {'listing':vendor_view(item),'reviews':reviews,'details':detail_view(con,lid)}

    @app.get('/api/party/vendors/{lid}/details/manage')
    def manage_listing_details(request:Request,lid:str):
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id'] and user['platform_role']!='admin':raise HTTPException(404,'Listing not found')
            return detail_view(con,lid)

    @app.put('/api/party/vendors/{lid}/details')
    def update_listing_details(request:Request,lid:str,payload:dict):
        clean=clean_listing_details(payload)
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id']:raise HTTPException(404,'Listing not found')
            current=detail_view(con,lid);current.update(clean)
            con.execute('''INSERT INTO market_listing_details(listing_id,highlights_json,inclusions_json,packages_json,faqs_json,lead_time,booking_note,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET
               highlights_json=excluded.highlights_json,inclusions_json=excluded.inclusions_json,
               packages_json=excluded.packages_json,faqs_json=excluded.faqs_json,
               lead_time=excluded.lead_time,booking_note=excluded.booking_note,updated_at=excluded.updated_at''',
               (lid,*[json.dumps(current[k]) for k in ('highlights','inclusions','packages','faqs')],current['lead_time'],current['booking_note'],NOW()))
            con.execute("UPDATE market_listings SET publication_status='pending',updated_at=? WHERE id=?",(NOW(),lid))
            return {'ok':True,'publication_status':'pending'}

    @app.post('/api/party/vendors/{lid}/photos')
    def add_listing_photo(request:Request,lid:str,payload:dict):
        require(payload,('image_data',),('image_data',))
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id']:raise HTTPException(404,'Listing not found')
            if con.execute('SELECT COUNT(*) FROM market_listing_photos WHERE listing_id=?',(lid,)).fetchone()[0]>=8:
                raise HTTPException(409,'Maximum 8 gallery photos per listing')
        b64=payload['image_data']
        if not isinstance(b64,str) or len(b64)>6_000_000:raise HTTPException(413,'Gallery photo must be less than 3 MB')
        try:
            contents=base64.b64decode(b64,validate=True)
            if not contents or len(contents)>3_000_000:raise ValueError('image size')
            img=Image.open(io.BytesIO(contents))
            if img.format not in ('PNG','JPEG','WEBP') or img.width*img.height>12_000_000:raise ValueError('image dimensions')
            img=ImageOps.exif_transpose(img);img.thumbnail((1600,1200));buf=io.BytesIO()
            img.convert('RGB').save(buf,format='WEBP',quality=82)
        except (ValueError,TypeError,UnidentifiedImageError,OSError,Image.DecompressionBombError,base64.binascii.Error):
            raise HTTPException(422,'Upload a valid JPG, PNG or WebP under 3 MB')
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id']:raise HTTPException(404,'Listing not found')
            if con.execute('SELECT COUNT(*) FROM market_listing_photos WHERE listing_id=?',(lid,)).fetchone()[0]>=8:
                raise HTTPException(409,'Maximum 8 gallery photos per listing')
            folder=Path(__file__).resolve().parent/'static'/'vendor_media';folder.mkdir(parents=True,exist_ok=True)
            ident=newid();path=folder/('v_'+newid()+'.webp');url='/static/vendor_media/'+path.name
            path.write_bytes(buf.getvalue())
            try:
                con.execute('INSERT INTO market_listing_photos(id,listing_id,image_url,created_at) VALUES(?,?,?,?)',(ident,lid,url,NOW()))
                con.execute("UPDATE market_listings SET publication_status='pending',updated_at=? WHERE id=?",(NOW(),lid))
            except Exception:path.unlink(missing_ok=True);raise
            return {'id':ident,'image_url':url,'publication_status':'pending'}

    @app.delete('/api/party/vendors/{lid}/photos/{photo_id}')
    def remove_listing_photo(request:Request,lid:str,photo_id:str):
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id']:raise HTTPException(404,'Listing not found')
            row=con.execute('SELECT id FROM market_listing_photos WHERE id=? AND listing_id=?',(photo_id,lid)).fetchone()
            if not row:raise HTTPException(404,'Photo not found')
            con.execute('DELETE FROM market_listing_photos WHERE id=? AND listing_id=?',(photo_id,lid))
            con.execute("UPDATE market_listings SET publication_status='pending',updated_at=? WHERE id=?",(NOW(),lid))
            # Keep file to avoid serving a broken cached URL; periodic file cleanup can handle unused media.
            return {'ok':True,'publication_status':'pending'}

    @app.delete('/api/party/vendors/{lid}')
    def remove_listing(request:Request,lid:str):
        with db() as con:
            user=get_user(request,con);owned=listing(con,lid,False)
            if owned['owner_id']!=user['id'] and user['platform_role']!='admin':raise HTTPException(404,'Listing not found')
            con.execute("UPDATE market_listings SET publication_status='deleted',updated_at=? WHERE id=?",(NOW(),lid))
            return {'ok':True,'removed_from_marketplace':True,'records_preserved':True}

    @app.delete('/api/party/community/{pid}')
    def remove_post(request:Request,pid:str):
        with db() as con:
            user=get_user(request,con)
            row=con.execute('SELECT author_id,status FROM party_posts WHERE id=?',(pid,)).fetchone()
            if not row or row['status']=='deleted':raise HTTPException(404,'Discussion not found')
            if row['author_id']!=user['id'] and user['platform_role']!='admin':raise HTTPException(403,'You can only delete your own discussion')
            con.execute("UPDATE party_posts SET status='deleted',updated_at=? WHERE id=?",(NOW(),pid))
            return {'ok':True,'removed_from_community':True}

    @app.post('/api/party/vendors/{lid}/save')
    def save_vendor(request:Request,lid:str,payload:dict):
        require(payload,('workspace_id','event_id'),('workspace_id',))
        with db() as con:
            access(request,con,string(payload,'workspace_id',1,191),True)
            lid_work=string(payload,'workspace_id',1,191)
            vid=save_crm(con,lid,lid_work,string(payload,'event_id',0,191),check_event)
            return {'ok':True,'crm_vendor_id':vid}

    @app.post('/api/party/quotes')
    def new_quote(request:Request,payload:dict):
        require(payload,('listing_id','workspace_id','event_id','brief','budget','event_date'),('listing_id','workspace_id','brief'))
        wid=string(payload,'workspace_id',1,191);lid=string(payload,'listing_id',1,64)
        with db() as con:
            u=get_user(request,con);access(request,con,wid,True)
            item=listing(con,lid)
            if item['owner_id']==u['id']:raise HTTPException(409,'Cannot request a quote from yourself')
            eid=string(payload,'event_id',0,191)
            if eid:check_event(con,wid,eid)
            if con.execute('SELECT COUNT(*) FROM market_quotes WHERE requester_id=?',(u['id'],)).fetchone()[0]>=200:raise HTTPException(409,'Request limit reached')
            ident=newid();date=NOW()
            con.execute('''INSERT INTO market_quotes(id,listing_id,workspace_id,event_id,requester_id,brief,event_date,budget,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (ident,lid,wid,eid,u['id'],string(payload,'brief',20,2500),string(payload,'event_date',0,30),price(payload.get('budget',0)),date,date))
            notify_user(con,item['owner_id'],'New quote request','A host requested a quote from '+item['business_name']+'.','/app')
            return {'id':ident,'status':'requested'}

    @app.get('/api/party/quotes')
    def quotes(request:Request):
        with db() as con:
            u=get_user(request,con)
            return [dict(r) for r in con.execute('''SELECT q.*,l.business_name,l.category, l.owner_id,
                CASE WHEN l.owner_id=? THEN 'vendor' ELSE 'host' END AS my_side
                FROM market_quotes q JOIN market_listings l ON l.id=q.listing_id
                WHERE l.owner_id=? OR EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id=q.workspace_id AND m.user_id=?)
                ORDER BY q.updated_at DESC LIMIT 200''',(u['id'],u['id'],u['id']))]

    @app.get('/api/party/quotes/{qid}')
    def quote_detail(request:Request,qid:str):
        with db() as con:
            u=get_user(request,con);q=get_quote(con,qid);seller,buyer=quote_access(con,q,u)
            messages=[dict(r) for r in con.execute('''SELECT m.message_body,m.offer_amount,m.created_at,u.full_name AS author
                 FROM market_quote_messages m JOIN users u ON u.id=m.author_id WHERE m.quote_id=? ORDER BY m.created_at ASC''',(qid,))]
            contract=con.execute('SELECT * FROM market_contracts WHERE quote_id=?',(qid,)).fetchone()
            return {'quote':q,'messages':messages,'contract':dict(contract) if contract else None,'side':'vendor' if seller else 'host'}

    @app.post('/api/party/quotes/{qid}/messages')
    def negotiate(request:Request,qid:str,payload:dict):
        require(payload,('message_body','offer_amount'),('message_body',))
        with db() as con:
            u=get_user(request,con);q=get_quote(con,qid);quote_access(con,q,u,True)
            if q['status'] in ('declined','cancelled','completed'):raise HTTPException(409,'Request is closed')
            if q['status']=='accepted':raise HTTPException(409,'The quote has been accepted; update the contract instead')
            msg=string(payload,'message_body',2,2000);amount=price(payload.get('offer_amount',0))
            con.execute('INSERT INTO market_quote_messages VALUES(?,?,?,?,?,?)',(newid(),qid,u['id'],msg,amount,NOW()))
            con.execute("UPDATE market_quotes SET status='negotiating',offer_amount=?,updated_at=? WHERE id=?",(amount or q['offer_amount'],NOW(),qid))
            owner=con.execute('SELECT owner_id FROM market_listings WHERE id=?',(q['listing_id'],)).fetchone()
            recipient=q['requester_id'] if owner['owner_id']==u['id'] else owner['owner_id']
            notify_user(con,recipient,'New vendor negotiation reply','You have a new response in a quote conversation.','/app')
            return {'ok':True}

    @app.post('/api/party/quotes/{qid}/status')
    def quote_status(request:Request,qid:str,payload:dict):
        require(payload,('status',),('status',))
        target=choice(payload['status'],('accepted','declined','cancelled','completed'),'status')
        with db() as con:
            u=get_user(request,con);q=get_quote(con,qid);seller,buyer=quote_access(con,q,u,True)
            if target in ('accepted','cancelled','completed') and not buyer:raise HTTPException(403,'Only the host may perform this action')
            if target=='declined' and not seller:raise HTTPException(403,'Only the vendor may decline')
            transitions={'requested':('accepted','declined','cancelled'),'negotiating':('accepted','declined','cancelled'),
                         'accepted':('completed','cancelled')}
            if target not in transitions.get(q['status'],()):raise HTTPException(409,'Invalid status transition')
            if target=='completed':
                ctr=con.execute('SELECT status FROM market_contracts WHERE quote_id=?',(qid,)).fetchone()
                if not ctr or ctr['status']!='signed':raise HTTPException(409,'Both contract signatures are required before completion')
            if target=='accepted':
                vid=save_crm(con,q['listing_id'],q['workspace_id'],q['event_id'],check_event)
                if q['event_id']:
                    con.execute("UPDATE event_vendors SET status='Quote requested',scope=?,committed=? WHERE event_id=? AND vendor_id=? AND workspace_id=?",
                                ('Marketplace quote '+qid, q['offer_amount'], q['event_id'],vid,q['workspace_id']))
            con.execute('UPDATE market_quotes SET status=?,updated_at=? WHERE id=?',(target,NOW(),qid))
            owner=con.execute('SELECT owner_id FROM market_listings WHERE id=?',(q['listing_id'],)).fetchone()
            recipient=q['requester_id'] if owner['owner_id']==u['id'] else owner['owner_id']
            notify_user(con,recipient,'Quote status updated','Your quote request is now '+target+'.','/app')
            return {'ok':True,'status':target}

    @app.post('/api/party/quotes/{qid}/contract')
    def draft_contract(request:Request,qid:str,payload:dict):
        require(payload,('terms','total_amount'),('terms',))
        with db() as con:
            u=get_user(request,con);q=get_quote(con,qid);seller,buyer=quote_access(con,q,u,True)
            if q['status']!='accepted':raise HTTPException(409,'Accept the quote before drafting a contract')
            terms=string(payload,'terms',40,10000);amt=price(payload.get('total_amount',0))
            existing=con.execute('SELECT * FROM market_contracts WHERE quote_id=?',(qid,)).fetchone()
            if existing and (existing['host_signed_at'] or existing['vendor_signed_at']):
                raise HTTPException(409,'A signed contract cannot be edited; create a new agreement outside this workflow')
            h=hashlib.sha256((qid+'\n'+terms+'\n'+str(amt)).encode()).hexdigest()
            if existing:
                con.execute("UPDATE market_contracts SET terms=?,total_amount=?,document_hash=?,updated_at=? WHERE quote_id=?",
                            (terms,amt,h,NOW(),qid))
                ident=existing['id']
            else:
                ident=newid()
                con.execute('''INSERT INTO market_contracts(id,quote_id,terms,total_amount,document_hash,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)''',(ident,qid,terms,amt,h,NOW(),NOW()))
            return {'id':ident,'status':'draft','document_hash':h}

    @app.post('/api/party/quotes/{qid}/contract/sign')
    def sign_contract(request:Request,qid:str,payload:dict):
        require(payload,('typed_name','consent'),('typed_name','consent'))
        if payload.get('consent') is not True:raise HTTPException(422,'Explicit signature consent required')
        name=string(payload,'typed_name',3,160)
        with db() as con:
            u=get_user(request,con);q=get_quote(con,qid);seller,buyer=quote_access(con,q,u,True)
            if q['status']!='accepted':raise HTTPException(409,'This agreement is not available for signature')
            c=con.execute('SELECT * FROM market_contracts WHERE quote_id=?',(qid,)).fetchone()
            if not c:raise HTTPException(404,'No contract draft')
            if buyer and not seller and u['id']!=q['requester_id']:
                raise HTTPException(403,'Only the requesting host may sign the agreement')
            if c['status']=='signed' or (seller and c['vendor_signed_at']) or (buyer and c['host_signed_at']):
                raise HTTPException(409,'This party has already signed')
            side='vendor' if seller else 'host';date=NOW()
            con.execute('UPDATE market_contracts SET '+side+'_name=?,'+side+'_signed_at=?,updated_at=? WHERE quote_id=?',
                        (name,date,date,qid))
            updated=con.execute('SELECT host_signed_at,vendor_signed_at FROM market_contracts WHERE quote_id=?',(qid,)).fetchone()
            if updated['host_signed_at'] and updated['vendor_signed_at']:
                con.execute("UPDATE market_contracts SET status='signed' WHERE quote_id=?",(qid,))
                if q['event_id']:
                    saved=con.execute('SELECT crm_vendor_id FROM market_saves WHERE listing_id=? AND workspace_id=?',
                                      (q['listing_id'],q['workspace_id'])).fetchone()
                    if saved:
                        con.execute("UPDATE event_vendors SET status='Booked' WHERE event_id=? AND workspace_id=? AND vendor_id=?",
                                    (q['event_id'],q['workspace_id'],saved['crm_vendor_id']))
            owner=con.execute('SELECT owner_id FROM market_listings WHERE id=?',(q['listing_id'],)).fetchone()
            recipient=q['requester_id'] if seller else owner['owner_id']
            notify_user(con,recipient,'Contract acknowledgement received','The other party acknowledged your agreement.','/app')
            return {'ok':True,'side':side,'status':'signed' if updated['host_signed_at'] and updated['vendor_signed_at'] else 'awaiting_other_party'}

    @app.post('/api/party/reviews')
    def review(request:Request,payload:dict):
        require(payload,('quote_id','rating','title','body'),('quote_id','rating','title','body'))
        rating=payload['rating']
        if type(rating) is not int or rating not in (1,2,3,4,5):raise HTTPException(422,'Rating must be 1–5')
        with db() as con:
            u=get_user(request,con);q=get_quote(con,string(payload,'quote_id',1,64));seller,buyer=quote_access(con,q,u,True)
            if not buyer or seller or u['id']!=q['requester_id'] or q['status']!='completed':raise HTTPException(403,'Only the original host after a completed service may review')
            if con.execute('SELECT 1 FROM market_reviews WHERE quote_id=?',(q['id'],)).fetchone():raise HTTPException(409,'Already reviewed')
            con.execute('INSERT INTO market_reviews VALUES(?,?,?,?,?,?,?,?,?)',
                        (newid(),q['listing_id'],q['id'],u['id'],rating,string(payload,'title',4,130),string(payload,'body',25,2000),'pending',NOW()))
            return {'ok':True,'status':'pending_review'}

    @app.get('/api/party/community')
    def community(request:Request,category:str='',city:str='',q:str='',sort:str='recent',page:int=1):
        if category and category not in TOPICS:raise HTTPException(422,'Unknown topic')
        if city and city not in CITY_SET:raise HTTPException(422,'Unknown city')
        if sort not in ('recent','active','unanswered','popular','saved'):raise HTTPException(422,'Invalid sort')
        if len(q)>100 or not 1<=page<=100:raise HTTPException(422,'Invalid search')
        where=["p.status='published'"];args=[]
        if category:where.append('p.category=?');args.append(category)
        if city:where.append('p.city=?');args.append(city)
        if q:where.append('(p.title LIKE ? OR p.body LIKE ?)');args.extend(['%'+q+'%']*2)
        with db() as con:
            if sort=='saved':
                u=get_user(request,con)
                where.append('EXISTS (SELECT 1 FROM party_post_saves b WHERE b.post_id=p.id AND b.user_id=?)')
                args.append(u['id'])
            if sort=='unanswered':
                where.append("NOT EXISTS (SELECT 1 FROM party_replies r WHERE r.post_id=p.id AND r.status='published')")
            order={'recent':'p.created_at DESC','active':"COALESCE((SELECT MAX(r.created_at) FROM party_replies r WHERE r.post_id=p.id AND r.status='published'),p.created_at) DESC",'unanswered':'p.created_at DESC','popular':'reply_count DESC, p.created_at DESC','saved':'p.created_at DESC'}[sort]
            rows=con.execute('''SELECT p.*,u.full_name AS author,
              (SELECT COUNT(*) FROM party_replies r WHERE r.post_id=p.id AND r.status='published') AS reply_count,
              (SELECT COUNT(*) FROM party_post_saves b WHERE b.post_id=p.id) AS save_count,
              (SELECT a.reply_id FROM party_accepted_answers a JOIN party_replies ar ON ar.id=a.reply_id AND ar.status='published' WHERE a.post_id=p.id) AS accepted_reply_id
              FROM party_posts p JOIN users u ON u.id=p.author_id WHERE '''+' AND '.join(where)+
              ' ORDER BY '+order+' LIMIT 25 OFFSET ?',tuple(args+[(page-1)*25])).fetchall()
            return {'items':[dict(r) for r in rows],'page':page,'sort':sort}

    @app.post('/api/party/community/{pid}/save')
    def save_community(request:Request,pid:str):
        with db() as con:
            u=get_user(request,con)
            post=con.execute("SELECT id FROM party_posts WHERE id=? AND status='published'",(pid,)).fetchone()
            if not post:raise HTTPException(404,'Discussion unavailable')
            saved=con.execute('SELECT 1 FROM party_post_saves WHERE post_id=? AND user_id=?',(pid,u['id'])).fetchone()
            if saved:
                con.execute('DELETE FROM party_post_saves WHERE post_id=? AND user_id=?',(pid,u['id']))
            else:
                con.execute('INSERT INTO party_post_saves(post_id,user_id,created_at) VALUES(?,?,?)',(pid,u['id'],NOW()))
            return {'saved':not bool(saved)}

    @app.post('/api/party/community/replies/{rid}/helpful')
    def helpful_reply(request:Request,rid:str):
        with db() as con:
            u=get_user(request,con)
            r=con.execute("SELECT r.id,r.author_id FROM party_replies r JOIN party_posts p ON p.id=r.post_id WHERE r.id=? AND r.status='published' AND p.status='published'",(rid,)).fetchone()
            if not r:raise HTTPException(404,'Reply unavailable')
            if r['author_id']==u['id']:raise HTTPException(403,'You cannot vote for your own reply')
            exists=con.execute('SELECT 1 FROM party_reply_helpful WHERE reply_id=? AND user_id=?',(rid,u['id'])).fetchone()
            if exists:con.execute('DELETE FROM party_reply_helpful WHERE reply_id=? AND user_id=?',(rid,u['id']))
            else:con.execute('INSERT INTO party_reply_helpful(reply_id,user_id,created_at) VALUES(?,?,?)',(rid,u['id'],NOW()))
            return {'helpful':not bool(exists)}

    @app.post('/api/party/community/{pid}/accept')
    def accept_reply(request:Request,pid:str,payload:dict):
        require(payload,('reply_id',),('reply_id',))
        rid=string(payload,'reply_id',1,64)
        with db() as con:
            u=get_user(request,con)
            post=con.execute("SELECT author_id FROM party_posts WHERE id=? AND status='published'",(pid,)).fetchone()
            if not post:raise HTTPException(404,'Discussion unavailable')
            if post['author_id']!=u['id'] and u['platform_role']!='admin':raise HTTPException(403,'Only the discussion author can select an answer')
            reply=con.execute("SELECT id FROM party_replies WHERE id=? AND post_id=? AND status='published'",(rid,pid)).fetchone()
            if not reply:raise HTTPException(404,'Reply unavailable')
            con.execute('DELETE FROM party_accepted_answers WHERE post_id=?',(pid,))
            con.execute('INSERT INTO party_accepted_answers(post_id,reply_id,accepted_by,created_at) VALUES(?,?,?,?)',(pid,rid,u['id'],NOW()))
            return {'ok':True,'accepted_reply_id':rid}

    @app.post('/api/party/community')
    def new_post(request:Request,payload:dict):
        require(payload,('title','body','category','event_type','city'),('title','body','category','event_type'))
        category=choice(payload['category'],TOPICS,'topic');event=choice(payload['event_type'],EVENT_TYPES,'event type')
        city=choice(payload.get('city',''),('',*CITIES),'city')
        with db() as con:
            u=get_user(request,con)
            since=datetime.now(timezone.utc).date().isoformat()
            if con.execute('SELECT COUNT(*) FROM party_posts WHERE author_id=? AND created_at>=?',(u['id'],since)).fetchone()[0]>=10:
                raise HTTPException(429,'Daily discussion limit reached')
            ident=newid();date=NOW()
            con.execute('''INSERT INTO party_posts(id,author_id,author_role,category,event_type,city,title,body,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (ident,u['id'],role(con,u),category,event,city,string(payload,'title',8,160),string(payload,'body',20,4000),'published',date,date))
            return {'id':ident,'status':'published'}

    @app.get('/api/party/community/{pid}')
    def thread(pid:str):
        with db() as con:
            post=con.execute('''SELECT p.*,u.full_name AS author FROM party_posts p JOIN users u ON u.id=p.author_id
                        WHERE p.id=? AND p.status='published' ''',(pid,)).fetchone()
            if not post:raise HTTPException(404,'Post unavailable')
            replies=[dict(r) for r in con.execute('''SELECT r.*,u.full_name AS author,
                       (SELECT COUNT(*) FROM party_reply_helpful h WHERE h.reply_id=r.id) AS helpful_count
                       FROM party_replies r JOIN users u ON u.id=r.author_id
                       WHERE r.post_id=? AND r.status='published' ORDER BY r.created_at ASC LIMIT 150''',(pid,))]
            accepted=con.execute("SELECT a.reply_id FROM party_accepted_answers a JOIN party_replies r ON r.id=a.reply_id AND r.status='published' WHERE a.post_id=?",(pid,)).fetchone()
            p=dict(post);p['accepted_reply_id']=accepted['reply_id'] if accepted else ''
            return {'post':p,'replies':replies}

    @app.post('/api/party/community/{pid}/replies')
    def post_reply(request:Request,pid:str,payload:dict):
        require(payload,('body',),('body',))
        with db() as con:
            u=get_user(request,con)
            original=con.execute("SELECT author_id FROM party_posts WHERE id=? AND status='published'",(pid,)).fetchone()
            if not original:raise HTTPException(404,'Post unavailable')
            since=datetime.now(timezone.utc).date().isoformat()
            if con.execute('SELECT COUNT(*) FROM party_replies WHERE author_id=? AND created_at>=?',(u['id'],since)).fetchone()[0]>=40:
                raise HTTPException(429,'Daily reply limit reached')
            ident=newid()
            con.execute('''INSERT INTO party_replies(id,post_id,author_id,author_role,body,status,created_at)
                           VALUES(?,?,?,?,?,?,?)''',
                        (ident,pid,u['id'],role(con,u),string(payload,'body',3,3000),'published',NOW()))
            if original['author_id']!=u['id']:
                from email_notifications import enqueue_user
                enqueue_user(con,original['author_id'],'announcements','Someone replied to your Party Community question',
                    'Your Party Community discussion has a new reply. Open EventOS to read it.','/app',dedupe='party-reply:'+ident)
            return {'id':ident}

    @app.post('/api/party/reports')
    def report_content(request:Request,payload:dict):
        require(payload,('target_type','target_id','reason'),('target_type','target_id','reason'))
        target=choice(payload['target_type'],('post','reply','review','listing'),'report type')
        table={'post':'party_posts','reply':'party_replies','review':'market_reviews','listing':'market_listings'}[target]
        with db() as con:
            u=get_user(request,con);ident=string(payload,'target_id',1,64)
            if not con.execute('SELECT 1 FROM '+table+' WHERE id=?',(ident,)).fetchone():raise HTTPException(404,'Content unavailable')
            if con.execute('SELECT 1 FROM party_reports WHERE reporter_id=? AND target_type=? AND target_id=?',(u['id'],target,ident)).fetchone():raise HTTPException(409,'Already reported')
            con.execute('INSERT INTO party_reports VALUES(?,?,?,?,?,?,?)',(newid(),u['id'],target,ident,string(payload,'reason',8,300),'open',NOW()))
            return {'ok':True}

    @app.get('/api/party/trends')
    def trends():
        with db() as con:
            return [dict(r) for r in con.execute("SELECT id,title,summary,source_url,category,event_type,city,created_at FROM party_trends WHERE status='published' ORDER BY created_at DESC LIMIT 100")]

    @app.get('/api/party/admin/overview')
    def moderation_overview(request:Request):
        with db() as con:
            u=get_user(request,con);admin(u)
            listings=[dict(r) for r in con.execute("SELECT * FROM market_listings ORDER BY CASE WHEN publication_status='pending' THEN 0 WHEN publication_status='published' THEN 1 ELSE 2 END, updated_at DESC LIMIT 200")]
            verify=[dict(r) for r in con.execute("SELECT * FROM market_verification_requests WHERE status='pending' ORDER BY submitted_at LIMIT 100")]
            reviews=[dict(r) for r in con.execute("SELECT * FROM market_reviews WHERE status='pending' ORDER BY created_at LIMIT 100")]
            reports=[dict(r) for r in con.execute("SELECT * FROM party_reports WHERE status='open' ORDER BY created_at LIMIT 100")]
            posts=[dict(r) for r in con.execute('SELECT id,title,status,category,author_id,created_at FROM party_posts ORDER BY created_at DESC LIMIT 100')]
            trends=[dict(r) for r in con.execute('SELECT * FROM party_trends ORDER BY created_at DESC LIMIT 100')]
            return {'listings':listings,'verifications':verify,'reviews':reviews,'reports':reports,'posts':posts,'trends':trends,'pending_listings':con.execute("SELECT COUNT(*) AS n FROM market_listings WHERE publication_status='pending'").fetchone()['n']}

    @app.patch('/api/party/admin/listings/{lid}')
    def moderate_listing(request:Request,lid:str,payload:dict):
        require(payload,('publication_status','verification_status'))
        if not payload:raise HTTPException(422,'No updates')
        fields={}
        if 'publication_status' in payload:fields['publication_status']=choice(payload['publication_status'],('pending','published','hidden'),'listing status')
        if 'verification_status' in payload:fields['verification_status']=choice(payload['verification_status'],('unverified','verified','rejected'),'verification status')
        with db() as con:
            u=get_user(request,con);admin(u)
            listing(con,lid,False)
            con.execute('UPDATE market_listings SET '+','.join(k+'=?' for k in fields)+',updated_at=? WHERE id=?',(*fields.values(),NOW(),lid))
            from email_notifications import enqueue_user
            entry=con.execute('SELECT owner_id FROM market_listings WHERE id=?',(lid,)).fetchone()
            enqueue_user(con,entry['owner_id'],'vendors','Your EventOS vendor listing was reviewed',
                'A platform administrator updated your marketplace listing. Sign in to review its approval status.','/app')
            return {'ok':True}

    @app.patch('/api/party/admin/verification/{rid}')
    def moderate_verify(request:Request,rid:str,payload:dict):
        require(payload,('status',),('status',))
        status=choice(payload['status'],('approved','rejected'),'status')
        with db() as con:
            u=get_user(request,con);admin(u)
            row=con.execute("SELECT * FROM market_verification_requests WHERE id=? AND status='pending'",(rid,)).fetchone()
            if not row:raise HTTPException(404,'Claim unavailable')
            con.execute('UPDATE market_verification_requests SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,u['id'],NOW(),rid))
            con.execute('UPDATE market_listings SET verification_status=? WHERE id=?',('verified' if status=='approved' else 'rejected',row['listing_id']))
            from email_notifications import enqueue_user
            vendor=con.execute('SELECT owner_id FROM market_listings WHERE id=?',(row['listing_id'],)).fetchone()
            enqueue_user(con,vendor['owner_id'],'vendors','Vendor verification review completed',
                'Your EventOS vendor verification request has been '+status+'. Sign in to review the decision.','/app',dedupe='verify:'+rid)
            return {'ok':True}

    @app.patch('/api/party/admin/reviews/{rid}')
    def moderate_review(request:Request,rid:str,payload:dict):
        require(payload,('status',),('status',))
        status=choice(payload['status'],('published','rejected','hidden'),'status')
        with db() as con:
            u=get_user(request,con);admin(u)
            if con.execute('UPDATE market_reviews SET status=? WHERE id=?',(status,rid)).rowcount!=1:raise HTTPException(404,'Review not found')
            from email_notifications import enqueue_user
            reviewer=con.execute('SELECT r.reviewer_id FROM market_reviews r WHERE r.id=?',(rid,)).fetchone()
            if reviewer:
                enqueue_user(con,reviewer['reviewer_id'],'vendors','Your EventOS vendor review was moderated',
                    'Your marketplace review status has changed. Sign in to see the result.','/app')
            return {'ok':True}

    @app.patch('/api/party/admin/replies/{rid}')
    def moderate_reply(request:Request,rid:str,payload:dict):
        require(payload,('status',),('status',))
        status=choice(payload['status'],('published','hidden'),'status')
        with db() as con:
            u=get_user(request,con);admin(u)
            if con.execute('UPDATE party_replies SET status=? WHERE id=?',(status,rid)).rowcount!=1:
                raise HTTPException(404,'Reply not found')
            return {'ok':True}

    @app.patch('/api/party/admin/posts/{pid}')
    def moderate_post(request:Request,pid:str,payload:dict):
        require(payload,('status',),('status',))
        status=choice(payload['status'],('published','hidden'),'status')
        with db() as con:
            u=get_user(request,con);admin(u)
            if not con.execute('SELECT 1 FROM party_posts WHERE id=?',(pid,)).fetchone():raise HTTPException(404,'Post unavailable')
            con.execute('UPDATE party_posts SET status=? WHERE id=?',(status,pid))
            return {'ok':True}

    @app.patch('/api/party/admin/reports/{rid}')
    def resolve_report(request:Request,rid:str,payload:dict):
        require(payload,('status',),('status',))
        status=choice(payload['status'],('resolved','dismissed'),'status')
        with db() as con:
            u=get_user(request,con);admin(u)
            if con.execute('UPDATE party_reports SET status=? WHERE id=? AND status=?',(status,rid,'open')).rowcount!=1:raise HTTPException(404,'Report unavailable')
            return {'ok':True}

    @app.post('/api/party/admin/trends')
    def create_trend(request:Request,payload:dict):
        require(payload,('title','summary','event_type','category','city','source_url','status'),('title','summary','event_type','category'))
        status=choice(payload.get('status','draft'),('draft','published'),'status')
        event=choice(payload['event_type'],EVENT_TYPES,'event type')
        cat=choice(payload['category'],TOPICS,'category')
        city=choice(payload.get('city',''),('',*CITIES),'city')
        with db() as con:
            u=get_user(request,con);admin(u);ident=newid()
            con.execute('INSERT INTO party_trends VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ident,u['id'],event,cat,city,string(payload,'title',8,160),string(payload,'summary',30,5000),
                         link(string(payload,'source_url',0,400)),status,NOW(),NOW()))
            return {'id':ident,'status':status}

    @app.patch('/api/party/admin/trends/{tid}')
    def edit_trend(request:Request,tid:str,payload:dict):
        allowed=('status','title','summary','category','event_type','city','source_url')
        require(payload,allowed)
        if not payload:raise HTTPException(422,'No fields provided')
        clean={}
        for key,value in payload.items():
            if key=='status':clean[key]=choice(value,('draft','published','hidden'),'status')
            elif key=='category':clean[key]=choice(value,TOPICS,'category')
            elif key=='event_type':clean[key]=choice(value,EVENT_TYPES,'event type')
            elif key=='city':clean[key]=choice(value,('',*CITIES),'city')
            elif key=='title':clean[key]=string(payload,'title',8,160)
            elif key=='summary':clean[key]=string(payload,'summary',30,5000)
            elif key=='source_url':clean[key]=link(string(payload,'source_url',0,400))
        clean['updated_at']=NOW()
        with db() as con:
            u=get_user(request,con);admin(u)
            if not con.execute('SELECT 1 FROM party_trends WHERE id=?',(tid,)).fetchone():raise HTTPException(404,'Trend unavailable')
            con.execute('UPDATE party_trends SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),tid))
            return {'ok':True}

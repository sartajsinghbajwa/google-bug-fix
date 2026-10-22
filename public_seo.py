"""Public marketing SEO for Honestly Zen Event Planning.

No schema migration.  Only approved marketplace listings / published blog posts
are rendered and listed.  Private account/event/customer pages are never listed.
"""
from __future__ import annotations
import html, json, os, re
from urllib.parse import urlsplit
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response

BRAND = 'Honestly Zen Event Planning'
PUBLIC_HOSTS = {'honestlyzen.com', 'www.honestlyzen.com'}
STAGING_HOSTS = {'staging.honestlyzen.com'}
BASE_URL = 'https://honestlyzen.com'
STATIC_PAGES = ('/', '/for-vendors', '/discover/vendors', '/news')

def host_name(request):
    return (request.url.hostname or '').lower().rstrip('.')

def public_base(request):
    host = host_name(request)
    if host in PUBLIC_HOSTS:
        return BASE_URL  # Consolidate www/non-www canonical URLs.
    if host in STAGING_HOSTS:
        return 'https://' + host
    configured = os.environ.get('EVENTOS_PUBLIC_BASE_URL', '').rstrip('/')
    parsed = urlsplit(configured)
    if (parsed.scheme == 'https' and parsed.netloc and not parsed.username
            and not parsed.password and not parsed.path and not parsed.query):
        return configured
    # Never turn an arbitrary user-supplied Host into a canonical URL.
    return BASE_URL

def should_index(request, settings):
    host = host_name(request)
    return host not in STAGING_HOSTS and settings.get('robots_index') == '1' and (
        host in PUBLIC_HOSTS or host == urlsplit(os.environ.get('EVENTOS_PUBLIC_BASE_URL', '')).hostname)

def xml_sitemap(con, request, settings):
    base = public_base(request)
    if settings.get('robots_index') != '1':
        return Response('', status_code=404, media_type='text/plain')
    paths = list(STATIC_PAGES)
    dates = {}
    for row in con.execute("SELECT slug,published_at FROM growth_articles WHERE status='published' ORDER BY published_at DESC LIMIT 2000"):
        slug = row['slug']
        if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,148}', slug):
            path = '/news/' + slug
            paths.append(path)
            if row['published_at']:
                dates[path] = row['published_at'][:10]
    for row in con.execute("SELECT id,updated_at FROM market_listings WHERE publication_status='published' ORDER BY updated_at DESC LIMIT 2000"):
        ident = row['id']
        if re.fullmatch(r'[a-f0-9]{32}', ident):
            path = '/vendors/' + ident
            paths.append(path)
            if row['updated_at']:
                dates[path] = row['updated_at'][:10]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in paths:
        parts.append('<url><loc>' + html.escape(base + path, quote=False) + '</loc>' +
                     ('<lastmod>' + html.escape(dates[path]) + '</lastmod>' if path in dates and re.fullmatch(r'\d{4}-\d{2}-\d{2}', dates[path]) else '') + '</url>')
    parts.append('</urlset>')
    return Response('\n'.join(parts) + '\n', media_type='application/xml', headers={'Cache-Control':'public, max-age=300', 'X-Content-Type-Options':'nosniff'})

def robots_txt(request, settings):
    if not should_index(request, settings):
        return Response('User-agent: *\nDisallow: /\n', media_type='text/plain')
    base = public_base(request)
    # Allow the crawler to read the /app HTML noindex tag; login/private APIs remain protected.
    lines = ['User-agent: *', 'Disallow: /api/', 'Disallow: /rsvp/',
             'Disallow: /checkin/', 'Disallow: /client/', 'Disallow: /event/',
             'Disallow: /gifts/', 'Disallow: /photos/', 'Sitemap: '+base+'/sitemap.xml']
    return Response('\n'.join(lines)+'\n', media_type='text/plain')

def esc(value):
    return html.escape(str(value or ''), quote=True)

def safe_media(src, base):
    if not isinstance(src, str) or not src.strip():return ''
    src=src.strip()
    if src.startswith('/') and not src.startswith('//') and '\\' not in src:
        return base+src
    if src.startswith('https://') and urlsplit(src).hostname:
        return src
    return ''

def structured(data):
    return '<script type="application/ld+json">'+json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<','\\u003c')+'</script>'

def branding_markup(raw, settings):
    name=settings.get('site_name') or BRAND
    # Visible identity across headers, footer and mockups, not API symbols.
    raw=raw.replace('event<span class="logo-accent">os</span><span class="logo-period">.</span>', esc(name))
    raw=raw.replace('event<span class="logo-accent">os</span>.',esc(name))
    raw=raw.replace('✳ event<span>os</span>.',esc(name))
    raw=raw.replace('aria-label="EventOS home"', 'aria-label="'+esc(name)+' home"')
    raw=raw.replace('© <span id="currentYear">2026</span> EventOS.', '© <span id="currentYear">2026</span> '+esc(name)+'.')
    logo=settings.get('logo_url','')
    if logo:
        url=esc(logo)
        raw=re.sub(r'<span class="logo-shape"(?: aria-hidden="true")?>✳</span>',
                   '<img class="brand-uploaded-logo" src="'+url+'" alt="'+esc(name)+' logo">',raw)
        # Standalone market-logo content: replace old wordmark with image. Already protected in public_nav.
        raw=re.sub(r'(<a\b[^>]*class="[^"]*market-logo[^"]*"[^>]*>).*?(</a>)',
                   lambda m: m.group(1) + '<img class="brand-uploaded-logo" src="'+url+'" alt="'+esc(name)+' logo">'+m.group(2),raw,flags=re.S)
    else:
        raw=re.sub(r'(<a\b[^>]*class="[^"]*market-logo[^"]*"[^>]*>).*?(</a>)',
                   lambda m: m.group(1)+esc(name)+m.group(2),raw,flags=re.S)
    return raw

def enhance_marketing(raw, request, settings, path, title, description, ldtype='WebPage', hero=False, ogimage=''):
    base=public_base(request)
    url=base+path
    # Strip existing duplicate SEO tags, keep other metadata and asset links intact.
    raw=re.sub(r'<title>.*?</title>','',raw,flags=re.I|re.S)
    raw=re.sub(r'<meta\s+(?:name="(?:description|robots|keywords)"|property="(?:og:title|og:description|og:url|og:site_name|og:type|og:image)")[^>]*>', '',raw,flags=re.I)
    raw=re.sub(r'<link\s+rel="canonical"[^>]*>', '',raw,flags=re.I)
    name=settings.get('site_name') or BRAND
    desc=(description or '').strip()[:300]
    additions=[f'<title>{esc(title)}</title>',f'<meta name="description" content="{esc(desc)}">',
               f'<link rel="canonical" href="{esc(url)}">',
               f'<meta property="og:type" content="{"article" if ldtype == "BlogPosting" else "website"}">',
               f'<meta property="og:title" content="{esc(title)}">',f'<meta property="og:description" content="{esc(desc)}">',
               f'<meta property="og:url" content="{esc(url)}">', f'<meta property="og:site_name" content="{esc(name)}">',
               '<meta name="twitter:card" content="summary_large_image">']
    if not should_index(request,settings):additions.append('<meta name="robots" content="noindex,nofollow">')
    img=safe_media(ogimage or settings.get('social_image_url'),base)
    if img:additions.append('<meta property="og:image" content="'+esc(img)+'">')
    if settings.get('favicon_url'):
        additions.append('<link rel="icon" href="'+esc(settings['favicon_url'])+'">')
    org={'@context':'https://schema.org','@type':'Organization','name':name,'url':base+'/',
         'logo':safe_media(settings.get('logo_url'),base) or None}
    org={k:v for k,v in org.items() if v is not None}
    additions.append(structured(org))
    additions.append(structured({'@context':'https://schema.org','@type':ldtype,
                'name':title,'description':desc,'url':url,'isPartOf':{'@type':'WebSite','name':name,'url':base+'/'}}))
    raw=raw.replace('</head>','\n'.join(additions)+'\n</head>',1)
    raw=branding_markup(raw,settings)
    return HTMLResponse(raw, headers={'Cache-Control':'no-store,max-age=0','X-Content-Type-Options':'nosniff'})

def blog_post(con,request,settings,slug):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,148}',slug):raise HTTPException(404,'Article not found')
    article=con.execute("SELECT slug,title,category,summary,body,cover_url,published_at,updated_at FROM growth_articles WHERE slug=? AND status='published'",(slug,)).fetchone()
    if not article:raise HTTPException(404,'Article not found')
    base=public_base(request);title=article['title'];desc=article['summary'];url=base+'/news/'+slug
    image=safe_media(article['cover_url'],base)
    doc={'@context':'https://schema.org','@type':'BlogPosting','headline':title,'description':desc,'url':url,
         'datePublished':article['published_at'],'dateModified':article['updated_at'] or article['published_at'],
         'author':{'@type':'Organization','name':settings.get('site_name') or BRAND}}
    if image:doc['image']=[image]
    body=''.join('<p>'+esc(p)+'</p>' for p in re.split(r'\n\s*\n',article['body']) if p.strip())
    content=f'''<!doctype html><html lang="en-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/public_seo.css"><script defer src="/static/public_nav.js"></script></head><body>
<header class="seo-nav"><a href="/" class="seo-logo site-logo">{esc(settings.get('site_name') or BRAND)}</a><nav><a href="/">Home</a><a href="/news">News & Guides</a><a href="/discover/vendors">Find vendors</a><a href="/for-vendors">For vendors</a><a href="/app">Plan a party</a></nav></header>
<main class="seo-container"><nav class="seo-crumb"><a href="/">Home</a> / <a href="/news">News & Guides</a> / {esc(title)}</nav><article><p class="seo-kicker">{esc(article['category'])} · {esc(article['published_at'][:10])}</p><h1>{esc(title)}</h1><p class="seo-summary">{esc(desc)}</p>{('<img class="seo-cover" src="'+esc(image)+'" alt="">') if image else ''}<div class="seo-body">{body}</div></article><a class="seo-cta" href="/app?mode=signup">Start planning with Honestly Zen →</a></main>
<footer class="seo-footer"><a href="/news">Browse more planning guides</a> · <a href="/">{esc(settings.get('site_name') or BRAND)}</a></footer></body></html>'''
    raw=enhance_marketing(content,request,settings,'/news/'+slug,title+' | '+(settings.get('site_name') or BRAND),desc,ldtype='BlogPosting',ogimage=article['cover_url']).body.decode('utf-8')
    raw=raw.replace('</head>',structured(doc)+'</head>',1)
    return HTMLResponse(raw,headers={'Cache-Control':'public, max-age=300'})

def vendor_page(con,request,settings,ident):
    """Server-rendered product-style vendor page, accessible to search engines."""
    if not re.fullmatch(r'[a-f0-9]{32}',ident):raise HTTPException(404,'Vendor not found')
    row=con.execute("""SELECT business_name,category,description,city,other_community,price_mode,starting_price,
        image_url,verification_status,updated_at FROM market_listings WHERE id=? AND publication_status='published'""",(ident,)).fetchone()
    if not row:raise HTTPException(404,'Vendor not found')
    base=public_base(request)
    location=row['other_community'] if row['city']=='Other Alberta community' else row['city']
    title=f"{row['business_name']} — {row['category']} in {location}, Alberta | {settings.get('site_name') or BRAND}"
    desc=(row['description'] or '')[:190]
    image=safe_media(row['image_url'],base)
    price=('Custom quote' if row['price_mode']=='quote' else ('Fixed price' if row['price_mode']=='fixed' else 'Starting at') + ' CAD $'+str(row['starting_price']))
    detail=con.execute('SELECT * FROM market_listing_details WHERE listing_id=?',(ident,)).fetchone()
    def parse_items(key):
        if not detail:return []
        try:
            values=json.loads(detail[key]);return values if isinstance(values,list) else []
        except (ValueError,TypeError,KeyError):return []
    highlights=parse_items('highlights_json')
    inclusions=parse_items('inclusions_json')
    packages=parse_items('packages_json')
    faqs=parse_items('faqs_json')
    photos=con.execute('SELECT image_url FROM market_listing_photos WHERE listing_id=? ORDER BY created_at ASC LIMIT 8',(ident,)).fetchall()
    gallery=[item for item in [image]+[safe_media(x['image_url'],base) for x in photos] if item]
    lead=detail['lead_time'] if detail else ''
    booking_note=detail['booking_note'] if detail else ''
    cards=''.join('<article class="seo-product-package"><div class="seo-product-package-head"><strong>'+esc(p.get('name',''))+'</strong><b>CAD $'+esc(p.get('price',''))+'</b></div><p>'+esc(p.get('description',''))+'</p></article>' for p in packages if isinstance(p,dict))
    faq_html=''.join('<details><summary>'+esc(f.get('question',''))+'</summary><p>'+esc(f.get('answer',''))+'</p></details>' for f in faqs if isinstance(f,dict))
    def bullet(items):return '<ul>'+''.join('<li>'+esc(i)+'</li>' for i in items if isinstance(i,str))+'</ul>'
    photo_html=('<img class="seo-product-photo" src="'+esc(gallery[0])+'" alt="'+esc(row['business_name'])+' services">' if gallery else '<div class="seo-product-placeholder">Vendor photos coming soon</div>')
    if len(gallery)>1:
        photo_html+='<div class="seo-product-thumbs">'+''.join('<img src="'+esc(img)+'" alt="Additional '+esc(row['business_name'])+' service photo" loading="lazy">' for img in gallery[1:])+'</div>'
    name=settings.get('site_name') or BRAND
    content=f'''<!doctype html><html lang="en-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/public_seo.css"><script defer src="/static/public_nav.js"></script></head><body>
<header class="seo-nav"><a class="seo-logo site-logo" href="/">{esc(name)}</a><nav><a href="/">Home</a><a href="/discover/vendors">Find vendors</a><a href="/for-vendors">For vendors</a></nav></header>
<main class="seo-container seo-product-container"><nav class="seo-crumb"><a href="/">Home</a> / <a href="/discover/vendors">Alberta Vendors</a> / {esc(row['business_name'])}</nav>
<div class="seo-product-layout"><article class="seo-product-content">{photo_html}<section><p class="seo-kicker">{esc(row['category'])} · {esc(location)}, Alberta</p><h1>{esc(row['business_name'])}</h1><p class="seo-summary">{esc(desc)}</p><h2>About this service</h2><div class="seo-body"><p>{esc(row['description'])}</p></div>
{('<h2>Highlights</h2>'+bullet(highlights)) if highlights else ''}{('<h2>What is included</h2>'+bullet(inclusions)) if inclusions else ''}
{('<h2>Packages & pricing options</h2><div class="seo-product-packages">'+cards+'</div>') if cards else ''}
{('<h2>Frequently asked questions</h2><div class="seo-product-faqs">'+faq_html+'</div>') if faq_html else ''}
</section></article><aside class="seo-product-order"><span class="seo-kicker">{esc(row['category'])}</span><h2>{esc(row['business_name'])}</h2><p class="seo-product-label">{'✓ Verified business' if row['verification_status']=='verified' else 'Verification not yet completed'}</p><p class="seo-product-price">{esc(price)}</p><dl><dt>Location</dt><dd>{esc(location)}, Alberta</dd>{('<dt>Recommended notice</dt><dd>'+esc(lead)+'</dd>') if lead else ''}</dl>{('<p class="seo-product-note">'+esc(booking_note)+'</p>') if booking_note else ''}<a class="seo-cta seo-product-cta" href="/app?mode=signup">Request a quote in Honestly Zen →</a><p class="seo-product-disclaimer">Contact the vendor to confirm availability, final pricing, inclusions and terms. Listings are not purchase commitments.</p></aside></div>
</main><footer class="seo-footer"><a href="/discover/vendors">Browse Alberta vendors</a> · <a href="/">{esc(name)}</a></footer></body></html>'''
    return enhance_marketing(content,request,settings,'/vendors/'+ident,title,desc,ogimage=row['image_url'])

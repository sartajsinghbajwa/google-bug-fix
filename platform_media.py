"""Operator-managed image uploads for platform branding and blog covers.

Uploads are re-encoded as safe WebP and stored in MariaDB/SQLite so WHC
application upgrades do not accidentally orphan local static files.
"""
from __future__ import annotations
import base64, binascii, io, uuid
from datetime import datetime, timezone
from fastapi import HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES=4*1024*1024
KINDS={'logo','favicon','social','blog'}
DDL='''CREATE TABLE IF NOT EXISTS platform_media (
 id VARCHAR(64) PRIMARY KEY,
 kind VARCHAR(24) NOT NULL,
 content_type VARCHAR(40) NOT NULL,
 contents LONGBLOB NOT NULL,
 created_at VARCHAR(40) NOT NULL,
 uploaded_by VARCHAR(191) NOT NULL
)'''

def transform(mime, encoded, kind):
    if mime not in ('image/jpeg','image/png','image/webp'):
        raise HTTPException(422,'Choose JPG, PNG or WebP')
    if not isinstance(encoded,str) or len(encoded)>((MAX_BYTES+2)//3*4+4):
        raise HTTPException(413,'Image must be under 4 MB')
    try: raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise HTTPException(422,'Invalid image encoding')
    if not raw or len(raw)>MAX_BYTES:raise HTTPException(413,'Image must be under 4 MB')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>25_000_000:
                raise HTTPException(422,'Unsupported image or dimensions')
            image=ImageOps.exif_transpose(im)
            max_px=256 if kind=='favicon' else 1200 if kind=='logo' else 2000
            image.thumbnail((max_px,max_px),Image.Resampling.LANCZOS)
            # Re-encoding strips EXIF and malicious trailing content.
            image=image.convert('RGBA' if image.mode in ('RGBA','LA') or 'transparency' in image.info else 'RGB')
            out=io.BytesIO();image.save(out,format='WEBP',quality=86,method=4)
    except (UnidentifiedImageError,OSError,ValueError):raise HTTPException(422,'Invalid or damaged image')
    return out.getvalue()

def install(app,db,get_user,mariadb=False):
    if not mariadb:
        with db() as con:con.execute(DDL)
    @app.post('/api/platform/media')
    def upload(request:Request,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'kind','content_type','data_base64'}:
            raise HTTPException(422,'Invalid image upload')
        kind=payload['kind']
        if kind not in KINDS:raise HTTPException(422,'Unknown image purpose')
        blob=transform(payload['content_type'],payload['data_base64'],kind)
        with db() as con:
            who=get_user(request,con)
            if who['platform_role']!='admin':raise HTTPException(403,'Platform administrator required')
            ident=uuid.uuid4().hex
            con.execute('INSERT INTO platform_media (id,kind,content_type,contents,created_at,uploaded_by) VALUES(?,?,?,?,?,?)',
                        (ident,kind,'image/webp',blob,datetime.now(timezone.utc).isoformat(),who['id']))
        return {'url':'/api/public/platform-media/'+ident,'kind':kind}
    @app.get('/api/public/platform-media/{image_id}')
    def image(request:Request,image_id:str):
        if not len(image_id)==32 or any(ch not in '0123456789abcdef' for ch in image_id):
            raise HTTPException(404,'Image not found')
        with db() as con:
            row=con.execute('SELECT kind,content_type,contents FROM platform_media WHERE id=?',(image_id,)).fetchone()
            if not row:raise HTTPException(404,'Image not found')
            url='/api/public/platform-media/'+image_id
            linked=con.execute('SELECT 1 FROM platform_site_settings WHERE value=? LIMIT 1',(url,)).fetchone()
            if not linked:
                linked=con.execute("SELECT 1 FROM growth_articles WHERE cover_url=? AND status='published' LIMIT 1",(url,)).fetchone()
            if not linked:
                try:
                    admin=get_user(request,con)['platform_role']=='admin'
                except HTTPException:admin=False
                if not admin:raise HTTPException(404,'Image not found')
            return Response(bytes(row['contents']),media_type=row['content_type'],headers={
                'Cache-Control':'public, max-age=600','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"default-src 'none'"})

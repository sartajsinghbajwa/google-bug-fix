"""EventOS event photo gallery with event-scoped guest links and optional moderation.

Image data is stored in SQLite only for test/demo use; migrate images to object
storage before a commercial SaaS launch. EXIF is removed on image re-encode.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from saas_access import limits
import cloud_media

MAX_UPLOAD = 8 * 1024 * 1024
MAX_PHOTOS = 250
MAX_GALLERY_BYTES = 120 * 1024 * 1024
MIMES = {'image/jpeg','image/png','image/webp'}
Image.MAX_IMAGE_PIXELS = 26_000_000


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean_string(data,key,max_len,required=False):
    value=data.get(key,'')
    if not isinstance(value,str) or len(value)>max_len or (required and not value.strip()):
        raise HTTPException(422,'Invalid '+key)
    return value.strip()


def binary_image(data):
    if not isinstance(data,dict):
        raise HTTPException(422,'Invalid upload')
    mime=data.get('content_type')
    if mime not in MIMES:
        raise HTTPException(422,'Only JPEG, PNG or WebP images are supported')
    encoded=data.get('data_base64')
    if not isinstance(encoded,str) or len(encoded) > (MAX_UPLOAD*4//3+16):
        raise HTTPException(413,'Photo exceeds 8 MB limit')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise HTTPException(422,'Invalid image encoding')
    if not raw or len(raw)>MAX_UPLOAD:raise HTTPException(413,'Photo exceeds 8 MB limit')
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ('JPEG','PNG','WEBP'):
                raise HTTPException(422,'Unsupported image format')
            if img.width*img.height>26_000_000:
                raise HTTPException(413,'Image dimensions too large')
            img.seek(0)
            img=ImageOps.exif_transpose(img)
            img.thumbnail((1920,1920),Image.Resampling.LANCZOS)
            if img.mode not in ('RGB','RGBA'):
                img=img.convert('RGB')
            if img.mode=='RGBA':
                base=Image.new('RGB',img.size,'white')
                base.paste(img,mask=img.getchannel('A'))
                img=base
            full=io.BytesIO()
            img.save(full,format='JPEG',quality=84,optimize=True,progressive=True)
            thumb=img.copy()
            thumb.thumbnail((440,440),Image.Resampling.LANCZOS)
            small=io.BytesIO()
            thumb.save(small,format='JPEG',quality=76,optimize=True)
            return full.getvalue(),small.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(422,'Could not decode a valid photo')


def migrate(db):
    cloud_media.migrate(db)
    with db() as con:
        con.executescript("""
          CREATE TABLE IF NOT EXISTS event_galleries(
            event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            access_token TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 0,
            allow_guest_uploads INTEGER NOT NULL DEFAULT 0,
            require_approval INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL DEFAULT 'Our Event Memories',
            updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS event_gallery_photos(
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            submitted_by TEXT NOT NULL DEFAULT '',
            uploader_tag TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'image/jpeg',
            full_image BLOB NOT NULL,
            thumbnail BLOB NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
            created_at TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS idx_gallery_event ON event_gallery_photos(event_id,status,created_at);
        """)


def ensure(con,wid,eid):
    row=con.execute('SELECT * FROM event_galleries WHERE event_id=? AND workspace_id=?',(eid,wid)).fetchone()
    if row:return dict(row)
    con.execute("""INSERT INTO event_galleries(event_id,workspace_id,access_token,updated_at)
      VALUES (?,?,?,?)""",(eid,wid,secrets.token_urlsafe(30),timestamp()))
    return dict(con.execute('SELECT * FROM event_galleries WHERE event_id=?',(eid,)).fetchone())


def guest_scope(con,token):
    if not isinstance(token,str) or len(token)>160:
        raise HTTPException(404,'Gallery unavailable')
    g=con.execute('''SELECT x.*, e.name AS event_name
        FROM event_galleries x JOIN events e ON e.id=x.event_id
        WHERE x.access_token=?''',(token,)).fetchone()
    if not g or not g['enabled']:
        raise HTTPException(404,'Gallery unavailable')
    limits(con,g['workspace_id'])
    return dict(g)


def public_metadata(photo):
    return {key:photo[key] for key in ('id','caption','submitted_by','created_at')}


def list_photos(con,eid,moderator=False):
    query='''SELECT id,caption,submitted_by,status,created_at,filename
        FROM event_gallery_photos WHERE event_id=?'''
    if not moderator:query+=" AND status='approved'"
    query+=' ORDER BY created_at DESC,id DESC LIMIT 250'
    return [dict(x) for x in con.execute(query,(eid,))]


def add_photo(con,gallery,payload,editor=False,tag=''):
    if not editor and (not gallery['enabled'] or not gallery['allow_guest_uploads']):
        raise HTTPException(403,'Guest photo uploads are not enabled')
    if not isinstance(payload,dict) or set(payload)-{'filename','content_type','data_base64','caption','submitted_by'}:
        raise HTTPException(422,'Invalid photo information')
    name=clean_string(payload,'filename',120,True)
    if '/' in name or '\\' in name:raise HTTPException(422,'Invalid filename')
    caption=clean_string(payload,'caption',200)
    who=clean_string(payload,'submitted_by',70)
    full,thumb=binary_image(payload)
    count,size=con.execute("""SELECT COUNT(*),COALESCE(SUM(LENGTH(full_image)+LENGTH(thumbnail)+
        (SELECT COALESCE(SUM(byte_size),0) FROM cloud_media_objects
          WHERE entity_kind='gallery_photo' AND entity_id=p.id)),0)
        FROM event_gallery_photos p WHERE event_id=?""",(gallery['event_id'],)).fetchone()
    if count>=MAX_PHOTOS or size+len(full)+len(thumb)>MAX_GALLERY_BYTES:
        raise HTTPException(409,'Gallery photo storage limit reached')
    if not editor:
        cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds')
        recent=con.execute("SELECT COUNT(*) FROM event_gallery_photos WHERE event_id=? AND uploader_tag=? AND created_at>=?",(gallery['event_id'],tag,cutoff)).fetchone()[0]
        if recent>=15:
            raise HTTPException(429,'Daily upload limit reached for this invitation link')
    status='approved' if editor or not gallery['require_approval'] else 'pending'
    ident=str(uuid.uuid4())
    external=cloud_media.upload_pair(con,'gallery_photo',ident,gallery['workspace_id'],gallery['event_id'],full,thumb)
    con.execute('''INSERT INTO event_gallery_photos(
      id,event_id,workspace_id,submitted_by,uploader_tag,caption,filename,
      content_type,full_image,thumbnail,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (ident,gallery['event_id'],gallery['workspace_id'],who,tag,caption,name,'image/jpeg',b'' if external else full,b'' if external else thumb,status,timestamp()))
    return {'id':ident,'status':status,'caption':caption,'submitted_by':who}


def photo_response(con,eid,pid,thumb,public=False):
    p=con.execute('''SELECT * FROM event_gallery_photos WHERE id=? AND event_id=?''',(pid,eid)).fetchone()
    if not p or (public and p['status']!='approved'):
        raise HTTPException(404,'Photo not found')
    body=bytes(p['thumbnail'] if thumb else p['full_image'])
    if not body:
        body=cloud_media.read(con,'gallery_photo',pid,'thumb' if thumb else 'full',p['workspace_id'],eid)
    return Response(body,
        media_type='image/jpeg',headers={'Cache-Control':'private, max-age=60','X-Content-Type-Options':'nosniff',
           'Content-Security-Policy':"default-src 'none'"})


def install_gallery(app,db,access,check_event):
    migrate(db)

    @app.get('/api/w/{wid}/events/{eid}/gallery')
    def admin_list(request:Request,wid:str,eid:str):
        with db() as con:
            membership=access(request,con,wid)
            check_event(con,wid,eid)
            g=ensure(con,wid,eid)
            photos=list_photos(con,eid,True)
            return {'settings':{key:g[key] for key in ('enabled','allow_guest_uploads','require_approval','title')},
                    'share_url':'/photos/'+g['access_token'],
                    'photos':photos,'can_edit':membership['role'] in ('owner','editor'),
                    'counts':{'approved':sum(x['status']=='approved' for x in photos),
                              'pending':sum(x['status']=='pending' for x in photos)}}

    @app.patch('/api/w/{wid}/events/{eid}/gallery')
    def admin_settings(request:Request,wid:str,eid:str,payload:dict):
        allowed={'enabled','allow_guest_uploads','require_approval','title'}
        if not isinstance(payload,dict) or not payload or set(payload)-allowed:
            raise HTTPException(422,'Invalid gallery settings')
        changes={}
        for key,value in payload.items():
            if key=='title':changes[key]=clean_string(payload,key,100,True)
            elif type(value) is bool:changes[key]=int(value)
            else:raise HTTPException(422,'Gallery setting must be a boolean')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            ensure(con,wid,eid)
            sql=','.join(k+'=?' for k in changes)
            con.execute('UPDATE event_galleries SET '+sql+',updated_at=? WHERE event_id=? AND workspace_id=?',(*changes.values(),timestamp(),eid,wid))
        return {'ok':True}

    @app.post('/api/w/{wid}/events/{eid}/gallery/rotate-link')
    def rotate(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            ensure(con,wid,eid)
            token=secrets.token_urlsafe(30)
            con.execute('UPDATE event_galleries SET access_token=?,updated_at=? WHERE event_id=?',(token,timestamp(),eid))
            return {'share_url':'/photos/'+token}

    @app.post('/api/w/{wid}/events/{eid}/gallery/photos')
    def admin_upload(request:Request,wid:str,eid:str,payload:dict):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            g=ensure(con,wid,eid)
            return add_photo(con,g,payload,True)

    @app.patch('/api/w/{wid}/events/{eid}/gallery/photos/{pid}')
    def moderate(request:Request,wid:str,eid:str,pid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'status'} or payload['status'] not in ('approved','rejected','pending'):
            raise HTTPException(422,'Choose pending, approved or rejected')
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if not con.execute('UPDATE event_gallery_photos SET status=? WHERE id=? AND event_id=? AND workspace_id=?',
                (payload['status'],pid,eid,wid)).rowcount:
                raise HTTPException(404,'Photo not found')
        return {'ok':True}

    @app.delete('/api/w/{wid}/events/{eid}/gallery/photos/{pid}')
    def delete_photo(request:Request,wid:str,eid:str,pid:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if not con.execute('DELETE FROM event_gallery_photos WHERE id=? AND event_id=? AND workspace_id=?',(pid,eid,wid)).rowcount:
                raise HTTPException(404,'Photo not found')
            cloud_media.delete(con,'gallery_photo',pid,wid,eid)
        return {'ok':True}

    @app.get('/api/w/{wid}/events/{eid}/gallery/photos/{pid}/{size}')
    def staff_image(request:Request,wid:str,eid:str,pid:str,size:str):
        if size not in ('thumb','full'):raise HTTPException(404,'Photo not found')
        with db() as con:
            access(request,con,wid);check_event(con,wid,eid)
            return photo_response(con,eid,pid,size=='thumb')

    @app.get('/api/gallery/{token}')
    def public_list(token:str):
        with db() as con:
            g=guest_scope(con,token)
            photos=list_photos(con,g['event_id'])
            return {'event_name':g['event_name'],'title':g['title'],
                    'allow_uploads':bool(g['allow_guest_uploads']),
                    'approval_required':bool(g['require_approval']),
                    'photos':[public_metadata(x) for x in photos],
                    'updated_at':g['updated_at']}

    @app.post('/api/gallery/{token}/photos')
    def guest_upload(request:Request,token:str,payload:dict):
        with db() as con:
            g=guest_scope(con,token)
            # Rate cap per connecting address, not the entire gallery share URL.
            # Reverse-proxy configuration must forward trusted client addresses.
            address=request.client.host if request.client else 'unknown'
            tag=hashlib.sha256((token+'|'+address).encode()).hexdigest()
            return add_photo(con,g,payload,False,tag)

    @app.get('/api/gallery/{token}/photos/{pid}/{size}')
    def guest_image(token:str,pid:str,size:str):
        if size not in ('thumb','full'):raise HTTPException(404,'Photo not found')
        with db() as con:
            g=guest_scope(con,token)
            return photo_response(con,g['event_id'],pid,size=='thumb',True)

    @app.get('/photos/{token}')
    def public_page(token:str):
        # Token validated before serving a public gallery screen.
        from pathlib import Path
        with db() as con:guest_scope(con,token)
        return FileResponse(Path(__file__).parent/'static'/'gallery_public.html',
            headers={'Cache-Control':'no-store'})

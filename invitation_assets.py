"""Event-scoped invitation media and owner-curated template catalog.

The short-lived Render demo uses SQLite BLOBs to avoid an external paid storage
service. Each read is authorized either by workspace membership or a guest RSVP
token for the SAME event; storage is not a public unrestricted uploads folder.
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request, Response
from invitation_designs import default_design, normalize_design

MAX_IMAGE = 4 * 1024 * 1024
MAX_PDF = 6 * 1024 * 1024
MAX_PHOTOS = 8
ALLOWED = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}

BUILTIN = [
    ('classic', 'Classic Elegance', 'Refined traditional invitation', '#946e50', 'serif', 'center'),
    ('garden', 'Garden Romance', 'Soft botanical celebration', '#64846e', 'serif', 'center'),
    ('modern', 'Modern Minimal', 'Clean contemporary layout', '#256a7b', 'sans', 'left'),
    ('celebration', 'Festive Celebration', 'Warm playful festivities', '#b45e66', 'sans', 'center'),
]


def schema(db):
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS invitation_media (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('invite','photo')),
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            contents BLOB NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_invitation_media_event ON invitation_media(event_id,kind);
        -- Separate table avoids rebuilding existing invitation_media tables whose kind CHECK
        -- predates cover uploads; retain existing invitations and gallery photos.
        CREATE TABLE IF NOT EXISTS invitation_covers (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            contents BLOB NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invitation_templates (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            design_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_invitation_templates_ws ON invitation_templates(workspace_id);
        CREATE TABLE IF NOT EXISTS invitation_template_covers (
            template_id TEXT PRIMARY KEY REFERENCES invitation_templates(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            filename TEXT NOT NULL, content_type TEXT NOT NULL, contents BLOB NOT NULL
        );
        ''')


def metadata(row):
    return {key: row[key] for key in ('id','kind','filename','content_type','caption','created_at')}


def event_media(con, eid):
    rows=con.execute('''SELECT id,kind,filename,content_type,caption,created_at
         FROM invitation_media WHERE event_id=? ORDER BY created_at,id''',(eid,)).fetchall()
    cover=con.execute('''SELECT id, 'cover' AS kind, filename,content_type,
                      '' AS caption,created_at FROM invitation_covers WHERE event_id=?''',(eid,)).fetchone()
    return {'invite':next((metadata(r) for r in rows if r['kind']=='invite'),None),
            'cover':metadata(cover) if cover else None,
            'photos':[metadata(r) for r in rows if r['kind']=='photo']}


def verify_file(mime, contents):
    if mime == 'image/png' and contents[:8] == b'\x89PNG\r\n\x1a\n': return
    if mime == 'image/jpeg' and contents[:3] == b'\xff\xd8\xff': return
    if mime == 'image/webp' and contents[:4] == b'RIFF' and contents[8:12] == b'WEBP': return
    if mime == 'application/pdf' and contents[:5] == b'%PDF-': return
    raise HTTPException(422, 'File data does not match its image/PDF type')


def decode_upload(payload):
    if not isinstance(payload, dict): raise HTTPException(422,'Invalid upload')
    kind=payload.get('kind')
    if kind not in ('invite','photo','cover'): raise HTTPException(422,'Choose an invitation, cover photo or gallery photo')
    mime=payload.get('content_type')
    if mime not in ALLOWED or (kind in ('photo','cover') and mime=='application/pdf'):
        raise HTTPException(422,'Choose a JPEG, PNG, WebP image or invitation PDF')
    name=payload.get('filename','')
    caption=payload.get('caption','')
    if not isinstance(name,str) or len(name)>130 or not name.strip() or any(x in name for x in ('/','\\','\x00')):
        raise HTTPException(422,'Invalid filename')
    if not isinstance(caption,str) or len(caption)>120:
        raise HTTPException(422,'Caption must be 120 characters or less')
    encoded=payload.get('data_base64')
    limit=MAX_PDF if mime=='application/pdf' else MAX_IMAGE
    if not isinstance(encoded,str) or len(encoded)>((limit+2)//3*4)+4:
        raise HTTPException(413,'Upload is too large')
    try: content=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError): raise HTTPException(422,'Invalid encoded file')
    if not content or len(content)>limit: raise HTTPException(413,'Upload is empty or too large')
    verify_file(mime,content)
    return kind,mime,name.strip(),caption.strip(),content


def library_design(design):
    if not isinstance(design,dict): raise HTTPException(422,'Invalid template design')
    # A template is a reusable builder style, never a pointer to someone's uploaded media.
    d=normalize_design({**default_design(),**design})
    # Reusable templates store artwork and text placeholders, never event-specific
    # uploaded images (which live in event-scoped protected storage).
    d['mode']='canvas' if d['mode']=='canvas' else 'builder'
    d['canvas_use_cover']=False
    return d


def catalog(con, wid):
    builtin=[]
    for key,name,description,accent,font,align in BUILTIN:
        d=default_design()
        d.update(template=key,accent_color=accent,font=font,alignment=align)
        builtin.append({'id':'built-in:'+key,'name':name,'description':description,'design':d,'built_in':True,'has_background':False})
    custom=[]
    for r in con.execute('SELECT id,name,description,design_json FROM invitation_templates WHERE workspace_id=? ORDER BY name,id',(wid,)):
        custom.append({'id':r['id'],'name':r['name'],'description':r['description'],
                       'design':json.loads(r['design_json']),'built_in':False,
                       'has_background':bool(con.execute('SELECT 1 FROM invitation_template_covers WHERE template_id=? AND workspace_id=?',(r['id'],wid)).fetchone())})
    return builtin+custom


def owner_only(role):
    if role != 'owner': raise HTTPException(403,'Only workspace administrators can manage templates')


def valid_template(payload):
    if not isinstance(payload,dict) or not {'name','description','design'} <= set(payload) or set(payload)-{'name','description','design','background_event_id'}:
        raise HTTPException(422,'Template requires name, description and design')
    name=payload['name']; description=payload['description']
    if not isinstance(name,str) or not 1<=len(name.strip())<=75:
        raise HTTPException(422,'Template name must be 1–75 characters')
    if not isinstance(description,str) or len(description)>200:
        raise HTTPException(422,'Template description too long')
    design=library_design(payload['design'])
    background_event_id=payload.get('background_event_id')
    if background_event_id is not None and (not isinstance(background_event_id,str) or len(background_event_id)>100):
        raise HTTPException(422,'Invalid background event ID')
    return name.strip(),description.strip(),design,background_event_id


def install_invitation_assets(app,db,access,check_event):
    schema(db)

    def update_template_background(con,wid,tid,source_eid):
        if source_eid is None:
            return
        if not source_eid:
            con.execute('DELETE FROM invitation_template_covers WHERE template_id=? AND workspace_id=?',(tid,wid))
            return
        check_event(con,wid,source_eid)
        cover=con.execute('SELECT filename,content_type,contents FROM invitation_covers WHERE workspace_id=? AND event_id=?',(wid,source_eid)).fetchone()
        if not cover:
            raise HTTPException(422,'Upload a background to the event before saving it as a template')
        con.execute('''INSERT INTO invitation_template_covers(template_id,workspace_id,filename,content_type,contents)
            VALUES(?,?,?,?,?) ON CONFLICT(template_id) DO UPDATE SET workspace_id=excluded.workspace_id,
            filename=excluded.filename,content_type=excluded.content_type,contents=excluded.contents''',
            (tid,wid,cover['filename'],cover['content_type'],cover['contents']))


    @app.get('/api/w/{wid}/invitation-templates')
    def templates_get(request:Request,wid:str):
        with db() as con:
            role=access(request,con,wid)['role']
            return {'templates':catalog(con,wid),'can_manage':role=='owner'}

    @app.post('/api/w/{wid}/invitation-templates')
    def templates_create(request:Request,wid:str,payload:dict[str,Any]):
        name,desc,design,background_event_id=valid_template(payload)
        with db() as con:
            owner_only(access(request,con,wid)['role'])
            getattr(app.state, 'validate_design_asset_references', lambda *_: None)(con,wid,design)
            if con.execute('SELECT COUNT(*) FROM invitation_templates WHERE workspace_id=?',(wid,)).fetchone()[0]>=40:
                raise HTTPException(409,'Template library limit reached')
            ident=str(uuid.uuid4())
            con.execute('INSERT INTO invitation_templates VALUES(?,?,?,?,?,?)',
                        (ident,wid,name,desc,json.dumps(design),datetime.now(timezone.utc).isoformat()))
            update_template_background(con,wid,ident,background_event_id)
        return {'id':ident,'name':name,'description':desc,'design':design,'built_in':False,
                'has_background':bool(background_event_id)}

    @app.put('/api/w/{wid}/invitation-templates/{tid}')
    def templates_update(request:Request,wid:str,tid:str,payload:dict[str,Any]):
        name,desc,design,background_event_id=valid_template(payload)
        with db() as con:
            owner_only(access(request,con,wid)['role'])
            getattr(app.state, 'validate_design_asset_references', lambda *_: None)(con,wid,design)
            if con.execute('UPDATE invitation_templates SET name=?,description=?,design_json=?,updated_at=? WHERE workspace_id=? AND id=?',
                           (name,desc,json.dumps(design),datetime.now(timezone.utc).isoformat(),wid,tid)).rowcount!=1:
                raise HTTPException(404,'Template not found')
            update_template_background(con,wid,tid,background_event_id)
            has_background=bool(con.execute('SELECT 1 FROM invitation_template_covers WHERE template_id=? AND workspace_id=?',(tid,wid)).fetchone())
        return {'id':tid,'name':name,'description':desc,'design':design,'built_in':False,'has_background':has_background}

    @app.delete('/api/w/{wid}/invitation-templates/{tid}')
    def templates_delete(request:Request,wid:str,tid:str):
        with db() as con:
            owner_only(access(request,con,wid)['role'])
            if con.execute('DELETE FROM invitation_templates WHERE workspace_id=? AND id=?',(wid,tid)).rowcount!=1:
                raise HTTPException(404,'Template not found')
        return {'deleted':True}


    @app.post('/api/w/{wid}/events/{eid}/invitation-templates/{tid}/apply-background')
    def apply_template_background(request:Request,wid:str,eid:str,tid:str):
        with db() as con:
            access(request,con,wid,True)
            check_event(con,wid,eid)
            source=con.execute('''SELECT filename,content_type,contents FROM invitation_template_covers
                WHERE workspace_id=? AND template_id=?''',(wid,tid)).fetchone()
            if not source:
                raise HTTPException(404,'Template background not found')
            ident=str(uuid.uuid4())
            now=datetime.now(timezone.utc).isoformat()
            con.execute('''INSERT INTO invitation_covers (id,workspace_id,event_id,filename,content_type,contents,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET id=excluded.id,
                filename=excluded.filename,content_type=excluded.content_type,
                contents=excluded.contents,created_at=excluded.created_at''',
                (ident,wid,eid,source['filename'],source['content_type'],source['contents'],now))
        return {'ok':True,'background_id':ident}

    @app.get('/api/w/{wid}/events/{eid}/invitation-media')
    def media_list(request:Request,wid:str,eid:str):
        with db() as con:
            access(request,con,wid);check_event(con,wid,eid)
            return event_media(con,eid)

    @app.post('/api/w/{wid}/events/{eid}/invitation-media')
    def media_upload(request:Request,wid:str,eid:str,payload:dict[str,Any]):
        kind,mime,name,caption,content=decode_upload(payload)
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            if kind=='cover':
                ident=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
                con.execute('''INSERT INTO invitation_covers
                    (id,workspace_id,event_id,filename,content_type,contents,created_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                    id=excluded.id, filename=excluded.filename, content_type=excluded.content_type,
                    contents=excluded.contents, created_at=excluded.created_at''',
                    (ident,wid,eid,name,mime,content,now))
                return {'id':ident,'kind':'cover','filename':name,'content_type':mime,'caption':'','created_at':now}
            if kind=='photo' and con.execute('SELECT COUNT(*) FROM invitation_media WHERE event_id=? AND kind=?',(eid,'photo')).fetchone()[0]>=MAX_PHOTOS:
                raise HTTPException(409,'A maximum of eight gallery photos is supported')
            if kind=='invite':con.execute('DELETE FROM invitation_media WHERE event_id=? AND kind=?',(eid,'invite'))
            ident=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
            con.execute('INSERT INTO invitation_media VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident,wid,eid,kind,name,mime,content,caption,now))
        return {'id':ident,'kind':kind,'filename':name,'content_type':mime,'caption':caption,'created_at':now}

    @app.delete('/api/w/{wid}/events/{eid}/invitation-media/{asset_id}')
    def media_delete(request:Request,wid:str,eid:str,asset_id:str):
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            deleted=con.execute('DELETE FROM invitation_media WHERE workspace_id=? AND event_id=? AND id=?',(wid,eid,asset_id)).rowcount
            if not deleted:
                deleted=con.execute('DELETE FROM invitation_covers WHERE workspace_id=? AND event_id=? AND id=?',(wid,eid,asset_id)).rowcount
            if not deleted:
                raise HTTPException(404,'Invitation asset not found')
        return {'deleted':True}

    def get_media(con,eid,asset_id):
        row=con.execute('SELECT * FROM invitation_media WHERE event_id=? AND id=?',(eid,asset_id)).fetchone()
        if row is None:
            row=con.execute('SELECT * FROM invitation_covers WHERE event_id=? AND id=?',(eid,asset_id)).fetchone()
        if row is None:raise HTTPException(404,'Media not found')
        return Response(bytes(row['contents']),media_type=row['content_type'],
                       headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
                                'Content-Security-Policy':'sandbox' if row['content_type']=='application/pdf' else "default-src 'none'",
                                'Content-Disposition': 'attachment; filename="invitation.pdf"' if row['content_type']=='application/pdf' else 'inline'})

    @app.get('/api/w/{wid}/events/{eid}/invitation-media/{asset_id}')
    def media_admin_view(request:Request,wid:str,eid:str,asset_id:str):
        with db() as con:
            access(request,con,wid);check_event(con,wid,eid)
            return get_media(con,eid,asset_id)

    @app.get('/api/rsvp/{token}/media/{asset_id}')
    def media_guest_view(token:str,asset_id:str):
        with db() as con:
            guest=con.execute('SELECT event_id FROM guests WHERE invitation_token=?',(token,)).fetchone()
            if not guest:raise HTTPException(404,'Invitation not found')
            return get_media(con,guest['event_id'],asset_id)

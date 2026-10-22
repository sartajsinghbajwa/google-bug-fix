"""Workspace-admin artwork and font library for editable invitation canvases.

Images are raster-only (SVG is intentionally not accepted). Font uploads must match
their binary signatures. Guests may fetch only assets referenced by their own
event's saved invitation design, not the entire workspace collection.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
import io
from PIL import Image, ImageOps, UnidentifiedImageError
from datetime import datetime, timezone
from fastapi import HTTPException, Request, Response

IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp'}
FONT_MIMES = {
    'font/woff2': b'wOF2',
    'font/woff': b'wOFF',
    'font/ttf': b'\x00\x01\x00\x00',
    'font/otf': b'OTTO',
}
CATEGORIES = {'Flowers', 'Borders', 'Symbols', 'Cultural', 'Other'}
MAX_IMAGE = 2 * 1024 * 1024
MAX_FONT = 2 * 1024 * 1024


def install_design_asset_library(app, db, access, check_event):
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS invitation_design_assets (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            kind TEXT NOT NULL CHECK(kind IN ('clipart','font')),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            contents BLOB NOT NULL,
            created_at TEXT NOT NULL,
            thumbnail BLOB
        )''')
        con.execute('''CREATE INDEX IF NOT EXISTS idx_design_assets_workspace
            ON invitation_design_assets(workspace_id,kind,category)''')

    with db() as con:
        cols={x['name'] for x in con.execute('PRAGMA table_info(invitation_design_assets)')}
        if 'thumbnail' not in cols:
            con.execute('ALTER TABLE invitation_design_assets ADD COLUMN thumbnail BLOB')

    def thumbnail_of(mime, blob):
        if mime not in IMAGE_MIMES:
            return None
        try:
            with Image.open(io.BytesIO(blob)) as image:
                if image.width*image.height>25_000_000:
                    raise HTTPException(413,'Image dimensions too large')
                image=ImageOps.exif_transpose(image)
                image.thumbnail((160,160), Image.Resampling.LANCZOS)
                converted=image.convert('RGBA' if 'A' in image.getbands() or image.info.get('transparency') is not None else 'RGB')
                output=io.BytesIO()
                converted.save(output,format='PNG',optimize=True)
                return output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            return None

    def validate_references(con, wid, design):
        refs=[]
        for item in design.get('canvas_items', []):
            if item.get('kind')=='asset':
                refs.append((item['asset_id'],'clipart'))
            if item.get('kind')=='text' and item.get('font','').startswith('font:'):
                refs.append((item['font'][5:],'font'))
        if design.get('font','').startswith('font:'):
            refs.append((design['font'][5:],'font'))
        if design.get('canvas_font','').startswith('font:'):
            refs.append((design['canvas_font'][5:],'font'))
        for font in design.get('canvas_field_fonts',{}).values():
            if font.startswith('font:'):
                refs.append((font[5:],'font'))
        for ident,kind in set(refs):
            row=con.execute('''SELECT 1 FROM invitation_design_assets
                WHERE workspace_id=? AND id=? AND kind=?''',(wid,ident,kind)).fetchone()
            if not row:
                raise HTTPException(422,'A selected clipart or font is missing from this workspace library')

    app.state.validate_design_asset_references = validate_references

    def meta(row):
        return {key:row[key] for key in ('id','kind','name','category','filename','content_type')}

    def require_owner(request, con, wid, kind='clipart'):
        access(request, con, wid, True)
        from saas_access import is_admin
        # Membership is required for both; only platform admin can upload fonts.
        user=app.state.get_user(request,con) if hasattr(app.state,'get_user') else None
        if kind == 'font':
            if not user or not is_admin(con,user['id']):
                raise HTTPException(403,'Only EventOS platform administrators can upload or delete font files')
        elif access(request,con,wid,True)['role'] != 'owner' and not (user and is_admin(con,user['id'])):
            raise HTTPException(403,'Only workspace owners can manage clipart')

    def verify(kind, mime, blob):
        if kind == 'clipart':
            if mime not in IMAGE_MIMES:
                raise HTTPException(422, 'Clipart must be PNG, JPEG or WebP (SVG is not supported)')
            if mime == 'image/png' and blob.startswith(b'\x89PNG\r\n\x1a\n'): return
            if mime == 'image/jpeg' and blob.startswith(b'\xff\xd8\xff'): return
            if mime == 'image/webp' and blob.startswith(b'RIFF') and blob[8:12] == b'WEBP': return
            raise HTTPException(422, 'Image contents do not match the selected format')
        if kind != 'font' or mime not in FONT_MIMES:
            raise HTTPException(422, 'Font must be WOFF2, WOFF, TTF or OTF')
        if not blob.startswith(FONT_MIMES[mime]):
            raise HTTPException(422, 'Font file contents do not match its format')

    @app.get('/api/w/{wid}/design-assets')
    def list_assets(request:Request,wid:str):
        with db() as con:
            member=access(request,con,wid)
            rows=con.execute('''SELECT id,kind,name,category,filename,content_type
                FROM invitation_design_assets WHERE workspace_id=? ORDER BY kind,category,name''',(wid,)).fetchall()
            return {'clipart':[meta(x) for x in rows if x['kind']=='clipart'],
                    'fonts':[meta(x) for x in rows if x['kind']=='font'],
                    'can_manage':member['role']=='owner' or app.state.get_user(request,con)['platform_role']=='admin',
                    'can_manage_fonts':app.state.get_user(request,con)['platform_role']=='admin'}

    @app.post('/api/w/{wid}/design-assets')
    def upload_asset(request:Request,wid:str,payload:dict):
        kind=payload.get('kind')
        mime=payload.get('content_type')
        name=payload.get('name')
        category=payload.get('category') if kind=='clipart' else 'Fonts'
        filename=payload.get('filename')
        encoded=payload.get('data_base64')
        if kind not in ('clipart','font') or not isinstance(name,str) or not 1<=len(name.strip())<=75:
            raise HTTPException(422, 'Choose an image or font and provide a short name')
        if not isinstance(category,str) or (kind=='clipart' and category not in CATEGORIES):
            raise HTTPException(422, 'Invalid clipart category')
        if not isinstance(filename,str) or not 1<=len(filename)<=130 or re.search(r'[/\\\x00]',filename):
            raise HTTPException(422,'Invalid filename')
        limit=MAX_IMAGE if kind=='clipart' else MAX_FONT
        if not isinstance(encoded,str) or len(encoded)>(limit+2)//3*4+4:
            raise HTTPException(413,'Asset exceeds 2 MB')
        try:
            blob=base64.b64decode(encoded,validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422,'Invalid encoded asset')
        if not blob or len(blob)>limit:
            raise HTTPException(413,'Asset must be under 2 MB')
        verify(kind,mime,blob)
        with db() as con:
            require_owner(request,con,wid,kind)
            count=con.execute('SELECT COUNT(*) FROM invitation_design_assets WHERE workspace_id=? AND kind=?',(wid,kind)).fetchone()[0]
            if count >= (100 if kind=='clipart' else 25):
                raise HTTPException(409,'Workspace asset library limit reached')
            ident=str(uuid.uuid4())
            thumb=thumbnail_of(mime,blob) if kind=='clipart' else None
            con.execute('''INSERT INTO invitation_design_assets
                (id,workspace_id,kind,name,category,filename,content_type,contents,created_at,thumbnail)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (ident,wid,kind,name.strip(),category,filename,mime,blob,
                 datetime.now(timezone.utc).isoformat(timespec='seconds'),thumb))
        return {'id':ident,'kind':kind,'name':name.strip(),'category':category,
                'filename':filename,'content_type':mime}

    @app.delete('/api/w/{wid}/design-assets/{asset_id}')
    def remove_asset(request:Request,wid:str,asset_id:str):
        with db() as con:
            present=con.execute('SELECT id,kind FROM invitation_design_assets WHERE id=? AND workspace_id=?',(asset_id,wid)).fetchone()
            if not present:raise HTTPException(404,'Asset not found')
            require_owner(request,con,wid,present['kind'])
            pattern='%'+asset_id+'%'
            used=con.execute('SELECT 1 FROM invitation_designs WHERE workspace_id=? AND design_json LIKE ? LIMIT 1',(wid,pattern)).fetchone()
            templ=con.execute('SELECT 1 FROM invitation_templates WHERE workspace_id=? AND design_json LIKE ? LIMIT 1',(wid,pattern)).fetchone()
            if used or templ:
                raise HTTPException(409,'This asset is used in a saved invitation or template. Remove it from those designs first.')
            con.execute('DELETE FROM invitation_design_assets WHERE id=? AND workspace_id=?',(asset_id,wid))
        return {'deleted':True}

    def send_asset(row):
        return Response(bytes(row['contents']), media_type=row['content_type'],headers={
            'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'",
        })

    @app.get('/api/w/{wid}/design-assets/{asset_id}')
    def workspace_asset(request:Request,wid:str,asset_id:str):
        with db() as con:
            access(request,con,wid)
            row=con.execute('SELECT * FROM invitation_design_assets WHERE workspace_id=? AND id=?',(wid,asset_id)).fetchone()
            if not row:raise HTTPException(404,'Asset not found')
            return send_asset(row)

    @app.get('/api/w/{wid}/design-assets/{asset_id}/thumbnail')
    def workspace_thumbnail(request:Request,wid:str,asset_id:str):
        with db() as con:
            access(request,con,wid)
            row=con.execute('''SELECT contents,content_type,thumbnail,kind FROM invitation_design_assets
                WHERE workspace_id=? AND id=?''',(wid,asset_id)).fetchone()
            if not row or row['kind']!='clipart':raise HTTPException(404,'Artwork not found')
            thumb=bytes(row['thumbnail']) if row['thumbnail'] else thumbnail_of(row['content_type'],bytes(row['contents']))
            if thumb:
                if not row['thumbnail']:
                    con.execute('UPDATE invitation_design_assets SET thumbnail=? WHERE workspace_id=? AND id=?',
                                (thumb,wid,asset_id))
                return Response(thumb,media_type='image/png',
                                headers={'Cache-Control':'private,max-age=300','X-Content-Type-Options':'nosniff'})
            return Response(bytes(row['contents']),media_type=row['content_type'],
                            headers={'Cache-Control':'private,max-age=300','X-Content-Type-Options':'nosniff'})

    @app.get('/api/rsvp/{token}/design-assets/{asset_id}')
    def guest_asset(token:str,asset_id:str):
        with db() as con:
            guest=con.execute('SELECT event_id,workspace_id FROM guests WHERE invitation_token=?',(token,)).fetchone()
            if not guest:raise HTTPException(404,'Invitation not found')
            from invitation_designs import get_design
            design=get_design(con,guest['event_id'])
            if design.get('mode') not in ('builder','canvas'):
                raise HTTPException(404,'Asset not found')
            used=design.get('font') == 'font:'+asset_id
            used=used or asset_id in [item.get('asset_id') for item in design.get('canvas_items',[]) if item.get('kind')=='asset']
            used=used or design.get('canvas_font') == 'font:'+asset_id
            used=used or ('font:'+asset_id) in design.get('canvas_field_fonts',{}).values()
            used=used or any(item.get('font')=='font:'+asset_id for item in design.get('canvas_items',[]) if item.get('kind')=='text')
            if not used:raise HTTPException(404,'Asset not found')
            row=con.execute('SELECT * FROM invitation_design_assets WHERE workspace_id=? AND id=?',
                            (guest['workspace_id'],asset_id)).fetchone()
            if not row:raise HTTPException(404,'Asset not found')
            return send_asset(row)

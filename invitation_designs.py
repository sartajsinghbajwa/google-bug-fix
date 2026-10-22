"""Organizer-managed visual invitation designs for existing token-scoped RSVP pages.

Keep design data separate from RSVP questions and from guest data. Uploaded media is handled by the separate event-scoped media module.
"""
from __future__ import annotations

import json
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from invitation_library_assets import install_design_asset_library

TEMPLATES = {'classic', 'garden', 'modern', 'celebration'}
from google_fonts import offline_font
FONTS = {'serif', 'sans', 'script', 'display'}
FONT_REF = re.compile(r'^font:[0-9a-f-]{36}$')
ALIGNMENTS = {'center', 'left'}
HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
CANVAS_BACKGROUNDS = {'ivory', 'blush', 'sage', 'champagne', 'midnight', 'parchment'}
CLIPART = {'rose', 'leaf', 'flourish', 'rings', 'heart', 'stars', 'lotus', 'mandala', 'butterfly', 'bouquet'}
ITEM_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
CANVAS_FIELDS = ('host_line','headline','title','subtitle','date','location','closing_note')
CANVAS_POSITIONS = {
    'host_line': {'x': 50, 'y': 23, 'size': 3.5},
    'headline': {'x': 50, 'y': 35, 'size': 6.5},
    'title': {'x': 50, 'y': 48, 'size': 7.5},
    'subtitle': {'x': 50, 'y': 60, 'size': 3.5},
    'date': {'x': 50, 'y': 72, 'size': 3.2},
    'location': {'x': 50, 'y': 79, 'size': 3.2},
    'closing_note': {'x': 50, 'y': 90, 'size': 3.1},
}

def default_design():
    return {
        'mode': 'builder', 'template': 'classic', 'accent_color': '#946E50', 'font': 'serif',
        'alignment': 'center', 'headline': "You're invited", 'subtitle': '',
        'host_line': '', 'closing_note': 'We look forward to celebrating with you.',
        'cover_image_url': '', 'show_date': True, 'show_location': True,
        'canvas_background': 'ivory', 'canvas_use_cover': False,
        'canvas_title': '', 'canvas_date': '', 'canvas_location': '',
        'canvas_text_color': '#403735', 'canvas_font':'serif', 'canvas_hidden_fields': [], 'canvas_field_fonts': {},
        'canvas_positions': {k: dict(v) for k,v in CANVAS_POSITIONS.items()},
        'canvas_items': [],
        'guest_show_countdown': False, 'guest_music_url': '',
    }


def ensure_schema(db):
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS invitation_designs (
            event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            design_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')


def normalize_design(payload: Any):
    defaults = default_design()
    if not isinstance(payload, dict) or set(payload) != set(defaults):
        raise HTTPException(422, 'Invitation settings are missing or invalid')
    design = dict(payload)
    # Previously saved Google font IDs become local basic font selections.
    # Uploaded font IDs are not rewritten, and no saved invitation is deleted.
    design['font'] = offline_font(design.get('font'))
    design['canvas_font'] = offline_font(design.get('canvas_font'))
    if isinstance(design.get('canvas_field_fonts'), dict):
        design['canvas_field_fonts'] = {k: offline_font(v) for k,v in design['canvas_field_fonts'].items()}
    if isinstance(design.get('canvas_items'), list):
        design['canvas_items'] = [
            {**item, 'font': offline_font(item['font'])} if isinstance(item, dict) and
            item.get('kind') == 'text' and isinstance(item.get('font'), str) else item
            for item in design['canvas_items']
        ]
    if design['mode'] not in ('builder','upload','canvas') or not isinstance(design['mode'],str):
        raise HTTPException(422, 'Choose builder or uploaded invitation')
    if (not isinstance(design['template'], str) or design['template'] not in TEMPLATES or
            not isinstance(design['font'], str) or not (design['font'] in FONTS or FONT_REF.fullmatch(design['font'])) or
            not isinstance(design['alignment'], str) or design['alignment'] not in ALIGNMENTS):
        raise HTTPException(422, 'Choose a supported design template, font, and alignment')
    if not isinstance(design['accent_color'], str) or not HEX_COLOR.fullmatch(design['accent_color']):
        raise HTTPException(422, 'Accent color must be a six-digit hex color')
    design['accent_color'] = design['accent_color'].lower()
    for field, limit, required in [('headline', 120, True), ('subtitle', 240, False), ('host_line', 160, False), ('closing_note', 350, False)]:
        value = design[field]
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise HTTPException(422, f'Invalid {field.replace("_", " ")}')
        design[field] = value.strip()
    for field in ('show_date', 'show_location', 'guest_show_countdown'):
        if type(design[field]) is not bool:
            raise HTTPException(422, f'{field} must be true or false')
    image = design['cover_image_url']
    if not isinstance(image, str) or len(image) > 500:
        raise HTTPException(422, 'Invalid cover image URL')
    image = image.strip()
    if image:
        try:
            u = urlsplit(image)
            if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or
                    any(ord(c) < 33 for c in image) or u.port not in (None, 443) or '.' not in u.hostname or
                    u.hostname.lower().endswith(('.local', '.internal', '.localhost'))):
                raise ValueError()
            try:
                ipaddress.ip_address(u.hostname)
            except ValueError:
                pass
            else:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Cover image must be a public HTTPS URL without credentials')
    design['cover_image_url'] = image
    music = design['guest_music_url']
    if not isinstance(music, str) or len(music) > 500:
        raise HTTPException(422, 'Invalid guest music URL')
    music = music.strip()
    if music:
        try:
            u = urlsplit(music)
            if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or
                    any(ord(c) < 33 or c in '<>"\\' for c in music) or "'" in music or
                    u.port not in (None, 443) or '.' not in u.hostname or
                    u.hostname.lower().endswith(('.local', '.internal', '.localhost')) or
                    not u.path.lower().endswith(('.mp3', '.ogg', '.wav', '.m4a'))):
                raise ValueError()
            try:
                ipaddress.ip_address(u.hostname)
            except ValueError:
                pass
            else:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Music must be a direct public HTTPS audio file URL (MP3, OGG, WAV or M4A)')
    design['guest_music_url'] = music
    if not isinstance(design['canvas_background'],str) or design['canvas_background'] not in CANVAS_BACKGROUNDS:
        raise HTTPException(422, 'Choose a supported canvas background')
    if type(design['canvas_use_cover']) is not bool:
        raise HTTPException(422, 'Invalid canvas background image setting')
    if not isinstance(design['canvas_text_color'], str) or not HEX_COLOR.fullmatch(design['canvas_text_color']):
        raise HTTPException(422, 'Invalid canvas text color')
    design['canvas_text_color'] = design['canvas_text_color'].lower()
    if not isinstance(design['canvas_font'],str) or not (design['canvas_font'] in FONTS or FONT_REF.fullmatch(design['canvas_font'])):
        raise HTTPException(422, 'Invalid canvas font')
    for key,limit in [('canvas_title',160),('canvas_date',120),('canvas_location',160)]:
        if not isinstance(design[key],str) or len(design[key]) > limit:
            raise HTTPException(422, f'Invalid {key.replace("_"," ")}')
        design[key]=design[key].strip()
    def number(value, low, high):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not low<=value<=high:
            raise HTTPException(422, 'Canvas positions or sizes are outside allowed bounds')
        return round(float(value),2)
    hidden = design['canvas_hidden_fields']
    if (not isinstance(hidden,list) or len(hidden)>len(CANVAS_FIELDS) or
            any(not isinstance(key,str) or key not in CANVAS_FIELDS for key in hidden) or
            len(set(hidden)) != len(hidden)):
        raise HTTPException(422,'Invalid hidden canvas text fields')
    field_fonts=design['canvas_field_fonts']
    if not isinstance(field_fonts,dict) or any(not isinstance(key,str) or key not in CANVAS_FIELDS
       or not isinstance(font,str) or not (font in FONTS or FONT_REF.fullmatch(font))
       for key,font in field_fonts.items()):
        raise HTTPException(422,'Invalid text layer fonts')
    positions = design['canvas_positions']
    if not isinstance(positions,dict) or set(positions) != set(CANVAS_FIELDS):
        raise HTTPException(422, 'Invalid canvas text positions')
    for key,pos in positions.items():
        if not isinstance(pos,dict) or set(pos) != {'x','y','size'}:
            raise HTTPException(422, 'Invalid canvas text block position')
        positions[key]={'x':number(pos['x'],0,100),'y':number(pos['y'],0,100),
                        'size':number(pos['size'],1.6,12)}
    items = design['canvas_items']
    if not isinstance(items,list) or len(items) > 40:
        raise HTTPException(422, 'Use up to 40 decorations and custom text elements')
    ids=set()
    for item in items:
        if not isinstance(item,dict):
            raise HTTPException(422, 'Invalid canvas item')
        kind=item.get('kind')
        base={'id','kind','x','y','size','rotation','color'}
        required=base | ({'art'} if kind=='clipart' else {'text','font'} if kind=='text' else {'asset_id'} if kind=='asset' else set())
        if kind not in ('clipart','text','asset') or set(item)!=required:
            raise HTTPException(422, 'Invalid canvas item fields')
        item_id=item['id']
        if not isinstance(item_id,str) or not ITEM_ID.fullmatch(item_id) or item_id in ids:
            raise HTTPException(422, 'Canvas item IDs must be unique')
        ids.add(item_id)
        item['x']=number(item['x'],0,100)
        item['y']=number(item['y'],0,100)
        item['rotation']=number(item['rotation'],-180,180)
        item['size']=number(item['size'],3,48) if kind in ('clipart','asset') else number(item['size'],1.8,12)
        if not isinstance(item['color'],str) or not HEX_COLOR.fullmatch(item['color']):
            raise HTTPException(422, 'Invalid canvas item color')
        item['color']=item['color'].lower()
        if kind=='clipart':
            if not isinstance(item['art'],str) or item['art'] not in CLIPART:
                raise HTTPException(422, 'Unknown clipart design')
        elif kind=='asset':
            if not isinstance(item['asset_id'],str) or not re.fullmatch(r'[0-9a-f-]{36}',item['asset_id']):
                raise HTTPException(422,'Invalid uploaded clipart ID')
        else:
            if not isinstance(item['text'],str) or not 1<=len(item['text'].strip())<=240:
                raise HTTPException(422, 'Custom text must contain 1–240 characters')
            if not isinstance(item['font'],str) or not (item['font'] in FONTS or FONT_REF.fullmatch(item['font'])):
                raise HTTPException(422, 'Invalid text font')
            item['text']=item['text'].strip()
    return design


def get_design(con, event_id):
    row = con.execute('SELECT design_json FROM invitation_designs WHERE event_id=?', (event_id,)).fetchone()
    if not row:
        return default_design()
    design = {**default_design(), **json.loads(row['design_json'])}
    design['font'] = offline_font(design['font'])
    design['canvas_font'] = offline_font(design['canvas_font'])
    design['canvas_field_fonts'] = {k:offline_font(v) for k,v in design.get('canvas_field_fonts',{}).items()}
    for item in design.get('canvas_items',[]):
        if isinstance(item,dict) and item.get('kind') == 'text':
            item['font'] = offline_font(item.get('font'))
    return design


def install_invitation_designer(app, db, access, check_event):
    ensure_schema(db)
    install_design_asset_library(app, db, access, check_event)

    @app.get('/api/w/{wid}/events/{eid}/invitation-design')
    def invitation_design_get(request: Request, wid: str, eid: str):
        with db() as con:
            access(request, con, wid)
            check_event(con, wid, eid)
            return get_design(con, eid)

    @app.put('/api/w/{wid}/events/{eid}/invitation-design')
    def invitation_design_put(request: Request, wid: str, eid: str, payload: dict[str, Any]):
        design = normalize_design(payload)
        with db() as con:
            access(request, con, wid, True)
            check_event(con, wid, eid)
            getattr(app.state, 'validate_design_asset_references', lambda *_: None)(con,wid,design)
            if design['mode']=='upload' and not con.execute("SELECT 1 FROM invitation_media WHERE event_id=? AND kind='invite'",(eid,)).fetchone():
                raise HTTPException(422, 'Upload an invitation file before selecting upload mode')
            con.execute('''INSERT INTO invitation_designs(event_id,workspace_id,design_json,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                design_json=excluded.design_json,updated_at=excluded.updated_at''',
                (eid, wid, json.dumps(design, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return design

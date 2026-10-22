"""RSVP attendance settings, party details, reusable templates and Excel exports.

No new runtime dependencies: the XLSX writer uses the Python standard library.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

from fastapi import HTTPException, Request, Response

DEFAULT_ATTENDANCE = {
    'question': 'Will you attend?',
    'confirmed': 'Joyfully accepts',
    'maybe': 'Maybe',
    'declined': 'Regretfully declines',
}
DEFAULT_PARTY = {
    'enabled': True,
    'allow_plus_one': True,
    'ask_adults_kids': True,
    'names_optional': True,
    'meals_optional': True,
    'meal_options': ['Vegetarian', 'Non-vegetarian', 'Vegan', 'Kids meal'],
}
TEMPLATE_PRESETS = {
    'wedding': ('Wedding celebration', 'We would love to celebrate with you.', ['Vegetarian', 'Non-vegetarian', 'Vegan', 'Kids meal']),
    'birthday': ('Birthday party', 'Join us for a birthday celebration!', ['Vegetarian', 'Non-vegetarian', 'Kids meal']),
    'corporate': ('Corporate event', 'Please confirm your attendance for our event.', ['Vegetarian', 'Regular', 'Vegan']),
    'gala': ('Gala dinner', 'We look forward to celebrating with you.', ['Vegetarian', 'Meat', 'Fish', 'Vegan']),
    'baby_shower': ('Baby shower', 'Join us to celebrate the little one!', ['Vegetarian', 'Non-vegetarian', 'Kids meal']),
    'graduation': ('Graduation party', 'Celebrate this milestone with us.', ['Vegetarian', 'Regular', 'Kids meal']),
    'holiday': ('Holiday gathering', 'We hope to celebrate the holidays with you.', ['Vegetarian', 'Regular', 'Kids meal']),
    'social': ('Family gathering', 'We would love to see you there.', ['Vegetarian', 'Non-vegetarian', 'Kids meal']),
}

def default_settings():
    return {'attendance': dict(DEFAULT_ATTENDANCE), 'party': {**DEFAULT_PARTY, 'meal_options': list(DEFAULT_PARTY['meal_options'])}}

def clean_settings(payload):
    cleaned = {}
    if 'attendance' in payload:
        raw = payload['attendance']
        if not isinstance(raw, dict) or set(raw) != set(DEFAULT_ATTENDANCE):
            raise HTTPException(422, 'Invalid attendance question settings')
        for key, value in raw.items():
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 140:
                raise HTTPException(422, 'Attendance wording must contain 1–140 characters')
        cleaned['attendance'] = {key: raw[key].strip() for key in DEFAULT_ATTENDANCE}
        if len({cleaned['attendance'][x].casefold() for x in ('confirmed','maybe','declined')}) != 3:
            raise HTTPException(422, 'Attendance options must be distinct')
    if 'party' in payload:
        raw = payload['party']
        if not isinstance(raw, dict) or set(raw) != set(DEFAULT_PARTY):
            raise HTTPException(422, 'Invalid guest details settings')
        for key in ('enabled','allow_plus_one','ask_adults_kids','names_optional','meals_optional'):
            if type(raw[key]) is not bool:
                raise HTTPException(422, 'Guest details switches must be true or false')
        opts = raw['meal_options']
        if not isinstance(opts, list) or not 1 <= len(opts) <= 20 or any(
            not isinstance(x,str) or not 1 <= len(x.strip()) <= 100 for x in opts
        ):
            raise HTTPException(422, 'Specify 1–20 meal choices')
        opts = [x.strip() for x in opts]
        if len({x.casefold() for x in opts}) != len(opts):
            raise HTTPException(422, 'Meal choices cannot repeat')
        cleaned['party'] = {**raw, 'meal_options': opts}
    return cleaned

def clean_party(form, payload, status, seats):
    config = form.get('party')
    if not config or not config.get('enabled') or status != 'Confirmed':
        if payload not in (None, {}):
            raise HTTPException(422, 'Guest details are only accepted for confirmed RSVPs with guest details enabled')
        return {}
    if not isinstance(payload, dict) or set(payload) - {'adults', 'kids', 'plus_one', 'guests'}:
        raise HTTPException(422, 'Invalid attendee details')
    adults, kids = payload.get('adults'), payload.get('kids')
    if type(adults) is not int or type(kids) is not int or adults < 0 or kids < 0:
        raise HTTPException(422, 'Enter valid adult and child counts')
    total = adults + kids
    plus_one = payload.get('plus_one', False)
    if type(plus_one) is not bool or (plus_one and not config['allow_plus_one']):
        raise HTTPException(422, 'Additional guest not permitted')
    if total < 1 or total > min(10000, seats + int(plus_one)):
        raise HTTPException(422, 'The number attending exceeds the seats available')
    if not config['ask_adults_kids'] and kids:
        raise HTTPException(422, 'Child counts are disabled for this form')
    guests = payload.get('guests', [])
    if not isinstance(guests, list) or len(guests) > min(total, 100):
        raise HTTPException(422, 'Too many individual guest details')
    cleaned_guests = []
    for item in guests:
        if not isinstance(item, dict) or set(item) - {'name', 'meal'}:
            raise HTTPException(422, 'Invalid individual guest details')
        name, meal = item.get('name',''), item.get('meal','')
        if not isinstance(name, str) or len(name) > 160 or not isinstance(meal, str):
            raise HTTPException(422, 'Invalid guest name or meal preference')
        name, meal = name.strip(), meal.strip()
        if meal and (not config['meals_optional'] or meal not in config['meal_options']):
            raise HTTPException(422, 'Invalid meal preference')
        if name and not config['names_optional']:
            raise HTTPException(422, 'Guest names are disabled')
        cleaned_guests.append({'name': name, 'meal': meal})
    return {'adults': adults, 'kids': kids, 'total': total, 'plus_one': plus_one, 'guests': cleaned_guests}

def migrate(con):
    columns = {row['name'] for row in con.execute('PRAGMA table_info(rsvp_forms)')}
    if 'settings_json' not in columns:
        con.execute("ALTER TABLE rsvp_forms ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
    columns = {row['name'] for row in con.execute('PRAGMA table_info(rsvp_responses)')}
    if 'party_json' not in columns:
        con.execute("ALTER TABLE rsvp_responses ADD COLUMN party_json TEXT NOT NULL DEFAULT '{}'")
    con.execute('''CREATE TABLE IF NOT EXISTS rsvp_templates (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        name TEXT NOT NULL, form_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_rsvp_templates_workspace ON rsvp_templates(workspace_id)')

# Each built-in form provides useful starter questions, all editable in the builder.
TEMPLATE_QUESTIONS = {
    'wedding': [('dietary', 'Any allergies or dietary restrictions?', 'paragraph'),
                ('song', 'A song that gets you dancing?', 'short_text')],
    'birthday': [('dietary', 'Any food allergies?', 'short_text'),
                 ('note', 'A birthday message for the guest of honour?', 'paragraph')],
    'corporate': [('organization', 'Company or organization', 'short_text'),
                  ('accessibility', 'Any accessibility requirements?', 'paragraph')],
    'gala': [('dietary', 'Dietary restrictions or allergies?', 'short_text'),
             ('seating', 'Any seating requests?', 'paragraph')],
    'baby_shower': [('dietary', 'Any dietary restrictions?', 'short_text'),
                   ('message', 'A message for the parents?', 'paragraph')],
    'graduation': [('dietary', 'Any dietary restrictions?', 'short_text'),
                   ('message', 'A note for the graduate?', 'paragraph')],
    'holiday': [('dietary', 'Any food allergies?', 'short_text'),
                ('activity', 'What activity would you enjoy?', 'short_text')],
    'social': [('dietary', 'Any food allergies?', 'short_text'),
               ('note', 'Anything we should know?', 'paragraph')],
}

def preset_questions(kind):
    return [{'id':kind+'_'+qid, 'label':label, 'type':answer_type,
             'required':False, 'help_text':'', 'options':[]}
            for qid,label,answer_type in TEMPLATE_QUESTIONS.get(kind,[])]
def built_in_templates():
    templates = []
    for ident, (name, intro, meals) in TEMPLATE_PRESETS.items():
        settings = default_settings()
        settings['party']['meal_options'] = meals
        if ident == 'corporate':
            settings['party']['allow_plus_one'] = False
        templates.append({'id': 'builtin-'+ident, 'name': name, 'built_in': True,
                          'form': {'title': name+' RSVP', 'intro': intro, 'deadline': '',
                                   'published': True, 'questions': preset_questions(ident), **settings}})
    return templates

def workbook(rows, columns):
    """Minimal valid Office Open XML workbook with all user strings encoded as text."""
    def col_name(n):
        s = ''
        while n:
            n, digit = divmod(n-1, 26)
            s = chr(65+digit)+s
        return s
    def cell(value, row, col):
        ref = col_name(col)+str(row)
        if isinstance(value, (int,float)) and not isinstance(value,bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape(str(value if value is not None else ''))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
    content = ['<row r="1">'+''.join(cell(k,1,c+1) for c,k in enumerate(columns))+'</row>']
    for rindex, row in enumerate(rows,2):
        content.append(f'<row r="{rindex}">'+''.join(cell(row.get(k,''),rindex,c+1) for c,k in enumerate(columns))+'</row>')
    last = col_name(len(columns))
    widths = ''.join(f'<col min="{n}" max="{n}" width="{min(38,max(15,len(h)+4))}" customWidth="1"/>' for n,h in enumerate(columns,1))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:{last}{max(1,len(rows)+1)}"/>'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{widths}</cols><sheetData>{"".join(content)}</sheetData>'
             f'<autoFilter ref="A1:{last}{max(1,len(rows)+1)}"/></worksheet>')
    buff = io.BytesIO()
    with zipfile.ZipFile(buff,'w',zipfile.ZIP_DEFLATED) as f:
        f.writestr('[Content_Types].xml','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        f.writestr('_rels/.rels','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        f.writestr('xl/workbook.xml','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="RSVP Responses" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        f.writestr('xl/_rels/workbook.xml.rels','''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        f.writestr('xl/worksheets/sheet1.xml',sheet)
    return buff.getvalue()

def install_extras(app, db, access, check_event, get_form, normalise_form):
    @app.get('/api/w/{wid}/rsvp-templates')
    def list_templates(request: Request, wid: str):
        with db() as con:
            access(request,con,wid)
            custom = con.execute('SELECT id,name,form_json FROM rsvp_templates WHERE workspace_id=? ORDER BY created_at DESC',(wid,)).fetchall()
            return built_in_templates() + [{'id':r['id'],'name':r['name'],'built_in':False,'form':json.loads(r['form_json'])} for r in custom]

    @app.post('/api/w/{wid}/rsvp-templates', status_code=201)
    def add_template(request: Request, wid: str, payload: dict):
        if not isinstance(payload,dict) or set(payload) != {'name','form'}:
            raise HTTPException(422, 'Template requires a name and form')
        name = payload['name']
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 100:
            raise HTTPException(422, 'Template name must be 1–100 characters')
        config = normalise_form(payload['form'])
        with db() as con:
            access(request, con, wid, True)
            if con.execute('SELECT COUNT(*) FROM rsvp_templates WHERE workspace_id=?',(wid,)).fetchone()[0] >= 100:
                raise HTTPException(422, 'No more than 100 saved templates per workspace')
            ident=uuid.uuid4().hex
            con.execute('INSERT INTO rsvp_templates VALUES (?,?,?,?,?)',(ident,wid,name.strip(),
                json.dumps(config,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return {'id':ident,'name':name.strip(),'built_in':False,'form':config}

    @app.delete('/api/w/{wid}/rsvp-templates/{template_id}', status_code=204)
    def delete_template(request: Request, wid: str, template_id: str):
        with db() as con:
            access(request,con,wid,True)
            if not con.execute('SELECT id FROM rsvp_templates WHERE id=? AND workspace_id=?',(template_id,wid)).fetchone():
                raise HTTPException(404, 'Template not found')
            con.execute('DELETE FROM rsvp_templates WHERE id=? AND workspace_id=?',(template_id,wid))
        return Response(status_code=204)

    @app.get('/api/w/{wid}/events/{eid}/rsvp-responses.xlsx')
    def responses_excel(request: Request, wid: str, eid: str):
        with db() as con:
            access(request,con,wid)
            ev = check_event(con,wid,eid)
            form = get_form(con,eid,ev['name'])
            question_labels = [(q['id'],q['label']) for q in form['questions']]
            rows = con.execute('''SELECT g.name,g.email,g.phone,g.rsvp,g.party_size,r.answers_json,r.party_json,r.submitted_at
                FROM rsvp_responses r JOIN guests g ON g.id=r.guest_id
                WHERE r.event_id=? AND g.workspace_id=? ORDER BY r.submitted_at DESC''',(eid,wid)).fetchall()
            # Each attendee gets dedicated Name/Meal columns. Blank values remain blank.
            # The export adapts to the largest party in the event, up to the form's
            # existing 100-person attendee-details limit.
            parsed=[(item,json.loads(item['party_json'] or '{}'),
                     json.loads(item['answers_json'] or '{}')) for item in rows]
            max_guests=max((len(party.get('guests',[])) for _,party,_ in parsed),default=0)
            headers=['Guest name','Email','Phone','RSVP','Reserved seats','Adults','Kids','Attending','Plus one']
            for index in range(1,max_guests+1):
                headers.extend((f'Guest {index} Name',f'Guest {index} Meal'))
            headers += ['Submitted at']
            headers += [label+' ('+qid+')' for qid,label in question_labels]
            output=[]
            for item,party,answers in parsed:
                row={'Guest name':item['name'],'Email':item['email'],'Phone':item['phone'],'RSVP':item['rsvp'],
                     'Reserved seats':item['party_size'],'Adults':party.get('adults',''),
                     'Kids':party.get('kids',''),'Attending':party.get('total',''),
                     'Plus one':'Yes' if party.get('plus_one') else 'No',
                     'Submitted at':item['submitted_at']}
                for index,guest in enumerate(party.get('guests',[]),1):
                    row[f'Guest {index} Name']=guest.get('name','')
                    row[f'Guest {index} Meal']=guest.get('meal','')
                for qid,label in question_labels:
                    value=answers.get(qid,'')
                    row[label+' ('+qid+')']=', '.join(map(str,value)) if isinstance(value,list) else ('Yes' if value is True else 'No' if value is False else value)
                output.append(row)
        data=workbook(output,headers)
        return Response(content=data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':'attachment; filename="eventos-rsvp-responses.xlsx"',
                                 'Cache-Control':'no-store'})

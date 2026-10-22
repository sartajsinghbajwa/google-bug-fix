"""Per-event configurable RSVP forms and guest answers for EventOS.

Invitation links are guest bearer tokens. Organizer routes require workspace access.
Historic answers remain stored when a question is removed from a form.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from rsvp_features import clean_settings, clean_party, migrate, install_extras

TYPES = {'short_text', 'paragraph', 'single_choice', 'multiple_choice', 'number', 'date', 'yes_no'}
CHOICES = {'single_choice', 'multiple_choice'}
QUESTION_ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
MAX_QUESTIONS = 30


def ensure_schema(db):
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS rsvp_forms (
            event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            title TEXT NOT NULL, intro TEXT NOT NULL DEFAULT '',
            deadline TEXT NOT NULL DEFAULT '', published INTEGER NOT NULL DEFAULT 1,
            questions_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rsvp_responses (
            guest_id TEXT PRIMARY KEY REFERENCES guests(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            answers_json TEXT NOT NULL DEFAULT '{}', submitted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rsvp_responses_event ON rsvp_responses(event_id);
        ''')
        migrate(con)


def get_form(con, event_id, event_name):
    row = con.execute('SELECT * FROM rsvp_forms WHERE event_id=?', (event_id,)).fetchone()
    if row is None:
        return {'title': f'{event_name} RSVP', 'intro': 'We would love to celebrate with you. Please reply below.',
                'deadline': '', 'published': True, 'questions': []}
    return {'title': row['title'], 'intro': row['intro'], 'deadline': row['deadline'],
            'published': bool(row['published']), 'questions': json.loads(row['questions_json']),
            **json.loads(row['settings_json'] or '{}')}


def normalise_form(payload: Any):
    if not isinstance(payload, dict) or set(payload) - {'title', 'intro', 'deadline', 'published', 'questions', 'attendance', 'party'}:
        raise HTTPException(422, 'Invalid form settings')
    title = payload.get('title', '')
    intro = payload.get('intro', '')
    deadline = payload.get('deadline', '')
    published = payload.get('published', True)
    questions = payload.get('questions', [])
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise HTTPException(422, 'Form title must contain 1–160 characters')
    if not isinstance(intro, str) or len(intro) > 1500:
        raise HTTPException(422, 'Welcome message must contain at most 1,500 characters')
    if not isinstance(deadline, str) or (deadline and (len(deadline) != 10 or not _valid_date(deadline))):
        raise HTTPException(422, 'Deadline must be a valid YYYY-MM-DD date')
    if type(published) is not bool:
        raise HTTPException(422, 'Published must be true or false')
    if not isinstance(questions, list) or len(questions) > MAX_QUESTIONS:
        raise HTTPException(422, f'Use no more than {MAX_QUESTIONS} questions')
    cleaned, seen = [], set()
    for i, q in enumerate(questions):
        if not isinstance(q, dict) or set(q) - {'id', 'label', 'type', 'required', 'help_text', 'options', 'show_when'}:
            raise HTTPException(422, f'Invalid question #{i+1}')
        ident = q.get('id') or uuid.uuid4().hex
        label = q.get('label', '')
        kind = q.get('type', '')
        required = q.get('required', False)
        help_text = q.get('help_text', '')
        options = q.get('options', [])
        show_when = q.get('show_when', 'always')
        if not isinstance(show_when, str) or show_when not in ('always', 'Confirmed', 'Maybe', 'Declined'):
            raise HTTPException(422, f'Invalid conditional visibility for question #{i+1}')
        if not isinstance(ident, str) or not QUESTION_ID.fullmatch(ident) or ident in seen:
            raise HTTPException(422, 'Question IDs must be unique and valid')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 180:
            raise HTTPException(422, f'Question #{i+1} needs a label (up to 180 characters)')
        if not isinstance(kind, str) or kind not in TYPES or type(required) is not bool:
            raise HTTPException(422, f'Invalid type or required flag for question #{i+1}')
        if not isinstance(help_text, str) or len(help_text) > 350:
            raise HTTPException(422, f'Help text is too long for question #{i+1}')
        if not isinstance(options, list):
            raise HTTPException(422, f'Options must be a list for question #{i+1}')
        if kind in CHOICES:
            if not 2 <= len(options) <= 15 or any(not isinstance(o, str) or not 1 <= len(o.strip()) <= 100 for o in options):
                raise HTTPException(422, f'Question #{i+1} needs 2–15 valid choices')
            options = [o.strip() for o in options]
            if len(set(options)) != len(options):
                raise HTTPException(422, f'Question #{i+1} has duplicate choices')
        else:
            options = []
        clean_question = {'id': ident, 'label': label.strip(), 'type': kind, 'required': required,
                          'help_text': help_text.strip(), 'options': options}
        if 'show_when' in q:
            clean_question['show_when'] = show_when
        cleaned.append(clean_question)
        seen.add(ident)
    settings = clean_settings(payload)
    return {'title': title.strip(), 'intro': intro.strip(), 'deadline': deadline,
            'published': published, 'questions': cleaned, **settings}


def _valid_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_answers(form, answers, status='Confirmed'):
    if not isinstance(answers, dict) or len(json.dumps(answers, ensure_ascii=False)) > 16000:
        raise HTTPException(422, 'Invalid or oversized RSVP answers')
    questions = {q['id']: q for q in form['questions']}
    if set(answers) - set(questions):
        raise HTTPException(422, 'Unknown RSVP question')
    result = {}
    for key, q in questions.items():
        if q.get('show_when', 'always') not in ('always', status):
            continue
        val = answers.get(key)
        if val is None or val == '' or val == []:
            if q['required']:
                raise HTTPException(422, f'Please answer: {q["label"]}')
            continue
        kind = q['type']
        if kind in ('short_text', 'paragraph'):
            limit = 350 if kind == 'short_text' else 3000
            if not isinstance(val, str) or len(val) > limit or not val.strip():
                raise HTTPException(422, f'Invalid answer for {q["label"]}')
            val = val.strip()
        elif kind == 'single_choice':
            if not isinstance(val, str) or val not in q['options']:
                raise HTTPException(422, f'Invalid choice for {q["label"]}')
        elif kind == 'multiple_choice':
            if not isinstance(val, list) or len(val) > len(q['options']) or len(val) != len(set(map(str, val))) or any(not isinstance(x, str) or x not in q['options'] for x in val):
                raise HTTPException(422, f'Invalid choices for {q["label"]}')
        elif kind == 'number':
            if type(val) not in (int, float) or not math.isfinite(val) or abs(val) > 1e9:
                raise HTTPException(422, f'Invalid number for {q["label"]}')
        elif kind == 'date':
            if not isinstance(val, str) or not _valid_date(val):
                raise HTTPException(422, f'Invalid date for {q["label"]}')
        elif kind == 'yes_no':
            if type(val) is not bool:
                raise HTTPException(422, f'Invalid Yes/No answer for {q["label"]}')
        result[key] = val
    return result


def public_form(con, event_id, event_name, guest_id):
    form = get_form(con, event_id, event_name)
    response = con.execute('SELECT answers_json,party_json,submitted_at FROM rsvp_responses WHERE guest_id=?', (guest_id,)).fetchone()
    return {'form': form, 'answers': json.loads(response['answers_json']) if response else {},
            'party': json.loads(response['party_json']) if response else {},
            'submitted_at': response['submitted_at'] if response else None}


def save_guest_response(con, guest_id, event_id, event_name, answers, party=None, status='Confirmed', seats=1):
    form = get_form(con, event_id, event_name)
    if not form['published']:
        raise HTTPException(409, 'This RSVP form is not accepting responses')
    if form['deadline'] and date.today().isoformat() > form['deadline']:
        raise HTTPException(409, 'The RSVP deadline has passed')
    clean = validate_answers(form, answers, status)
    details = clean_party(form, party, status, seats)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    con.execute('''INSERT INTO rsvp_responses (guest_id,event_id,answers_json,party_json,submitted_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(guest_id) DO UPDATE SET answers_json=excluded.answers_json,
                   party_json=excluded.party_json, submitted_at=excluded.submitted_at''',
                (guest_id, event_id, json.dumps(clean, ensure_ascii=False), json.dumps(details, ensure_ascii=False), now))
    return clean


def install_rsvp_forms(app, db, access, check_event):
    ensure_schema(db)

    @app.get('/api/w/{wid}/events/{eid}/rsvp-form')
    def form_get(request: Request, wid: str, eid: str):
        with db() as con:
            access(request, con, wid)
            event = check_event(con, wid, eid)
            return get_form(con, eid, event['name'])

    @app.put('/api/w/{wid}/events/{eid}/rsvp-form')
    def form_put(request: Request, wid: str, eid: str, payload: dict[str, Any]):
        config = normalise_form(payload)
        with db() as con:
            access(request, con, wid, True)
            check_event(con, wid, eid)
            now = datetime.now(timezone.utc).isoformat(timespec='seconds')
            con.execute('''INSERT INTO rsvp_forms
                (event_id,workspace_id,title,intro,deadline,published,questions_json,updated_at,settings_json)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,intro=excluded.intro,deadline=excluded.deadline,
                published=excluded.published,questions_json=excluded.questions_json,
                settings_json=excluded.settings_json,updated_at=excluded.updated_at''',
                (eid, wid, config['title'], config['intro'], config['deadline'], int(config['published']),
                 json.dumps(config['questions'], ensure_ascii=False), now, json.dumps({k:config[k] for k in ('attendance','party') if k in config}, ensure_ascii=False)))
        return config

    @app.get('/api/w/{wid}/events/{eid}/rsvp-responses')
    def responses_get(request: Request, wid: str, eid: str):
        with db() as con:
            access(request, con, wid)
            check_event(con, wid, eid)
            rows = con.execute('''SELECT g.id AS guest_id,g.name AS guest_name,g.rsvp,r.answers_json,r.party_json,r.submitted_at
                                  FROM rsvp_responses r JOIN guests g ON g.id=r.guest_id
                                  WHERE r.event_id=? AND g.workspace_id=? ORDER BY r.submitted_at DESC''',
                               (eid, wid)).fetchall()
            return [{'guest_id': r['guest_id'], 'guest_name': r['guest_name'], 'rsvp': r['rsvp'],
                     'answers': json.loads(r['answers_json']), 'party': json.loads(r['party_json']), 'submitted_at': r['submitted_at']} for r in rows]

    install_extras(app, db, access, check_event, get_form, normalise_form)

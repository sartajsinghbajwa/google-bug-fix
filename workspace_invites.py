"""Invite-code membership for existing EventOS organizations and personal co-hosts.

Codes are high entropy and hashed at rest. Invites specify *workspace role* and
are separate from subscription/plan codes. Redemption is transactional and
checks workspace status and member quota.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from saas_access import enforce, limits, CODE_PATTERN, valid_expiry


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def migrate(db):
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS team_access_codes(
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              code_hash TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL CHECK(role IN ('editor','viewer')),
              max_uses INTEGER NOT NULL CHECK(max_uses BETWEEN 1 AND 100),
              uses INTEGER NOT NULL DEFAULT 0,
              expires_at TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_team_access_codes_workspace ON team_access_codes(workspace_id);
        """)


def has_team_code(con,code):
    if not isinstance(code,str) or not CODE_PATTERN.fullmatch(code):
        return False
    return con.execute('SELECT 1 FROM team_access_codes WHERE code_hash=?',(hash_code(code),)).fetchone() is not None


def redeem(con,code,user_id):
    if not isinstance(code,str) or not CODE_PATTERN.fullmatch(code):
        raise HTTPException(422,'Invalid team access code')
    row=con.execute('SELECT * FROM team_access_codes WHERE code_hash=?',(hash_code(code),)).fetchone()
    if not row or not row['active'] or row['uses']>=row['max_uses'] or (row['expires_at'] and row['expires_at']<=now()):
        raise HTTPException(403,'Team access code is invalid, expired, or fully used')
    wid=row['workspace_id']
    limits(con,wid)
    if con.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(wid,user_id)).fetchone():
        raise HTTPException(409,'Already a member of this workspace')
    enforce(con,wid,'members')
    changed=con.execute("UPDATE team_access_codes SET uses=uses+1 WHERE id=? AND active=1 AND uses<max_uses",(row['id'],)).rowcount
    if changed!=1:
        raise HTTPException(409,'Team code has no remaining uses')
    con.execute('INSERT INTO memberships(workspace_id,user_id,role) VALUES (?,?,?)',(wid,user_id,row['role']))
    return {'workspace_id':wid,'role':row['role']}


def install_invites(app,db,get_user,access):
    migrate(db)

    @app.get('/api/w/{wid}/team/access-codes')
    def list_codes(request:Request,wid:str):
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':
                raise HTTPException(403,'Workspace owner required')
            return [dict(x) for x in con.execute('''SELECT id,role,max_uses,uses,expires_at,active,created_at
                FROM team_access_codes WHERE workspace_id=? ORDER BY created_at DESC LIMIT 200''',(wid,))]

    @app.post('/api/w/{wid}/team/access-codes')
    def create_code(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)-{'role','max_uses','expires_at'} or 'role' not in payload:
            raise HTTPException(422,'Invalid invite settings')
        role=payload.get('role')
        max_uses=payload.get('max_uses',1)
        expires=payload.get('expires_at','')
        if role not in ('editor','viewer') or type(max_uses) is not int or not 1<=max_uses<=100:
            raise HTTPException(422,'Choose editor or viewer and 1–100 uses')
        if not isinstance(expires,str) or (expires and not valid_expiry(expires)):
            raise HTTPException(422,'Invalid code expiry')
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':
                raise HTTPException(403,'Workspace owner required')
            if con.execute('SELECT COUNT(*) FROM team_access_codes WHERE workspace_id=?',(wid,)).fetchone()[0]>=300:
                raise HTTPException(409,'Too many team access codes')
            user=get_user(request,con)
            raw=secrets.token_urlsafe(24)
            con.execute('''INSERT INTO team_access_codes VALUES (?,?,?,?,?,?,?,?,?,?)''',(
                secrets.token_hex(16),wid,hash_code(raw),role,max_uses,0,expires,1,user['id'],now()))
            return {'access_code':raw,'role':role,'max_uses':max_uses,'workspace_id':wid}

    @app.patch('/api/w/{wid}/team/access-codes/{cid}')
    def toggle_code(request:Request,wid:str,cid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'active'} or type(payload['active']) is not bool:
            raise HTTPException(422,'Invalid code status')
        with db() as con:
            member=access(request,con,wid)
            if member['role']!='owner':
                raise HTTPException(403,'Workspace owner required')
            if not con.execute('UPDATE team_access_codes SET active=? WHERE id=? AND workspace_id=?',(
                int(payload['active']),cid,wid)).rowcount:
                raise HTTPException(404,'Team code not found')
        return {'ok':True}

    @app.post('/api/access/join-workspace')
    def join_existing(request:Request,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'access_code'}:
            raise HTTPException(422,'Enter a team access code')
        with db() as con:
            user=get_user(request,con)
            return redeem(con,payload['access_code'],user['id'])

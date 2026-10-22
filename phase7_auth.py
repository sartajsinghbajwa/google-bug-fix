"""Optional verified-email onboarding, self-service password recovery and abuse limits.

No account is verified by possessing its ID or email alone. Opaque one-time tokens
are stored as SHA-256 digests, expire quickly, and are invalidated on use.
All outgoing messages use the configured SMTP service; feature stays opt-in.
"""
from __future__ import annotations
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from fastapi import HTTPException, Request
from event_operations import mail_enabled, send_email

def now():
    return datetime.now(timezone.utc)
def iso(dt):
    return dt.isoformat(timespec='seconds')
def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
def verify_required():
    return os.getenv('EVENTOS_REQUIRE_VERIFIED_EMAIL','0')=='1'
def origin():
    base=os.getenv('EVENTOS_PUBLIC_BASE_URL','').strip().rstrip('/')
    if not base.startswith('https://'):
        raise HTTPException(503,'A secure public application URL is not configured')
    return base
def migrate(db):
    with db() as con:
        cols={x['name'] for x in con.execute('PRAGMA table_info(users)')}
        if 'email_verified' not in cols:
            # Existing users are grandfathered; only opt-in new accounts require verification.
            con.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1')
        con.executescript("""
        CREATE TABLE IF NOT EXISTS auth_one_time_tokens (
          token_hash TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
          expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_one_time_tokens(user_id,purpose);
        CREATE TABLE IF NOT EXISTS auth_request_limits (
          rate_key TEXT PRIMARY KEY, window_started TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0);
        """)

def issuance_limit(con,purpose,email,limit=3):
    # This caps each target account and does not reveal whether it exists.
    key=hashlib.sha256((purpose+'|'+email.lower()).encode()).hexdigest()
    cutoff=iso(now()-timedelta(hours=1))
    row=con.execute('SELECT window_started,count FROM auth_request_limits WHERE rate_key=?',(key,)).fetchone()
    if row and row['window_started']>cutoff:
        if row['count']>=limit:return False
        con.execute('UPDATE auth_request_limits SET count=count+1 WHERE rate_key=?',(key,))
    else:
        con.execute('INSERT INTO auth_request_limits(rate_key,window_started,count) VALUES (?,?,1) ON CONFLICT(rate_key) DO UPDATE SET window_started=excluded.window_started,count=1',(key,iso(now())))
    return True

def token_for(con,user_id,purpose):
    raw=secrets.token_urlsafe(32)
    expire=now()+timedelta(hours=24 if purpose=='verify' else 1)
    con.execute('DELETE FROM auth_one_time_tokens WHERE user_id=? AND purpose=?',(user_id,purpose))
    con.execute('INSERT INTO auth_one_time_tokens VALUES (?,?,?,?,?)',
                (hash_token(raw),user_id,purpose,iso(expire),iso(now())))
    return raw

def deliver_verification(email,token):
    send_email(email,'Verify your EventOS email address',
        'Welcome to EventOS.\n\nConfirm your email address using this one-time link:\n'
        +origin()+'/app#/verify-email?token='+quote(token,safe='')
        +'\n\nThis link expires after 24 hours. If you did not register, ignore this email.')

def send_password_reset(email,token):
    send_email(email,'Reset your EventOS password',
        'Use this private, one-time link to change your EventOS password:\n'
        +origin()+'/app#/reset-password?token='+quote(token,safe='')
        +'\n\nThis link expires after 1 hour. If you did not request it, ignore this email.')

def use_token(con,raw,purpose):
    if not isinstance(raw,str) or len(raw)>160:
        raise HTTPException(422,'Invalid or expired verification link')
    row=con.execute('SELECT * FROM auth_one_time_tokens WHERE token_hash=? AND purpose=? AND expires_at>?',
        (hash_token(raw),purpose,iso(now()))).fetchone()
    if not row:raise HTTPException(422,'Invalid or expired verification link')
    if con.execute('DELETE FROM auth_one_time_tokens WHERE token_hash=?',(row['token_hash'],)).rowcount!=1:
        raise HTTPException(422,'Verification link has already been used')
    return row['user_id']

def install_auth(app,db,hash_password):
    migrate(db)
    @app.post('/api/auth/verify-email')
    def verify(payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'token'}:
            raise HTTPException(422,'Invalid verification request')
        with db() as con:
            uid=use_token(con,payload['token'],'verify')
            con.execute('UPDATE users SET email_verified=1 WHERE id=?',(uid,))
            return {'ok':True}

    @app.post('/api/auth/verify-resend')
    def resend(payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'email'}:
            raise HTTPException(422,'Enter your email')
        email=payload['email']
        if not isinstance(email,str) or len(email)>255:
            raise HTTPException(422,'Enter a valid email')
        if not mail_enabled():
            raise HTTPException(503,'Email verification service is not configured')
        with db() as con:
            permitted=issuance_limit(con,'verify',email)
            row=con.execute('SELECT id,email_verified,is_active FROM users WHERE email=?',(email.strip().lower(),)).fetchone()
            token=token_for(con,row['id'],'verify') if permitted and row and not row['email_verified'] and row['is_active'] else None
        if token:
            try:deliver_verification(email,token)
            except Exception:raise HTTPException(503,'Email service is unavailable; try again later')
        return {'ok':True,'message':'If the account requires verification, a new link will be sent.'}

    @app.post('/api/auth/password/forgot')
    def forgot(payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'email'}:
            raise HTTPException(422,'Enter your email')
        email=payload['email']
        if not isinstance(email,str) or len(email)>255:
            raise HTTPException(422,'Enter a valid email')
        if not mail_enabled():
            raise HTTPException(503,'Password recovery email is not configured')
        with db() as con:
            permitted=issuance_limit(con,'reset',email)
            row=con.execute('SELECT id,is_active FROM users WHERE email=?',(email.strip().lower(),)).fetchone()
            token=token_for(con,row['id'],'reset') if permitted and row and row['is_active'] else None
        if token:
            try:send_password_reset(email,token)
            except Exception:raise HTTPException(503,'Email service is unavailable; try again later')
        return {'ok':True,'message':'If an eligible account exists, a reset link will be sent.'}

    @app.post('/api/auth/password/reset')
    def reset(payload:dict):
        if not isinstance(payload,dict) or set(payload)!={'token','password'}:
            raise HTTPException(422,'Invalid password reset')
        password=payload['password']
        if not isinstance(password,str) or not 12<=len(password)<=256:
            raise HTTPException(422,'Choose a password of 12–256 characters')
        with db() as con:
            uid=use_token(con,payload['token'],'reset')
            user=con.execute('SELECT is_active FROM users WHERE id=?',(uid,)).fetchone()
            if not user or not user['is_active']:
                raise HTTPException(403,'Account is inactive')
            con.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(password),uid))
            con.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            con.execute("DELETE FROM auth_one_time_tokens WHERE user_id=? AND purpose='reset'",(uid,))
            return {'ok':True}

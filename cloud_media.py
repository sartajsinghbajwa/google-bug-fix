"""Optional private S3-compatible object storage for EventOS event images.

Configured only when EVENTOS_S3_BUCKET and EVENTOS_S3_REGION are present.
Existing SQLite images remain readable; newly uploaded gallery/gift photos
are held outside the database when S3 is enabled. No public bucket ACLs or
exposed presigned URLs are used: authenticated EventOS endpoints proxy media.
"""
from __future__ import annotations
import os
import secrets
from fastapi import HTTPException

def configured():
    return bool(os.getenv('EVENTOS_S3_BUCKET') and os.getenv('EVENTOS_S3_REGION'))

def client():
    if not configured():
        raise RuntimeError('Cloud media storage is not configured')
    import boto3
    endpoint=os.getenv('EVENTOS_S3_ENDPOINT_URL','').strip()
    if endpoint and not endpoint.startswith('https://'):
        raise RuntimeError('EventOS S3 endpoint must use HTTPS')
    return boto3.client('s3',region_name=os.environ['EVENTOS_S3_REGION'],
                        **({'endpoint_url':endpoint} if endpoint else {}))

def bucket():
    return os.environ['EVENTOS_S3_BUCKET']

def migrate(db):
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS cloud_media_objects(
          entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, variant TEXT NOT NULL,
          workspace_id TEXT NOT NULL, event_id TEXT NOT NULL,
          object_key TEXT NOT NULL, byte_size INTEGER NOT NULL,
          PRIMARY KEY(entity_kind,entity_id,variant))""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_media_objects_workspace ON cloud_media_objects(workspace_id,event_id)')

def upload_pair(con,kind,identifier,wid,eid,full,thumb):
    if not configured():
        return False
    # Cloud gallery and gift images count against a workspace's storage allowance.
    # This quota does not claim to meter other legacy SQLite media blobs.
    plan=con.execute("SELECT billing_plan,billing_status FROM workspaces WHERE id=?",(wid,)).fetchone()
    if plan is None:raise HTTPException(404,"Workspace not found")
    active=plan["billing_status"] in ("active","trialing","manual","legacy")
    allowance_mb={"free":50,"pro":500,"business":2048,"legacy":2048}.get(plan["billing_plan"] if active else "free",50)
    existing=con.execute("SELECT COALESCE(SUM(byte_size),0) FROM cloud_media_objects WHERE workspace_id=?",(wid,)).fetchone()[0]
    replacing=con.execute("""SELECT COALESCE(SUM(byte_size),0) FROM cloud_media_objects
        WHERE workspace_id=? AND entity_kind=? AND entity_id=?""",(wid,kind,identifier)).fetchone()[0]
    if existing-replacing+len(full)+len(thumb)>allowance_mb*1024*1024:
        raise HTTPException(409,"Cloud media allowance exceeded; upgrade or remove unused photos")
    # Never use original filenames or private customer names in an object key.
    new_keys=[f'eventos/{kind}/{secrets.token_hex(16)}/{v}.jpg' for v in ('full','thumb')]
    store=client()
    sizes=[len(full),len(thumb)]
    written=[]
    try:
        for key,body in zip(new_keys,(full,thumb)):
            store.put_object(Bucket=bucket(),Key=key,Body=body,ContentType='image/jpeg',
                             CacheControl='private, max-age=60',ServerSideEncryption='AES256')
            written.append(key)
    except Exception as exc:
        for key in written:
            try:store.delete_object(Bucket=bucket(),Key=key)
            except Exception:pass
        raise HTTPException(503,'Image storage is temporarily unavailable') from exc
    # Only after both uploads succeed do we associate the objects with tenant IDs.
    for variant,key,size in zip(('full','thumb'),new_keys,sizes):
        con.execute("""INSERT INTO cloud_media_objects(entity_kind,entity_id,variant,workspace_id,event_id,object_key,byte_size)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(entity_kind,entity_id,variant) DO UPDATE SET
            object_key=excluded.object_key,byte_size=excluded.byte_size""",
            (kind,identifier,variant,wid,eid,key,size))
    return True

def read(con,kind,identifier,variant,wid,eid):
    row=con.execute('''SELECT object_key FROM cloud_media_objects WHERE entity_kind=? AND entity_id=? AND variant=?
            AND workspace_id=? AND event_id=?''',(kind,identifier,variant,wid,eid)).fetchone()
    if not row:raise HTTPException(404,'Image not found')
    if not configured():
        raise HTTPException(503,'Cloud image storage is not configured')
    try:
        obj=client().get_object(Bucket=bucket(),Key=row['object_key'])
        return obj['Body'].read()
    except Exception as exc:
        raise HTTPException(503,'Image storage is temporarily unavailable') from exc

def delete(con,kind,identifier,wid,eid):
    rows=con.execute('''SELECT object_key FROM cloud_media_objects WHERE entity_kind=? AND entity_id=?
        AND workspace_id=? AND event_id=?''',(kind,identifier,wid,eid)).fetchall()
    con.execute('DELETE FROM cloud_media_objects WHERE entity_kind=? AND entity_id=? AND workspace_id=? AND event_id=?',
                (kind,identifier,wid,eid))
    if configured() and rows:
        store=client()
        for row in rows:
            try:store.delete_object(Bucket=bucket(),Key=row['object_key'])
            except Exception:pass
    return len(rows)

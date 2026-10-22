"""Opt-in, off-site encrypted backup + verifiable restore-drill utilities.

No backup is treated as verified by merely uploading it. Restores must use a
separate disposable SQLite file or a dedicated PostgreSQL restore database.
Never place credentials, snapshots or customer records in log output.
"""
from __future__ import annotations
import gzip
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MAX_SNAPSHOT = 2 * 1024 * 1024 * 1024
PREFIX = "eventos-db-backups/"
TRUTH = {"1","true","yes"}


def enabled():
    return bool(os.getenv("EVENTOS_BACKUP_BUCKET") and os.getenv("EVENTOS_S3_REGION") and
                os.getenv("EVENTOS_BACKUP_SCHEDULE_ENABLED")=="1")


def store():
    import boto3
    url=os.getenv("EVENTOS_S3_ENDPOINT_URL","")
    if url and not url.startswith("https://"):
        raise RuntimeError("Backup storage must use HTTPS")
    return boto3.client("s3",region_name=os.environ["EVENTOS_S3_REGION"],
                        **({"endpoint_url":url} if url else {}))


def destination():
    return os.environ["EVENTOS_BACKUP_BUCKET"]


def _pg_dump():
    if not shutil.which("pg_dump"):
        raise RuntimeError("pg_dump is unavailable; install matching PostgreSQL client tools")
    url = os.environ.get("EVENTOS_DATABASE_URL","")
    if not url.startswith(("postgres://","postgresql://")) or "sslmode=disable" in url.lower():
        raise RuntimeError("A secure EVENTOS_DATABASE_URL is required for PostgreSQL backup")
    with tempfile.NamedTemporaryFile(suffix=".dump",delete=False) as f:
        path=Path(f.name)
    try:
        proc=subprocess.run(["pg_dump","--format=custom","--no-owner","--no-acl",
                             "--file",str(path),url],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            env={**os.environ,"PGSSLMODE":"require"},timeout=600,check=False)
        if proc.returncode!=0:raise RuntimeError("pg_dump failed (no data uploaded)")
        if not 0<path.stat().st_size<MAX_SNAPSHOT:
            raise RuntimeError("PostgreSQL snapshot has invalid size")
        return path.read_bytes()
    finally: path.unlink(missing_ok=True)


def sqlite_snapshot():
    source=Path(os.environ.get("EVENTOS_DB_PATH","eventos.sqlite3"))
    if not source.is_file():raise RuntimeError("SQLite database unavailable")
    with tempfile.NamedTemporaryFile(suffix=".sqlite3",delete=False) as f:
        tmp=Path(f.name)
    try:
        with sqlite3.connect("file:"+str(source.resolve())+"?mode=ro",uri=True,timeout=30) as src:
            with sqlite3.connect(tmp) as dst:src.backup(dst,pages=512,sleep=0.05)
        with sqlite3.connect(tmp) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise RuntimeError("SQLite snapshot failed integrity check")
        if tmp.stat().st_size>MAX_SNAPSHOT:raise RuntimeError("Snapshot exceeds configured limit")
        return gzip.compress(tmp.read_bytes(),compresslevel=5)
    finally:tmp.unlink(missing_ok=True)


def make_snapshot():
    from production_database import postgres_enabled
    if postgres_enabled():return "postgres",_pg_dump()
    return "sqlite",sqlite_snapshot()


def backup_to_s3():
    if not enabled():
        raise RuntimeError("Scheduled encrypted off-site backup is not configured")
    engine,body=make_snapshot()
    stamp=datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")
    key=PREFIX+engine+"/"+stamp+"-"+secrets.token_hex(12)+(".dump" if engine=="postgres" else ".sqlite3.gz")
    digest=hashlib.sha256(body).hexdigest()
    args=dict(Bucket=destination(),Key=key,Body=body,
              ContentType="application/octet-stream",ServerSideEncryption="AES256",
              CacheControl="private,no-store",
              Metadata={"sha256":digest,"engine":engine,"format_version":"1"})
    kms=os.getenv("EVENTOS_BACKUP_KMS_KEY_ID")
    if kms:
        args["ServerSideEncryption"]="aws:kms"
        args["SSEKMSKeyId"]=kms
    store().put_object(**args)
    metadata=store().head_object(Bucket=destination(),Key=key)
    if metadata["ContentLength"]!=len(body) or metadata.get("Metadata",{}).get("sha256")!=digest:
        raise RuntimeError("Uploaded backup verification failed")
    return {"engine":engine,"object_key":key,"sha256":digest,"size_bytes":len(body),
            "created_at":datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _read_backup(key,expected_sha):
    if not key.startswith(PREFIX) or ".." in key or "\\" in key or len(key)>512:
        raise ValueError("Invalid backup object key")
    obj=store().get_object(Bucket=destination(),Key=key)
    if int(obj.get("ContentLength",0))>MAX_SNAPSHOT:
        raise RuntimeError("Backup exceeds maximum size")
    body=obj["Body"].read(MAX_SNAPSHOT+1)
    if len(body)>MAX_SNAPSHOT or hashlib.sha256(body).hexdigest()!=expected_sha:
        raise RuntimeError("Backup SHA-256 verification failed")
    return body


def restore_drill(key,expected_sha):
    """Restore data into an isolated target. Never run against production DB."""
    if not enabled():raise RuntimeError("Off-site backup storage is not configured")
    if len(expected_sha)!=64 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise ValueError("Invalid expected SHA256")
    body=_read_backup(key,expected_sha)
    if key.endswith(".sqlite3.gz"):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3",delete=False) as f:
            path=Path(f.name)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
                raw=gz.read(MAX_SNAPSHOT+1)
            if len(raw)>MAX_SNAPSHOT:raise RuntimeError("Expanded backup is too large")
            path.write_bytes(raw)
            with sqlite3.connect(path) as con:
                check=con.execute("PRAGMA integrity_check").fetchone()[0]
                if check!="ok":raise RuntimeError("SQLite restore integrity failed")
                tables=con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                if tables<5:raise RuntimeError("Restored schema is incomplete")
            return {"verified":True,"engine":"sqlite","tables":tables}
        finally:path.unlink(missing_ok=True)
    if not key.endswith(".dump"):raise RuntimeError("Unknown backup format")
    test_url=os.environ.get("EVENTOS_RESTORE_TEST_DATABASE_URL","")
    live_url=os.environ.get("EVENTOS_DATABASE_URL","")
    # Use a deliberately isolated scratch DB, ideally in another cluster. NEVER
    # issue --clean against the production hostname / database.
    if not test_url.startswith(("postgres://","postgresql://")) or "sslmode=disable" in test_url.lower():
        raise RuntimeError("A dedicated PostgreSQL restore-test database is required")
    live=urlsplit(live_url)
    scratch=urlsplit(test_url)
    if (not scratch.path.lstrip("/").startswith("eventos_restore_") or
            (scratch.hostname==live.hostname and scratch.path==live.path) or
            test_url==live_url):
        raise RuntimeError("Unsafe restore target: use eventos_restore_* scratch database")
    if not shutil.which("pg_restore"):
        raise RuntimeError("pg_restore is unavailable")
    with tempfile.NamedTemporaryFile(suffix=".dump",delete=False) as f:
        tmp=Path(f.name)
    try:
        tmp.write_bytes(body)
        outcome=subprocess.run(["pg_restore","--clean","--if-exists","--no-owner","--no-acl",
                                "--exit-on-error","--dbname",test_url,str(tmp)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               env={**os.environ,"PGSSLMODE":"require"},timeout=600,check=False)
        if outcome.returncode!=0:raise RuntimeError("PostgreSQL scratch restore failed")
        import psycopg
        with psycopg.connect(test_url,connect_timeout=10,sslmode="require") as con:
            count=con.execute("""SELECT COUNT(*) FROM information_schema.tables
                                 WHERE table_schema=current_schema() AND table_type='BASE TABLE'""").fetchone()[0]
        if count<5:raise RuntimeError("Restored PostgreSQL schema is incomplete")
        return {"verified":True,"engine":"postgres","tables":count}
    finally:tmp.unlink(missing_ok=True)

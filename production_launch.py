"""Production operations, scheduled off-site backups and deployment readiness.

Secrets are only accepted as headers, not query params; public health reveals
no customer data. Backup and restore-drill operations are explicit opt-in.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request, Response

from production_database import postgres_enabled
from production_mariadb import enabled as mariadb_enabled
from production_backups import enabled as backups_enabled, backup_to_s3, restore_drill


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_auth(request,env_name):
    expected=os.environ.get(env_name,"")
    supplied=request.headers.get("X-EventOS-Job-Token","")
    if len(expected)<32 or not secrets.compare_digest(expected,supplied):
        raise HTTPException(403,"Job token is not configured or invalid")


def production_flags():
    return {
        "database_engine":"postgres" if postgres_enabled() else "mariadb" if mariadb_enabled() else "sqlite",
        "managed_database_configured":postgres_enabled() and bool(os.getenv("EVENTOS_DATABASE_URL")),
        "offsite_backup_scheduled":backups_enabled() and len(os.getenv("EVENTOS_BACKUP_JOB_TOKEN",""))>=32,
        "restore_drill_configured":backups_enabled() and len(os.getenv("EVENTOS_RESTORE_DRILL_TOKEN",""))>=32,
        "cloud_media_configured":bool(os.getenv("EVENTOS_S3_BUCKET") and os.getenv("EVENTOS_S3_REGION")),
        "smtp_configured":bool(os.getenv("EVENTOS_SMTP_HOST") and os.getenv("EVENTOS_SMTP_FROM")),
        "stripe_configured":bool(os.getenv("EVENTOS_STRIPE_SECRET_KEY") and os.getenv("EVENTOS_STRIPE_WEBHOOK_SECRET")),
        "agency_connect_configured":bool(os.getenv("EVENTOS_CONNECT_ENABLED")=="1" and
                                         os.getenv("EVENTOS_CONNECT_SECRET_KEY") and
                                         os.getenv("EVENTOS_CONNECT_WEBHOOK_SECRET")),
        "production_mode":os.getenv("EVENTOS_PRODUCTION_MODE")=="1",
    }


def production_preflight():
    if os.getenv("EVENTOS_PRODUCTION_MODE")!="1":return
    required={
        "EVENTOS_DATABASE_ENGINE":"postgres",
        "EVENTOS_DEMO_SEED":"0",
        "EVENTOS_PUBLIC_DEMO":"0",
        "EVENTOS_SECURE_COOKIES":"1",
    }
    for name,value in required.items():
        if os.getenv(name,"")!=value:
            raise RuntimeError(f"Production launch requires {name}={value}")
    for name in ("EVENTOS_DATABASE_URL","EVENTOS_PUBLIC_BASE_URL","EVENTOS_BACKUP_BUCKET",
                 "EVENTOS_BACKUP_JOB_TOKEN","EVENTOS_RESTORE_DRILL_TOKEN",
                 "EVENTOS_S3_BUCKET","EVENTOS_S3_REGION",
                 "EVENTOS_MONITOR_TOKEN","EVENTOS_SMTP_HOST","EVENTOS_SMTP_FROM",
                 "EVENTOS_SMTP_USER","EVENTOS_SMTP_PASSWORD",
                 "EVENTOS_RESTORE_TEST_DATABASE_URL"):
        if not os.getenv(name):raise RuntimeError(f"Production launch requires {name}")
    if not os.environ["EVENTOS_PUBLIC_BASE_URL"].startswith("https://"):
        raise RuntimeError("Production launch requires HTTPS public origin")
    for secret_name in ("EVENTOS_BACKUP_JOB_TOKEN","EVENTOS_RESTORE_DRILL_TOKEN","EVENTOS_MONITOR_TOKEN"):
        if len(os.environ[secret_name])<32:
            raise RuntimeError(f"Production launch requires a 32+ character {secret_name}")
    if os.getenv("EVENTOS_BACKUP_BUCKET")==os.getenv("EVENTOS_S3_BUCKET"):
        raise RuntimeError("Production backups must use a separate private bucket from customer media")
    if os.getenv("EVENTOS_BACKUP_SCHEDULE_ENABLED")!="1":
        raise RuntimeError("Production launch requires external backup scheduling")
    import shutil
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        raise RuntimeError("Production launch requires PostgreSQL client binaries for backups and recovery")
    for name in ("EVENTOS_STRIPE_PRICE_PRO","EVENTOS_STRIPE_PRICE_BUSINESS"):
        if not os.getenv(name):raise RuntimeError(f"Production launch requires {name}")
    if not os.getenv("EVENTOS_STRIPE_SECRET_KEY") or not os.getenv("EVENTOS_STRIPE_WEBHOOK_SECRET"):
        raise RuntimeError("Production launch requires verified subscription billing credentials")
    if not os.getenv("EVENTOS_REQUIRE_VERIFIED_EMAIL")=="1":
        raise RuntimeError("Production launch requires email verification")


def install(app,db,access,get_user):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS production_backup_jobs(
          id TEXT PRIMARY KEY,job_kind TEXT NOT NULL,
          started_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,object_key TEXT NOT NULL DEFAULT '',
          digest TEXT NOT NULL DEFAULT '',error_code TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_production_backup_started
          ON production_backup_jobs(job_kind,started_at DESC);
        """)

    @app.get("/api/health/ready")
    def ready():
        try:
            with db() as con:
                con.execute("SELECT 1").fetchone()
            return {"ready":True}
        except Exception:
            # Avoid revealing database connection details in a public endpoint.
            return Response('{"ready":false}',media_type="application/json",status_code=503)

    @app.get("/api/ops/monitor")
    def monitor(request:Request):
        _job_auth(request,"EVENTOS_MONITOR_TOKEN")
        with db() as con:
            con.execute("SELECT 1").fetchone()
            history=[dict(r) for r in con.execute(
                """SELECT job_kind,status,started_at,finished_at,error_code
                   FROM production_backup_jobs ORDER BY started_at DESC LIMIT 12""")]
        return {"status":"ok","flags":production_flags(),"backup_jobs":history,"checked_at":_now()}

    def take_lease(con,kind):
        recent=con.execute("""SELECT status,started_at FROM production_backup_jobs
                              WHERE job_kind=? ORDER BY started_at DESC LIMIT 1""",(kind,)).fetchone()
        stamp=_now()
        if recent and recent["status"]=="running" and recent["started_at"]>(datetime.now(timezone.utc)-timedelta(minutes=45)).isoformat(timespec="seconds"):
            raise HTTPException(409,"An operation is already running")
        identifier=secrets.token_hex(18)
        con.execute("INSERT INTO production_backup_jobs(id,job_kind,started_at,status) VALUES (?,?,?,'running')",
                    (identifier,kind,stamp))
        return identifier

    def finish(identifier,ok,key="",digest="",error=""):
        with db() as con:
            con.execute("""UPDATE production_backup_jobs SET status=?,finished_at=?,object_key=?,
                            digest=?,error_code=? WHERE id=?""",
                        ("success" if ok else "failed",_now(),key,digest,error,identifier))

    @app.post("/api/ops/backup/run")
    def backup_job(request:Request):
        _job_auth(request,"EVENTOS_BACKUP_JOB_TOKEN")
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            job=take_lease(con,"backup")
        try:
            result=backup_to_s3()
            finish(job,True,result["object_key"],result["sha256"])
            return {"ok":True,"sha256":result["sha256"],"size_bytes":result["size_bytes"],
                    "engine":result["engine"],"backup_job_id":job}
        except Exception:
            finish(job,False,error="backup_error")
            raise HTTPException(503,"Off-site backup failed; check private service logs")

    @app.post("/api/ops/restore-drill/run")
    def restore_job(request:Request,payload:dict):
        _job_auth(request,"EVENTOS_RESTORE_DRILL_TOKEN")
        if not isinstance(payload,dict) or set(payload)!={"backup_job_id"}:
            raise HTTPException(422,"Choose an existing backup job")
        with db() as con:
            if payload["backup_job_id"]=="latest":
                row=con.execute("""SELECT object_key,digest FROM production_backup_jobs
                                   WHERE job_kind='backup' AND status='success'
                                   ORDER BY started_at DESC LIMIT 1""").fetchone()
            else:
                row=con.execute("""SELECT object_key,digest FROM production_backup_jobs
                                   WHERE id=? AND job_kind='backup' AND status='success'""",
                                (payload["backup_job_id"],)).fetchone()
            if not row:raise HTTPException(404,"Backup job not found")
            con.execute("BEGIN IMMEDIATE")
            job=take_lease(con,"restore_drill")
            key,digest=row["object_key"],row["digest"]
        try:
            summary=restore_drill(key,digest)
            finish(job,True,key,digest)
            return {"ok":True,**summary,"restore_job_id":job}
        except Exception:
            finish(job,False,error="restore_error")
            raise HTTPException(503,"Restore drill failed; investigate before launch")

    @app.get("/api/platform/launch-readiness")
    def launch_readiness(request:Request):
        with db() as con:
            user=get_user(request,con)
            if user["platform_role"]!="admin":raise HTTPException(403,"Platform administrator required")
            history=[dict(r) for r in con.execute("""SELECT job_kind,status,started_at,finished_at
               FROM production_backup_jobs ORDER BY started_at DESC LIMIT 10""")]
        flags=production_flags()
        last_backup=next((x for x in history if x["job_kind"]=="backup" and x["status"]=="success"),None)
        last_drill=next((x for x in history if x["job_kind"]=="restore_drill" and x["status"]=="success"),None)
        return {"flags":flags,"latest_backup":last_backup,"latest_restore_drill":last_drill,
                "fully_verified":False,
                "note":"Credentials, provider activation, database cutover, actual backups, recovery rehearsals and live payment processing require operator verification."}

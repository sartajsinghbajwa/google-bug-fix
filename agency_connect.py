"""Agency-specific Stripe Connect Express onboarding (no client-side acct trust).

Onboarding is owner-only, returns Stripe-hosted account links, and only the
signed Stripe Connect webhook updates account eligibility. Does not create
charges or imply customer invoice payments/payouts are enabled.
"""
from __future__ import annotations
import os
import re
import secrets
from datetime import datetime, timezone
from fastapi import HTTPException, Request


def enabled():
    return os.environ.get("EVENTOS_CONNECT_ENABLED")=="1" and bool(
        os.getenv("EVENTOS_CONNECT_SECRET_KEY") and os.getenv("EVENTOS_CONNECT_WEBHOOK_SECRET")
        and os.getenv("EVENTOS_PUBLIC_BASE_URL","").startswith("https://"))


def stripe_provider():
    if not enabled():raise HTTPException(503,"Stripe Connect is not configured")
    import stripe
    stripe.api_key=os.environ["EVENTOS_CONNECT_SECRET_KEY"]
    return stripe


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner(request,con,wid,access):
    member=access(request,con,wid)
    if member["role"]!="owner":raise HTTPException(403,"Only the agency workspace owner may onboard payments")
    workspace=con.execute("SELECT type FROM workspaces WHERE id=?",(wid,)).fetchone()
    if not workspace or workspace["type"]!="business":
        raise HTTPException(403,"Payment onboarding is for business workspaces")


def _status(row):
    if not row:return {"connected":False,"onboarding_complete":False,"charges_enabled":False,
                       "payouts_enabled":False,"details_submitted":False}
    return {"connected":True,"onboarding_complete":bool(row["details_submitted"] and row["payouts_enabled"]),
            "charges_enabled":bool(row["charges_enabled"]),"payouts_enabled":bool(row["payouts_enabled"]),
            "details_submitted":bool(row["details_submitted"]),"country":row["country"],
            "last_provider_update":row["updated_at"]}


def install(app,db,access,get_user):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS agency_connect_accounts(
          workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
          stripe_account_id TEXT NOT NULL UNIQUE,country TEXT NOT NULL,
          details_submitted INTEGER NOT NULL DEFAULT 0,charges_enabled INTEGER NOT NULL DEFAULT 0,
          payouts_enabled INTEGER NOT NULL DEFAULT 0,
          provider_event_created INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agency_connect_webhook_events(
          event_id TEXT PRIMARY KEY,event_created INTEGER NOT NULL,processed_at TEXT NOT NULL);
        """)

    @app.get("/api/w/{wid}/payment-onboarding")
    def get_onboarding(request:Request,wid:str):
        with db() as con:
            _owner(request,con,wid,access)
            row=con.execute("SELECT * FROM agency_connect_accounts WHERE workspace_id=?",(wid,)).fetchone()
            return {"configured":enabled(),"status":_status(row),
                    "note":"Stripe Connect onboarding does not by itself enable live invoice checkout. Charge routing, platform fees, refunds and payment reconciliation require separate approval."}

    @app.post("/api/w/{wid}/payment-onboarding/start")
    def begin_onboarding(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={"country"} or payload["country"] not in ("CA","US","GB","AU"):
            raise HTTPException(422,"Choose a supported agency business country")
        stripe=stripe_provider()
        with db() as con:
            _owner(request,con,wid,access)
            user=get_user(request,con)
            con.execute("BEGIN IMMEDIATE")
            existing=con.execute("SELECT stripe_account_id,country FROM agency_connect_accounts WHERE workspace_id=?",(wid,)).fetchone()
            if existing and existing["country"]!=payload["country"]:
                raise HTTPException(409,"Existing payment account belongs to another country")
            if existing:
                account_id=existing["stripe_account_id"]
            else:
                try:
                    account=stripe.Account.create(type="express",country=payload["country"],
                        email=user["email"],capabilities={"transfers":{"requested":True}},
                        metadata={"eventos_workspace_id":wid},
                        idempotency_key="eventos-connect-account-"+wid)
                except Exception:
                    raise HTTPException(502,"Payment provider onboarding is unavailable")
                account_id=getattr(account,"id","")
                if not isinstance(account_id,str) or not re.fullmatch(r"acct_[A-Za-z0-9]{10,}",account_id):
                    raise HTTPException(502,"Invalid response from payment provider")
                con.execute("""INSERT INTO agency_connect_accounts(workspace_id,stripe_account_id,country,updated_at)
                               VALUES (?,?,?,?)""",(wid,account_id,payload["country"],_now()))
        base=os.environ["EVENTOS_PUBLIC_BASE_URL"].rstrip("/")
        try:
            link=stripe.AccountLink.create(
                account=account_id,type="account_onboarding",
                refresh_url=base+"/app#/"+wid+"/paymentOnboarding",
                return_url=base+"/app#/"+wid+"/paymentOnboarding")
        except Exception:
            raise HTTPException(502,"Provider account created, but onboarding link could not be generated")
        url=getattr(link,"url","")
        if not isinstance(url,str) or not url.startswith("https://connect.stripe.com/"):
            raise HTTPException(502,"Invalid provider onboarding link")
        return {"url":url,"provider":"stripe_connect","account_created":True,
                "note":"Completing Stripe's forms does not automatically activate payouts; wait for verified provider status."}

    @app.post("/api/w/{wid}/payment-onboarding/refresh")
    def refresh_status(request:Request,wid:str):
        stripe=stripe_provider()
        with db() as con:
            _owner(request,con,wid,access)
            row=con.execute("SELECT * FROM agency_connect_accounts WHERE workspace_id=?",(wid,)).fetchone()
            if not row:raise HTTPException(404,"Start payment onboarding first")
            account_id=row["stripe_account_id"]
        try:
            acct=stripe.Account.retrieve(account_id)
        except Exception:
            raise HTTPException(502,"Unable to check payment provider status")
        if getattr(acct,"id",None)!=account_id:
            raise HTTPException(502,"Unexpected payment account response")
        values=(int(bool(acct.get("details_submitted"))),int(bool(acct.get("charges_enabled"))),
                int(bool(acct.get("payouts_enabled"))))
        with db() as con:
            _owner(request,con,wid,access)
            con.execute("""UPDATE agency_connect_accounts SET details_submitted=?,charges_enabled=?,
                          payouts_enabled=?,updated_at=? WHERE workspace_id=? AND stripe_account_id=?""",
                        (*values,_now(),wid,account_id))
            row=con.execute("SELECT * FROM agency_connect_accounts WHERE workspace_id=?",(wid,)).fetchone()
            return {"status":_status(row)}

    @app.post("/api/payments/connect-webhook")
    async def connect_webhook(request:Request):
        stripe=stripe_provider()
        raw=await request.body()
        if len(raw)>512*1024:raise HTTPException(413,"Webhook payload is too large")
        try:
            evt=stripe.Webhook.construct_event(raw,request.headers.get("stripe-signature",""),
                                               os.environ["EVENTOS_CONNECT_WEBHOOK_SECRET"])
        except Exception:
            raise HTTPException(400,"Invalid provider webhook signature")
        if evt.get("type")!="account.updated":return {"ok":True,"ignored":True}
        obj=evt.get("data",{}).get("object",{})
        aid=obj.get("id","")
        event_id=evt.get("id","")
        created=evt.get("created",0)
        if not isinstance(aid,str) or not aid.startswith("acct_") or not isinstance(event_id,str) or not event_id.startswith("evt_") or type(created) is not int:
            raise HTTPException(422,"Invalid provider event")
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM agency_connect_webhook_events WHERE event_id=?",(event_id,)).fetchone():
                return {"ok":True,"duplicate":True}
            row=con.execute("SELECT * FROM agency_connect_accounts WHERE stripe_account_id=?",(aid,)).fetchone()
            if row and created>=row["provider_event_created"]:
                con.execute("""UPDATE agency_connect_accounts SET details_submitted=?,charges_enabled=?,
                             payouts_enabled=?,provider_event_created=?,updated_at=? WHERE stripe_account_id=?""",
                            (int(bool(obj.get("details_submitted"))),
                             int(bool(obj.get("charges_enabled"))),
                             int(bool(obj.get("payouts_enabled"))),created,_now(),aid))
            con.execute("INSERT INTO agency_connect_webhook_events VALUES (?,?,?)",(event_id,created,_now()))
            return {"ok":True,"updated":bool(row)}

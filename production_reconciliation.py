"""Server-side Stripe subscription reconciliation after signed webhook activation.

Reconciliation retrieves provider truth with the platform secret key; a browser
redirect can never activate a plan. Handles dropped webhooks, cancellations
and expired subscriptions without deleting customer records.
"""
from __future__ import annotations
import os
import secrets
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from phase7_billing import stripe_api, price_to_plan, activate_plan, ACTIVE, VALID_STATUSES, stripe_ready


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_token(request):
    expected=os.environ.get("EVENTOS_BILLING_RECONCILE_TOKEN","")
    given=request.headers.get("X-EventOS-Job-Token","")
    if len(expected)<32 or not secrets.compare_digest(expected,given):
        raise HTTPException(403,"Subscription reconciliation job is not configured")


def reconcile(db):
    if not stripe_ready():raise RuntimeError("Stripe subscriptions are not configured")
    stripe=stripe_api()
    with db() as con:
        accounts=[dict(r) for r in con.execute("""SELECT workspace_id,stripe_customer_id,stripe_subscription_id
                   FROM saas_billing_accounts WHERE stripe_subscription_id!=''
                   ORDER BY workspace_id LIMIT 200""")]
    result={"examined":0,"updated":0,"unchanged":0,"errors":0}
    for account in accounts:
        result["examined"]+=1
        try:
            subscription=stripe.Subscription.retrieve(account["stripe_subscription_id"])
            sub=dict(subscription)
            if (sub.get("id")!=account["stripe_subscription_id"] or
                sub.get("customer")!=account["stripe_customer_id"] or
                (sub.get("metadata") or {}).get("eventos_workspace_id")!=account["workspace_id"]):
                raise RuntimeError("Stripe subscription does not match its owning workspace")
            lines=(sub.get("items") or {}).get("data") or []
            price=(lines[0].get("price") or {}).get("id","") if lines else ""
            with db() as con:
                plan=price_to_plan(price,con)
            status=sub.get("status","")
            if not plan or status not in VALID_STATUSES:
                raise RuntimeError("Provider returned unknown subscription state or plan")
            effective=plan if status in ACTIVE else "free"
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                persisted=con.execute("""SELECT a.stripe_customer_id,a.stripe_subscription_id,
                    w.billing_plan,w.billing_status FROM saas_billing_accounts a
                    JOIN workspaces w ON a.workspace_id=w.id WHERE a.workspace_id=?""",
                    (account["workspace_id"],)).fetchone()
                if not persisted or persisted["stripe_customer_id"]!=account["stripe_customer_id"] or persisted["stripe_subscription_id"]!=account["stripe_subscription_id"]:
                    raise RuntimeError("Linked subscription changed during reconciliation")
                if persisted["billing_plan"]==effective and persisted["billing_status"]==status:
                    result["unchanged"]+=1
                    continue
                activate_plan(con,account["workspace_id"],effective,status)
                con.execute("""UPDATE saas_billing_accounts SET stripe_price_id=?,updated_at=?,
                     stripe_event_created=? WHERE workspace_id=?""",
                    (price,_stamp(),int(datetime.now(timezone.utc).timestamp()),account["workspace_id"]))
                con.execute("""INSERT INTO saas_admin_audit(actor_user_id,action,target_type,target_id,created_at)
                     VALUES('system','subscription.reconciled','workspace',?,?)""",
                    (account["workspace_id"],_stamp()))
                result["updated"]+=1
        except Exception:
            result["errors"]+=1
    return result


def install(app,db):
    @app.post("/api/ops/billing/reconcile")
    def reconcile_job(request:Request):
        _require_token(request)
        try:
            result=reconcile(db)
        except RuntimeError:
            raise HTTPException(503,"Provider reconciliation could not be started")
        # Do not falsely report a clean billing reconciliation when any account failed.
        if result["errors"]:
            return {"ok":False,**result,"note":"Investigate provider mismatch or failed retrieval"}
        return {"ok":True,**result}

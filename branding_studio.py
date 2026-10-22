"""Workspace white-label studio and personal quick-start wizard.

White label is a BUSINESS workspace feature. A verified DNS TXT record proves
control of a hostname; hosting and TLS still require provider configuration.
Custom email headers never claim an unverified sender address.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timezone
from html import escape
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from event_websites import DEFAULTS, ensure as ensure_site, valid_content
from saas_access import enforce as saas_enforce

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DOMAIN = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".test", ".example", ".invalid", ".onrender.com")
DEFAULT_BRAND = {
    "name": "", "tagline": "", "primary_color": "#23473d", "accent_color": "#b49a6a",
    "email_name": "", "reply_to": "", "footer_text": "", "logo_url": "",
}
PRESETS = [
    {"id":"soft-garden","name":"Soft Garden","description":"Organic botanical colors for intimate celebrations","theme":"garden","accent":"#658976","premium":False,"category":"Wedding"},
    {"id":"classic-vows","name":"Classic Vows","description":"Elegant serif styling for meaningful milestones","theme":"classic","accent":"#a47d5d","premium":False,"category":"Wedding"},
    {"id":"clean-celebration","name":"Clean Celebration","description":"Crisp, modern layout for any occasion","theme":"minimal","accent":"#333f3a","premium":False,"category":"General"},
    {"id":"midnight-gala","name":"Midnight Gala","description":"Statement gold on a dramatic evening palette","theme":"night","accent":"#bda57d","premium":True,"category":"Gala"},
    {"id":"modern-agency","name":"Modern Agency","description":"Professional editorial feel for clients and brands","theme":"modern","accent":"#386a78","premium":True,"category":"Corporate"},
    {"id":"romantic-olive","name":"Romantic Olive","description":"Warm, understated accents with timeless type","theme":"classic","accent":"#7b8060","premium":True,"category":"Wedding"},
    {"id":"garden-party","name":"Garden Party","description":"A refined celebratory look for parties and showers","theme":"garden","accent":"#a07687","premium":True,"category":"Birthday"},
]
PRESET_MAP = {p["id"]:p for p in PRESETS}
ALLOWED_TYPES = {"Birthday","Wedding","Anniversary","Conference","Corporate meeting","Festival","Concert","Fundraiser","Baby shower","Private party","Client event","Other"}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value, limit, field, required=False):
    if not isinstance(value, str) or len(value) > limit or any(x in value for x in ("\x00","\r","\n")):
        raise HTTPException(422, "Invalid "+field)
    value = value.strip()
    if required and not value:
        raise HTTPException(422, field+" is required")
    return value


def normalize_domain(value):
    value = clean(value, 253, "hostname", True).lower().rstrip(".")
    if not DOMAIN.fullmatch(value) or value.startswith("www.www.") or value.endswith(BLOCKED_SUFFIXES):
        raise HTTPException(422, "Enter a public domain you own, without https://, ports, or paths")
    return value


def available_premium(con, wid):
    r=con.execute("SELECT type,billing_plan,billing_status FROM workspaces WHERE id=?", (wid,)).fetchone()
    return bool(r and (r["billing_plan"] in ("pro","business") and r["billing_status"] in ("active","trialing","manual")
                       or r["type"] == "business" and r["billing_plan"] == "legacy" and r["billing_status"] == "legacy"))


def business(con,wid):
    row=con.execute("SELECT type FROM workspaces WHERE id=?",(wid,)).fetchone()
    if not row or row["type"] != "business":
        raise HTTPException(403, "Branding Studio is available in business workspaces")
    return row


def public_brand(con,wid):
    row=con.execute("SELECT name,tagline,primary_color,accent_color,email_name,reply_to,footer_text,"
                    "CASE WHEN length(logo_png)>0 THEN 1 ELSE 0 END AS has_logo "
                    "FROM brand_profiles WHERE workspace_id=?",(wid,)).fetchone()
    if not row:return None
    d=dict(row)
    d["logo_url"]="/api/brand/logo/"+wid+".png" if d.pop("has_logo") else ""
    return d


def migrate(db):
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS brand_profiles (
            workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
            name TEXT NOT NULL DEFAULT '',tagline TEXT NOT NULL DEFAULT '',
            primary_color TEXT NOT NULL DEFAULT '#23473d',
            accent_color TEXT NOT NULL DEFAULT '#b49a6a',
            email_name TEXT NOT NULL DEFAULT '',reply_to TEXT NOT NULL DEFAULT '',
            footer_text TEXT NOT NULL DEFAULT '',logo_png BLOB NOT NULL DEFAULT X'',
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS brand_domains (
            id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            hostname TEXT NOT NULL UNIQUE, verification_token TEXT NOT NULL,
            verified_at TEXT NOT NULL DEFAULT '',target TEXT NOT NULL,
            event_id TEXT REFERENCES events(id) ON DELETE CASCADE,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_brand_domains_workspace ON brand_domains(workspace_id);
        CREATE TABLE IF NOT EXISTS brand_templates(
            id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name TEXT NOT NULL,content_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_brand_templates_workspace ON brand_templates(workspace_id);
        """)


def template_content(preset, original=None):
    content={**DEFAULTS,"sections":dict(DEFAULTS["sections"]),
             "section_order":list(DEFAULTS["section_order"]),
             "accommodations":[],"faq":[]}
    content["template"]=preset["theme"]
    content["accent"]=preset["accent"]
    content["welcome"]="We are so glad you're here. All the details for our celebration are together in one place."
    if original:
        # Keep event-specific copy and private organizer settings already entered.
        content.update(original)
        content["template"]=preset["theme"]
        content["accent"]=preset["accent"]
    return valid_content(content)


def saved_template_content(content):
    # Only reusable visual/layout values; no previous event story, location, or contacts.
    return valid_content({
        **DEFAULTS,
        "template":content["template"],"accent":content["accent"],
        "welcome":content["welcome"],"story_title":content["story_title"],
        "footer":content["footer"],
        "sections":content["sections"],"section_order":content["section_order"],
        "accommodations":[],"faq":[],
    })


def _safe_template(con,wid,template_id):
    if template_id in PRESET_MAP:
        p=PRESET_MAP[template_id]
        if p["premium"] and not available_premium(con,wid):
            raise HTTPException(403,"This premium template requires an eligible paid plan")
        return template_content(p)
    row=con.execute("SELECT content_json FROM brand_templates WHERE workspace_id=? AND id=?",
                    (wid,template_id)).fetchone()
    if not row:raise HTTPException(404,"Template not found in this workspace")
    return valid_content(json.loads(row["content_json"]))


def apply_template(con,wid,eid,template_id):
    event=con.execute("SELECT id,name FROM events WHERE workspace_id=? AND id=?",(wid,eid)).fetchone()
    if not event:raise HTTPException(404,"Event not found in this workspace")
    site=ensure_site(con,wid,eid,event)
    original=json.loads(site["content_json"])
    template=_safe_template(con,wid,template_id)
    # Apply design/layout, keep event-specific description and text.
    merged={**DEFAULTS,**original}
    for field in ("template","accent","welcome","story_title","footer","sections","section_order"):
        merged[field]=template[field]
    cleaned=valid_content(merged)
    con.execute("UPDATE event_websites SET content_json=?,updated_at=? WHERE event_id=? AND workspace_id=?",
                (json.dumps(cleaned),utc(),eid,wid))
    return cleaned


def _txt_verifies(hostname,token):
    try:
        import dns.resolver
        answers=dns.resolver.resolve("_eventos-verify."+hostname, "TXT", lifetime=4)
        expected=("eventos-verification="+token).encode("utf-8")
        return any(b"".join(answer.strings)==expected for answer in answers)
    except Exception:
        # Network/DNS failure is not proof of ownership.
        return False


def _read_logo(payload):
    if not isinstance(payload,dict) or set(payload)!={"data_base64","content_type"} or payload["content_type"] not in ("image/png","image/jpeg","image/webp"):
        raise HTTPException(422,"Choose a PNG, JPG, or WebP logo")
    raw=payload["data_base64"]
    if not isinstance(raw,str) or len(raw)>2_000_000:
        raise HTTPException(413,"Logo exceeds maximum upload size")
    try:
        source=base64.b64decode(raw,validate=True)
        if len(source)>1_500_000:raise HTTPException(413,"Logo exceeds maximum upload size")
        with Image.open(io.BytesIO(source)) as image:
            if image.format not in ("PNG","JPEG","WEBP") or image.width*image.height>4_000_000:
                raise ValueError()
            image=ImageOps.exif_transpose(image)
            image.thumbnail((640,640))
            out=io.BytesIO()
            image.convert("RGBA").save(out,format="PNG",optimize=True)
            if out.tell()>1_000_000:raise HTTPException(413,"Logo exceeds maximum processed size")
            return out.getvalue()
    except (ValueError,OSError,UnidentifiedImageError,binascii.Error):
        raise HTTPException(422,"Invalid logo image")


def _domain_public(con,hostname):
    return con.execute(
        """SELECT d.*, w.type workspace_type FROM brand_domains d
           JOIN workspaces w ON w.id=d.workspace_id
           WHERE d.hostname=? AND d.verified_at!='' AND w.is_active=1""",
        (hostname,)).fetchone()


def domain_root(db,request,landing_file):
    """Only exact TXT-verified hostnames are routed; never redirect to Host."""
    host=request.headers.get("host","").split(":")[0].lower().rstrip(".")
    with db() as con:
        mapping=_domain_public(con,host)
        if mapping:
            if mapping["target"]=="website":
                row=con.execute(
                    "SELECT slug,access_token,published FROM event_websites WHERE event_id=? AND workspace_id=?",
                    (mapping["event_id"],mapping["workspace_id"])).fetchone()
                if row and row["published"]:
                    return RedirectResponse("/event/"+row["slug"]+"/"+row["access_token"],status_code=302)
                return HTMLResponse("<h1>Event website not published</h1>",status_code=404)
            name=(public_brand(con,mapping["workspace_id"]) or {}).get("name","Your event planner")
            return HTMLResponse(
                '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta name="robots" content="noindex"><title>Private client portal</title>'
                '<main style="font:16px system-ui;max-width:550px;margin:12vh auto;padding:24px">'
                '<h1>'+escape(name)+'</h1><p>Welcome to your private client portal.</p>'
                '<p>Use the secure invitation link and PIN your planner shared with you to access your documents.</p>'
                '</main>',headers={"Cache-Control":"no-store"})
    from fastapi.responses import FileResponse
    return FileResponse(landing_file,headers={"Cache-Control":"no-store,max-age=0"})


def install_branding(app,db,access,check_event,get_user,uid,now):
    migrate(db)

    @app.get("/api/w/{wid}/branding/templates")
    def template_gallery(request:Request,wid:str):
        with db() as con:
            access(request,con,wid)
            presets=[{**p,"available":not p["premium"] or available_premium(con,wid)} for p in PRESETS]
            custom=[{"id":r["id"],"name":r["name"],"premium":False,"available":True,
                     "custom":True,"description":"Saved from a workspace event","category":"Custom"}
                    for r in con.execute("SELECT id,name FROM brand_templates WHERE workspace_id=? ORDER BY created_at DESC",(wid,))]
            return {"templates":presets+custom,"can_create_custom":True}

    @app.get("/api/w/{wid}/branding")
    def get_brand(request:Request,wid:str):
        with db() as con:
            member=access(request,con,wid)
            business(con,wid)
            profile=public_brand(con,wid) or dict(DEFAULT_BRAND)
            domains=[{"id":r["id"],"hostname":r["hostname"],"target":r["target"],
                      "event_id":r["event_id"],"verified":bool(r["verified_at"]),
                      "txt_host":"_eventos-verify."+r["hostname"],
                      "txt_value":"eventos-verification="+r["verification_token"]}
                     for r in con.execute("SELECT * FROM brand_domains WHERE workspace_id=? ORDER BY created_at DESC",(wid,))]
            presets=[{**p,"available":not p["premium"] or available_premium(con,wid)} for p in PRESETS]
            own=[{"id":r["id"],"name":r["name"],"premium":False,"available":True,"custom":True}
                 for r in con.execute("SELECT id,name FROM brand_templates WHERE workspace_id=? ORDER BY created_at DESC",(wid,))]
            return {"profile":profile,"domains":domains,"templates":presets+own,
                    "can_edit":member["role"] in ("owner","editor"),
                    "can_manage_domains":member["role"]=="owner",
                    "hosting_note":"DNS verification is not hosting activation. Add the domain and TLS in Render or your hosting provider before using it."}

    @app.put("/api/w/{wid}/branding")
    def save_brand(request:Request,wid:str,payload:dict):
        caps={"name":120,"tagline":180,"primary_color":7,"accent_color":7,
              "email_name":100,"reply_to":255,"footer_text":220}
        if not isinstance(payload,dict) or set(payload)!=set(caps):
            raise HTTPException(422,"Provide complete branding fields")
        out={k:clean(payload[k],n,k) for k,n in caps.items()}
        for k in ("primary_color","accent_color"):
            if not HEX.fullmatch(out[k]):raise HTTPException(422,"Invalid brand color")
            out[k]=out[k].lower()
        if out["reply_to"] and not EMAIL.fullmatch(out["reply_to"]):
            raise HTTPException(422,"Invalid reply-to address")
        with db() as con:
            access(request,con,wid,True);business(con,wid)
            con.execute("""INSERT INTO brand_profiles
                 (workspace_id,name,tagline,primary_color,accent_color,email_name,reply_to,footer_text,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(workspace_id) DO UPDATE SET
                 name=excluded.name,tagline=excluded.tagline,primary_color=excluded.primary_color,
                 accent_color=excluded.accent_color,email_name=excluded.email_name,
                 reply_to=excluded.reply_to,footer_text=excluded.footer_text,updated_at=excluded.updated_at""",
                 (wid,*out.values(),now()))
            return public_brand(con,wid)

    @app.post("/api/w/{wid}/branding/logo")
    def upload_logo(request:Request,wid:str,payload:dict):
        logo=_read_logo(payload)
        with db() as con:
            access(request,con,wid,True);business(con,wid)
            con.execute("""INSERT INTO brand_profiles(workspace_id,logo_png,updated_at)
                         VALUES(?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
                         logo_png=excluded.logo_png,updated_at=excluded.updated_at""",(wid,logo,now()))
            return public_brand(con,wid)

    @app.delete("/api/w/{wid}/branding/logo")
    def delete_logo(request:Request,wid:str):
        with db() as con:
            access(request,con,wid,True);business(con,wid)
            con.execute("UPDATE brand_profiles SET logo_png=X'',updated_at=? WHERE workspace_id=?",(now(),wid))
            return public_brand(con,wid)

    @app.get("/api/brand/logo/{wid}.png")
    def public_logo(wid:str):
        with db() as con:
            r=con.execute("""SELECT b.logo_png FROM brand_profiles b JOIN workspaces w ON b.workspace_id=w.id
                             WHERE w.id=? AND w.type='business' AND w.is_active=1""",(wid,)).fetchone()
            if not r or not r["logo_png"]:raise HTTPException(404,"Logo not found")
            return Response(bytes(r["logo_png"]),media_type="image/png",headers={
                "Cache-Control":"public,max-age=120","X-Content-Type-Options":"nosniff"})

    @app.post("/api/w/{wid}/branding/domains")
    def add_domain(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={"hostname","target","event_id"}:
            raise HTTPException(422,"Enter the domain, target, and event")
        hostname=normalize_domain(payload["hostname"])
        if payload["target"] not in ("website","client_portal"):
            raise HTTPException(422,"Invalid domain target")
        with db() as con:
            role=access(request,con,wid);business(con,wid)
            if role["role"]!="owner":raise HTTPException(403,"Only workspace owners may manage domains")
            eid=payload["event_id"]
            if payload["target"]=="website":
                if not isinstance(eid,str):raise HTTPException(422,"Choose an event website")
                check_event(con,wid,eid)
            else:
                if eid is not None:raise HTTPException(422,"Client portal domain belongs to the workspace")
            count=con.execute("SELECT COUNT(*) FROM brand_domains WHERE workspace_id=?",(wid,)).fetchone()[0]
            if count>=5:raise HTTPException(409,"Maximum five domains per workspace")
            did=uid(); token=secrets.token_urlsafe(24)
            try:
                con.execute("""INSERT INTO brand_domains(id,workspace_id,hostname,verification_token,target,event_id,created_at)
                              VALUES(?,?,?,?,?,?,?)""",(did,wid,hostname,token,payload["target"],eid,now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409,"This domain is already connected to another workspace")
            return {"id":did,"hostname":hostname,"verified":False,
                    "txt_host":"_eventos-verify."+hostname,
                    "txt_value":"eventos-verification="+token,
                    "hosting_note":"DNS proof is not a live custom domain. Configure custom domain and TLS on the hosting provider."}

    @app.post("/api/w/{wid}/branding/domains/{did}/verify")
    def verify_domain(request:Request,wid:str,did:str):
        with db() as con:
            role=access(request,con,wid);business(con,wid)
            if role["role"]!="owner":raise HTTPException(403,"Only workspace owners may verify domains")
            row=con.execute("SELECT * FROM brand_domains WHERE workspace_id=? AND id=?",(wid,did)).fetchone()
            if not row:raise HTTPException(404,"Domain not found")
            hostname,token=row["hostname"],row["verification_token"]
        if not _txt_verifies(hostname,token):
            raise HTTPException(409,"Matching DNS TXT record not found yet")
        with db() as con:
            access(request,con,wid)
            con.execute("UPDATE brand_domains SET verified_at=? WHERE id=? AND workspace_id=?",(now(),did,wid))
        return {"verified":True,"hostname":hostname,"hosting_ready":False}

    @app.delete("/api/w/{wid}/branding/domains/{did}")
    def remove_domain(request:Request,wid:str,did:str):
        with db() as con:
            member=access(request,con,wid);business(con,wid)
            if member["role"]!="owner":raise HTTPException(403,"Only workspace owners may remove domains")
            if not con.execute("DELETE FROM brand_domains WHERE workspace_id=? AND id=?",(wid,did)).rowcount:
                raise HTTPException(404,"Domain not found")
            return {"ok":True}

    @app.post("/api/w/{wid}/branding/templates")
    def save_template(request:Request,wid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={"name","event_id"}:
            raise HTTPException(422,"Provide a name and source event")
        name=clean(payload["name"],80,"template name",True)
        with db() as con:
            access(request,con,wid,True)
            eid=payload["event_id"]
            check_event(con,wid,eid)
            source=con.execute("SELECT content_json FROM event_websites WHERE workspace_id=? AND event_id=?",(wid,eid)).fetchone()
            if not source:raise HTTPException(404,"Design the source event website first")
            count=con.execute("SELECT COUNT(*) FROM brand_templates WHERE workspace_id=?",(wid,)).fetchone()[0]
            if count>=30:raise HTTPException(409,"Maximum thirty reusable templates")
            design=saved_template_content(json.loads(source["content_json"]))
            id=uid()
            con.execute("INSERT INTO brand_templates VALUES (?,?,?,?,?)",(id,wid,name,json.dumps(design),now()))
            return {"id":id,"name":name,"premium":False,"custom":True}

    @app.delete("/api/w/{wid}/branding/templates/{tid}")
    def delete_template(request:Request,wid:str,tid:str):
        with db() as con:
            access(request,con,wid,True)
            if not con.execute("DELETE FROM brand_templates WHERE workspace_id=? AND id=?",(wid,tid)).rowcount:
                raise HTTPException(404,"Template not found")
            return {"ok":True}

    @app.post("/api/w/{wid}/events/{eid}/branding/apply-template")
    def set_template(request:Request,wid:str,eid:str,payload:dict):
        if not isinstance(payload,dict) or set(payload)!={"template_id"} or not isinstance(payload["template_id"],str):
            raise HTTPException(422,"Choose a template")
        with db() as con:
            access(request,con,wid,True);check_event(con,wid,eid)
            return {"ok":True,"content":apply_template(con,wid,eid,payload["template_id"])}

    @app.post("/api/w/{wid}/quick-create")
    def quick_create(request:Request,wid:str,payload:dict):
        required={"name","type","start_date","location","expected_guests","template_id"}
        if not isinstance(payload,dict) or set(payload)!=required:
            raise HTTPException(422,"Complete the event wizard")
        name=clean(payload["name"],160,"event name",True)
        kind=payload["type"]
        if not isinstance(kind,str) or kind not in ALLOWED_TYPES:raise HTTPException(422,"Invalid event type")
        date_text=clean(payload["start_date"],10,"date",True)
        try:
            if date.fromisoformat(date_text).isoformat()!=date_text:raise ValueError()
        except ValueError:raise HTTPException(422,"Enter a valid event date")
        location=clean(payload["location"],500,"venue")
        guests=payload["expected_guests"]
        if type(guests) is not int or not 0<=guests<=100000:raise HTTPException(422,"Invalid guest count")
        template_id=clean(payload["template_id"],100,"template",True)
        with db() as con:
            access(request,con,wid,True)
            ws=con.execute("SELECT type FROM workspaces WHERE id=?",(wid,)).fetchone()
            if not ws or ws["type"]!="personal":raise HTTPException(403,"Quick-start wizard is for personal workspaces")
            # Check all requirements before writing an event (transactional).
            design=_safe_template(con,wid,template_id)
            saas_enforce(con,wid,"events")
            eid=uid()
            con.execute("""INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (eid,wid,None,None,name,kind,date_text,None,location,guests,0,"Draft",now()))
            site=ensure_site(con,wid,eid)
            con.execute("UPDATE event_websites SET content_json=?,updated_at=? WHERE event_id=? AND workspace_id=?",
                        (json.dumps(design),now(),eid,wid))
            for title in ("Confirm venue and date","Build your guest list","Design and send invitations"):
                con.execute("""INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)""",
                            (uid(),wid,eid,title,"","Medium","Open",""))
            return {"id":eid,"name":name,"website_created":True,"published":False}

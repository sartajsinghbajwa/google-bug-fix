"""Phase 13: enhanced check-in using the existing EventOS operations schema.

Adds partial-party arrivals, audit records, CSV exports, reusable existing QR
passes and authenticated scanning. Existing operations APIs remain unchanged.
"""
from __future__ import annotations

import csv
import io
import os
import secrets
from urllib.parse import urlparse, unquote

import qrcode
from qrcode.image.svg import SvgPathImage
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from event_operations import details as operational_details


class AttendanceIn(BaseModel):
    seats: int = Field(ge=0, le=10000)


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=500)
    seats: int | None = Field(default=None, ge=0, le=10000)


def _guest(con, wid, eid, gid):
    row = con.execute(
        "SELECT * FROM guests WHERE id=? AND workspace_id=? AND event_id=?",
        (gid, wid, eid),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Guest not found for this event")
    return dict(row)


def _display(g):
    return {
        "id": g["id"], "name": g["name"], "email": g["email"],
        "group_name": g["group_name"], "party_size": g["party_size"],
        "seat_limit": int(g["attending"]) if g["rsvp"] == "Confirmed" else 0,
        "rsvp": g["rsvp"], "checked_in_seats": int(g["checked_in_count"] or 0),
        "checked_in_at": g["checked_in_at"],
    }


def _entry(con, wid, eid, gid):
    g = next((g for g in operational_details(con, wid, eid) if g["id"] == gid), None)
    if g is None:
        raise HTTPException(404, "Guest not found for this event")
    return _display(g)


def _set_attendance(con, wid, eid, gid, seats, actor, now, new_id):
    guest = _guest(con, wid, eid, gid)
    current = _entry(con, wid, eid, gid)
    if seats > 0 and guest["rsvp"] != "Confirmed":
        raise HTTPException(409, "Only confirmed invitations may check in")
    if seats > current["seat_limit"]:
        raise HTTPException(422, "Checked-in seats cannot exceed confirmed attendees")
    prior = con.execute(
        "SELECT attendees FROM event_checkins WHERE guest_id=? AND workspace_id=? AND event_id=?",
        (gid, wid, eid),
    ).fetchone()
    previous = int(prior["attendees"]) if prior else 0
    if seats != previous:
        timestamp = now()
        if seats == 0:
            # The existing checkin lookup considers any row to be checked in.
            con.execute(
                "DELETE FROM event_checkins WHERE guest_id=? AND workspace_id=? AND event_id=?",
                (gid, wid, eid),
            )
        else:
            con.execute(
                """INSERT INTO event_checkins
                   (guest_id,workspace_id,event_id,attendees,checked_in_at,staff_id)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(guest_id) DO UPDATE SET
                     attendees=excluded.attendees,
                     checked_in_at=excluded.checked_in_at,
                     staff_id=excluded.staff_id""",
                (gid, wid, eid, seats, timestamp, actor),
            )
        con.execute(
            """INSERT INTO event_checkin_history
               (id,workspace_id,event_id,guest_id,actor_id,previous_seats,new_seats,changed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (new_id(), wid, eid, gid, actor, previous, seats, timestamp),
        )
    return _entry(con, wid, eid, gid)


def _code_for(con, gid):
    row = con.execute("SELECT code FROM event_checkin_codes WHERE guest_id=?", (gid,)).fetchone()
    if row:
        return row["code"]
    code = secrets.token_urlsafe(24)
    con.execute("INSERT INTO event_checkin_codes(guest_id,code) VALUES (?,?)", (gid, code))
    return code


def _host_url(request):
    override = os.environ.get("EVENTOS_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if override.startswith("https://"):
        return override
    return str(request.base_url).rstrip("/")


def _csv_safe(value):
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def _scan_code(raw):
    """Accept existing EventOS check-in URLs, paths or opaque staff pass codes."""
    raw = raw.strip()
    parsed = urlparse(raw)
    if parsed.scheme in ("https", "http") and parsed.path.startswith("/checkin/"):
        return unquote(parsed.path[len("/checkin/"):])
    if raw.startswith("/checkin/"):
        return unquote(raw[len("/checkin/"):].split("?", 1)[0])
    if "://" not in raw and "/" not in raw and 1 <= len(raw) <= 100 and not any(c.isspace() for c in raw):
        return raw
    raise HTTPException(422, "Not an EventOS check-in pass")


def install_checkin(app, db, access, check_event, get_user, now, new_id):
    """The operations plugin must be installed first (it owns event_checkins)."""
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS event_checkin_history (
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          guest_id TEXT NOT NULL REFERENCES guests(id) ON DELETE CASCADE,
          actor_id TEXT NOT NULL REFERENCES users(id),
          previous_seats INTEGER NOT NULL,
          new_seats INTEGER NOT NULL,
          changed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_checkin_history_event
          ON event_checkin_history(workspace_id,event_id,changed_at);
        """)

    @app.get("/api/w/{wid}/events/{eid}/checkin")
    def checkin_list(request: Request, wid: str, eid: str):
        with db() as con:
            membership = access(request, con, wid)
            event = check_event(con, wid, eid)
            entries = [_display(g) for g in operational_details(con, wid, eid)]
        summary = {
            "invitation_groups": len(entries),
            "invited_seats": sum(g["party_size"] for g in entries),
            "confirmed_seats": sum(g["seat_limit"] for g in entries),
            "checked_in_seats": sum(g["checked_in_seats"] for g in entries),
            "arrived_groups": sum(g["checked_in_seats"] > 0 for g in entries),
            "fully_checked_in_groups": sum(g["seat_limit"] > 0 and g["checked_in_seats"] >= g["seat_limit"] for g in entries),
        }
        return {"event": {"name": event["name"], "status": event["status"]},
                "can_edit": membership["role"] in ("owner", "editor"),
                "summary": summary, "guests": entries}

    @app.get("/api/w/{wid}/events/{eid}/checkin/export.csv")
    def checkin_export(request: Request, wid: str, eid: str):
        with db() as con:
            access(request, con, wid)
            check_event(con, wid, eid)
            entries = [_display(g) for g in operational_details(con, wid, eid)]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["name","email","group","rsvp","invited_seats","confirmed_seats","checked_in_seats","checked_in_at"])
        for g in entries:
            writer.writerow([_csv_safe(g[key]) for key in ("name","email","group_name","rsvp")]
                            + [g["party_size"], g["seat_limit"], g["checked_in_seats"], g["checked_in_at"] or ""])
        return Response(
            "\ufeff" + out.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="eventos-checkin.csv"',
                     "Cache-Control": "private, no-store"},
        )

    @app.get("/api/w/{wid}/events/{eid}/checkin/history")
    def checkin_history(request: Request, wid: str, eid: str):
        with db() as con:
            access(request, con, wid)
            check_event(con, wid, eid)
            return [dict(r) for r in con.execute(
                """SELECT h.guest_id,g.name AS guest_name,u.full_name AS operator_name,
                          h.previous_seats,h.new_seats,h.changed_at
                   FROM event_checkin_history h
                   JOIN guests g ON g.id=h.guest_id
                   JOIN users u ON u.id=h.actor_id
                   WHERE h.workspace_id=? AND h.event_id=?
                   ORDER BY h.changed_at DESC,h.rowid DESC LIMIT 100""", (wid, eid)
            )]

    @app.get("/api/w/{wid}/events/{eid}/checkin/{gid}/qr.svg")
    def checkin_badge(request: Request, wid: str, eid: str, gid: str):
        with db() as con:
            access(request, con, wid)
            check_event(con, wid, eid)
            _guest(con, wid, eid, gid)
            # Reuse existing EventOS pass codes to avoid two incompatible QR systems.
            code = _code_for(con, gid)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=8, border=2)
        qr.add_data(_host_url(request) + "/checkin/" + code)
        qr.make(fit=True)
        svg_bytes = qr.make_image(image_factory=SvgPathImage).to_string()
        return Response(svg_bytes, media_type="image/svg+xml",
                        headers={"Cache-Control": "private, no-store",
                                 "X-Content-Type-Options": "nosniff"})

    @app.post("/api/w/{wid}/events/{eid}/checkin/scan")
    def checkin_scan(request: Request, wid: str, eid: str, data: ScanIn):
        with db() as con:
            access(request, con, wid, True)
            event = check_event(con, wid, eid)
            if event["status"] == "Cancelled":
                raise HTTPException(409, "Cannot check in guests to a cancelled event")
            user = get_user(request, con)
            code = _scan_code(data.code)
            found = con.execute(
                "SELECT guest_id FROM event_checkin_codes WHERE code=?", (code,)
            ).fetchone()
            if found is None:
                raise HTTPException(404, "Check-in pass not found")
            gid = found["guest_id"]
            _guest(con, wid, eid, gid)
            con.execute("BEGIN IMMEDIATE")
            current = _entry(con, wid, eid, gid)
            seats = current["seat_limit"] if data.seats is None else data.seats
            return _set_attendance(con, wid, eid, gid, seats, user["id"], now, new_id)

    @app.post("/api/w/{wid}/events/{eid}/checkin/{gid}")
    def checkin_update(request: Request, wid: str, eid: str, gid: str, data: AttendanceIn):
        with db() as con:
            access(request, con, wid, True)
            event = check_event(con, wid, eid)
            if event["status"] == "Cancelled":
                raise HTTPException(409, "Cannot check in guests to a cancelled event")
            user = get_user(request, con)
            con.execute("BEGIN IMMEDIATE")
            return _set_attendance(con, wid, eid, gid, data.seats,
                                   user["id"], now, new_id)

"""Read-only, event-scoped data for the EventOS Command Center.

All input IDs are authenticated and validated by the FastAPI route. Every query is
still explicitly scoped to both workspace and event to avoid cross-event leakage.
This module does not change database schema or mutate planning records.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json


def _one(con, query, args):
    return dict(con.execute(query, args).fetchone())


def _rows(con, query, args):
    return [dict(row) for row in con.execute(query, args).fetchall()]


def build_command_center(con, wid: str, eid: str, event: dict, today: date | None = None):
    today = today or datetime.now(timezone.utc).date()
    day = today.isoformat()
    week = (today + timedelta(days=6)).isoformat()
    scope = (wid, eid)

    guests = _one(con, """
        SELECT COUNT(*) AS invitations,
               COALESCE(SUM(party_size), 0) AS invited_seats,
               COALESCE(SUM(CASE WHEN rsvp='Confirmed' THEN COALESCE((SELECT json_extract(rr.party_json,'$.total') FROM rsvp_responses rr WHERE rr.guest_id=guests.id),party_size) ELSE 0 END), 0) AS confirmed_seats,
               SUM(CASE WHEN rsvp='Confirmed' THEN 1 ELSE 0 END) AS confirmed,
               SUM(CASE WHEN rsvp='Pending' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN rsvp='Maybe' THEN 1 ELSE 0 END) AS maybe,
               SUM(CASE WHEN rsvp='Declined' THEN 1 ELSE 0 END) AS declined
        FROM guests WHERE workspace_id=? AND event_id=?
    """, scope)
    for key in ('confirmed', 'pending', 'maybe', 'declined'):
        guests[key] = guests[key] or 0
    guests['response_rate'] = round(100 * (guests['invitations'] - guests['pending']) / guests['invitations']) if guests['invitations'] else 0

    # Aggregate individual meal selections for confirmed RSVPs only. Show all
    # configured choices, including choices with zero selections.
    settings_row = con.execute(
        'SELECT settings_json FROM rsvp_forms WHERE workspace_id=? AND event_id=?', scope
    ).fetchone()
    settings = json.loads(settings_row['settings_json'] or '{}') if settings_row else {}
    party_settings = settings.get('party') or {}
    options = (party_settings.get('meal_options') or []) if party_settings.get('enabled') and party_settings.get('meals_optional') else []
    meals = {'enabled': bool(options), 'choices': [{'name': option, 'count': 0} for option in options], 'selected': 0}
    if options:
        choices = {option: item for option, item in zip(options, meals['choices'])}
        meal_rows = con.execute("""
            SELECT r.party_json FROM rsvp_responses r
            JOIN guests g ON g.id=r.guest_id
            WHERE r.event_id=? AND g.workspace_id=? AND g.rsvp='Confirmed'
        """, (eid, wid)).fetchall()
        for response in meal_rows:
            party = json.loads(response['party_json'] or '{}')
            for guest in party.get('guests', []):
                choice = guest.get('meal','')
                if choice in choices:
                    choices[choice]['count'] += 1
                    meals['selected'] += 1

    tasks = _one(con, """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='Done' THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN status!='Done' THEN 1 ELSE 0 END) AS open,
               SUM(CASE WHEN status!='Done' AND due_date!='' AND due_date<? THEN 1 ELSE 0 END) AS overdue,
               SUM(CASE WHEN status!='Done' AND due_date>=? AND due_date<=? THEN 1 ELSE 0 END) AS due_soon
        FROM tasks WHERE workspace_id=? AND event_id=?
    """, (day, day, week, *scope))
    for key in ('done', 'open', 'overdue', 'due_soon'):
        tasks[key] = tasks[key] or 0
    tasks['completion_rate'] = round(tasks['done'] * 100 / tasks['total']) if tasks['total'] else 0
    priority = "CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END"
    tasks['next'] = _rows(con, f"""
        SELECT id, title, due_date, priority, status, assigned_to FROM tasks
        WHERE workspace_id=? AND event_id=? AND status!='Done'
        ORDER BY CASE WHEN due_date!='' AND due_date<? THEN 0
                      WHEN due_date!='' THEN 1 ELSE 2 END,
                 CASE WHEN due_date='' THEN '9999-12-31' ELSE due_date END,
                 {priority}, title LIMIT 5
    """, (*scope, day))

    budget = _one(con, """
        SELECT COALESCE(SUM(estimated),0) AS estimated,
               COALESCE(SUM(committed),0) AS committed,
               COALESCE(SUM(actual),0) AS actual,
               COALESCE(SUM(paid),0) AS paid
        FROM budget_items WHERE workspace_id=? AND event_id=?
    """, scope)
    budget['target'] = event['budget']
    budget['remaining_committable'] = event['budget'] - budget['committed']
    budget['committed_percent'] = round(budget['committed'] * 100 / event['budget']) if event['budget'] > 0 else None
    budget['over_budget'] = event['budget'] > 0 and (budget['committed'] > event['budget'] or budget['actual'] > event['budget'])
    budget['largest_items'] = _rows(con, """
        SELECT name, category, estimated, committed, actual, paid FROM budget_items
        WHERE workspace_id=? AND event_id=? ORDER BY committed DESC, actual DESC, name LIMIT 3
    """, scope)

    vendors = _one(con, """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='Booked' THEN 1 ELSE 0 END) AS booked,
               SUM(CASE WHEN status='Quote requested' THEN 1 ELSE 0 END) AS quotes_pending
        FROM event_vendors WHERE workspace_id=? AND event_id=?
    """, scope)
    vendors['booked'] = vendors['booked'] or 0
    vendors['quotes_pending'] = vendors['quotes_pending'] or 0
    schedule = _rows(con, """
        SELECT id, title, start_at, end_at, location FROM schedule_items
        WHERE workspace_id=? AND event_id=? AND substr(start_at,1,10)>=?
        ORDER BY start_at, title LIMIT 5
    """, (*scope, day))
    schedule_count = con.execute(
        'SELECT COUNT(*) FROM schedule_items WHERE workspace_id=? AND event_id=?', scope
    ).fetchone()[0]
    try:
        days_until = (date.fromisoformat(event['start_date'][:10]) - today).days
    except (TypeError, ValueError):
        days_until = None

    attention = []
    def flag(kind, title, detail, page, tone='neutral'):
        attention.append({'kind': kind, 'title': title, 'detail': detail, 'page': page, 'tone': tone})

    is_active = event['status'] not in ('Completed', 'Cancelled')
    if is_active:
        if tasks['overdue']:
            flag('overdue', f"{tasks['overdue']} overdue task{'s' if tasks['overdue'] != 1 else ''}", 'Review tasks with past due dates.', 'tasks', 'urgent')
        if budget['over_budget']:
            flag('budget', 'Budget needs a review', 'Committed or actual expenses exceed your event budget.', 'budget', 'urgent')
        if tasks['due_soon']:
            flag('due_soon', f"{tasks['due_soon']} task{'s' if tasks['due_soon'] != 1 else ''} due this week", 'Check the next seven days of deadlines.', 'tasks', 'notice')
        if guests['pending']:
            flag('rsvp', f"{guests['pending']} RSVP{'s' if guests['pending'] != 1 else ''} awaiting a reply", 'Review outstanding invitations; reminders are manual.', 'guests', 'notice')
        if vendors['quotes_pending']:
            flag('quotes', f"{vendors['quotes_pending']} vendor quote{'s' if vendors['quotes_pending'] != 1 else ''} requested", 'Follow up with vendors on outstanding quotes.', 'eventVendors', 'notice')
        if days_until is not None and 0 <= days_until <= 30 and not schedule_count:
            flag('timeline', 'Plan your run of show', 'No schedule items have been added for this event.', 'schedule')
        if not tasks['total']:
            flag('checklist', 'Start your planning checklist', 'Add tasks to track responsibilities and deadlines.', 'tasks')
    return {'event': event, 'as_of_date': day, 'days_until': days_until,
            'guests': guests, 'meals': meals, 'tasks': tasks, 'budget': budget,
            'vendors': vendors, 'schedule': schedule, 'schedule_count': schedule_count,
            'attention': attention[:6]}

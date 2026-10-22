"""Read-only cross-tenant operations reporting for authorized platform admins.

No migrations or payment-state mutations. Billing figures are grouped by currency;
recorded agency payments must not be confused with verified Stripe revenue.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, timezone
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


def _month_sequence(today=None):
    today=today or datetime.now(timezone.utc).date()
    current=today.year*12+today.month-1
    months=[]
    for offset in range(11,-1,-1):
        n=current-offset
        months.append(f'{n//12:04d}-{n%12+1:02d}')
    return months


def _rows(con, sql, args=()):
    return [dict(row) for row in con.execute(sql,args)]


def _count(con, table):
    # table names must only come from hard-coded internal constants
    return con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]


def _status(con, table, column):
    return [{'name':str(row['name'] or 'Unspecified'),'count':int(row['total'])}
            for row in con.execute(f'SELECT {column} AS name, COUNT(*) AS total FROM {table} GROUP BY {column} ORDER BY total DESC')]


def _trend(con, table, today=None):
    months=_month_sequence(today)
    rows=con.execute(f'SELECT SUBSTR(created_at,1,7) AS month, COUNT(*) AS total FROM {table} GROUP BY SUBSTR(created_at,1,7)')
    counts={str(row['month']):int(row['total']) for row in rows}
    return [{'month':month,'count':counts.get(month,0)} for month in months]


def snapshot(con, now=None):
    """Grounded metrics only. No synthetic growth/revenue projections."""
    counts={name:_count(con,table) for name,table in {
        'accounts':'users','workspaces':'workspaces','events':'events',
        'guests':'guests','vendors':'vendors','contacts':'contacts',
        'leads':'leads','clients':'agency_clients','invoices':'agency_documents',
        'checkout_attempts':'agency_invoice_checkouts','agency_payment_records':'agency_payments',
        'event_vendor_assignments':'event_vendors','checked_in_guest_records':'event_checkins',
        'rsvp_submissions':'rsvp_responses','galleries':'event_galleries',
        'event_websites':'event_websites','gift_registries':'gift_registries',
        'billing_webhook_events':'saas_billing_events', 'invoice_webhook_events':'agency_invoice_webhook_events',
        'agency_connect_accounts':'agency_connect_accounts', 'admin_audit':'saas_admin_audit',
        'issued_access_codes':'saas_codes'
    }.items()}
    counts['issued_invoices']=con.execute("SELECT COUNT(*) FROM agency_documents WHERE kind='invoice'").fetchone()[0]
    counts['published_event_websites']=con.execute('SELECT COUNT(*) FROM event_websites WHERE published=1').fetchone()[0]
    counts['active_clients']=con.execute('SELECT COUNT(*) FROM agency_clients WHERE active=1').fetchone()[0]
    counts['active_vendor_connections']=con.execute('SELECT COUNT(*) FROM agency_connect_accounts WHERE charges_enabled=1').fetchone()[0]
    counts['active_users']=con.execute('SELECT COUNT(*) FROM users WHERE is_active=1').fetchone()[0]
    counts['active_workspaces']=con.execute('SELECT COUNT(*) FROM workspaces WHERE is_active=1').fetchone()[0]
    counts['successful_invoice_checkouts']=con.execute("SELECT COUNT(*) FROM agency_invoice_checkouts WHERE status IN ('paid','complete','completed')").fetchone()[0]
    counts['stripe_subscriptions_linked']=con.execute("SELECT COUNT(*) FROM saas_billing_accounts WHERE stripe_subscription_id<>''").fetchone()[0]
    groups={
        'workspace_types':_status(con,'workspaces','type'),
        'workspace_plans':_status(con,'workspaces','billing_plan'),
        'subscription_statuses':_status(con,'workspaces','billing_status'),
        'event_statuses':_status(con,'events','status'),
        'lead_stages':_status(con,'leads','stage'),
        'invoice_statuses':_status(con,'agency_documents','status'),
        'checkout_statuses':_status(con,'agency_invoice_checkouts','status'),
        'vendor_categories':_status(con,'vendors','category'),
        'guest_rsvp_statuses':_status(con,'guests','rsvp'),
    }
    money={
        'issued_invoices':_rows(con,"""SELECT w.currency AS currency,COUNT(*) AS count,
            COALESCE(SUM(d.total_cents),0) AS amount_cents FROM agency_documents d
            JOIN workspaces w ON w.id=d.workspace_id
            WHERE d.kind='invoice' GROUP BY w.currency ORDER BY currency"""),
        'recorded_agency_payments':_rows(con,"""SELECT w.currency AS currency, COUNT(*) AS count,
            COALESCE(SUM(p.amount_cents),0) AS amount_cents FROM agency_payments p
            JOIN workspaces w ON w.id=p.workspace_id GROUP BY w.currency ORDER BY currency"""),
        'stripe_invoice_checkouts_paid':_rows(con,"""SELECT c.currency AS currency, COUNT(*) AS count,
            COALESCE(SUM(c.amount_cents),0) AS amount_cents FROM agency_invoice_checkouts c
            WHERE c.status IN ('paid','complete','completed') GROUP BY c.currency ORDER BY currency"""),
    }
    recent={
        'users':_rows(con,'SELECT id,full_name,email,platform_role,is_active,created_at FROM users ORDER BY created_at DESC LIMIT 12'),
        'workspaces':_rows(con,'SELECT id,name,type,currency,billing_plan,billing_status,is_active,created_at FROM workspaces ORDER BY created_at DESC LIMIT 12'),
        'events':_rows(con,'SELECT e.name,e.status,e.created_at,w.name AS workspace_name FROM events e JOIN workspaces w ON w.id=e.workspace_id ORDER BY e.created_at DESC LIMIT 12'),
        'payments':_rows(con,'SELECT p.created_at,p.amount_cents,p.method,w.currency,w.name AS workspace_name FROM agency_payments p JOIN workspaces w ON w.id=p.workspace_id ORDER BY p.created_at DESC LIMIT 12'),
        'audit':_rows(con,'SELECT action,target_type,created_at FROM saas_admin_audit ORDER BY created_at DESC LIMIT 20'),
    }
    directory={
        'vendors':_rows(con,'''SELECT v.id,v.name,v.category,v.email,v.phone,w.name AS workspace_name
            FROM vendors v JOIN workspaces w ON v.workspace_id=w.id
            ORDER BY v.name ASC LIMIT 200'''),
        'clients':_rows(con,'''SELECT c.id,c.client_name,c.client_email,c.active,c.created_at,
            w.name AS workspace_name FROM agency_clients c JOIN workspaces w ON c.workspace_id=w.id
            ORDER BY c.created_at DESC LIMIT 200'''),
        'invoices':_rows(con,'''SELECT d.id,d.number,d.title,d.kind,d.status,d.total_cents,d.due_date,
            w.currency,w.name AS workspace_name FROM agency_documents d
            JOIN workspaces w ON d.workspace_id=w.id WHERE d.kind='invoice'
            ORDER BY d.created_at DESC LIMIT 200''')
    }
    return {
        'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'counts':counts,'groups':groups,'money':money,'recent':recent,'directory':directory,
        'trends':{'users':_trend(con,'users',now),'workspaces':_trend(con,'workspaces',now),'events':_trend(con,'events',now)},
        'notes':{
            'scope':'Platform-wide operational records across all workspaces',
            'financial':'These figures represent issued invoices, recorded agency payments and verified checkout statuses; not Stripe-settled platform revenue or MRR.',
            'limits':'Recent activity lists show only the latest records. CSV exports are bounded for shared-hosting safety.'
        }
    }


# All table names and exported columns are constants, not user-controlled SQL.
_EXPORTS={
    'users':('users','id,email,full_name,platform_role,is_active,email_verified,created_at'),
    'workspaces':('workspaces','id,name,type,currency,is_active,billing_plan,billing_status,max_events,max_guests,max_members,created_at'),
    'events':('events','id,workspace_id,name,type,status,start_date,end_date,expected_guests,created_at'),
    'vendors':('vendors','id,workspace_id,name,category,email,phone'),
    'contacts':('contacts','id,workspace_id,name,email,phone,kind'),
    'clients':('agency_clients','id,workspace_id,client_name,client_email,active,created_at'),
    'invoices':('agency_documents','id,workspace_id,event_id,kind,number,title,status,total_cents,due_date,created_at'),
    'payments':('agency_payments','id,workspace_id,event_id,amount_cents,method,paid_on,created_at'),
    'access_codes':('saas_codes','id,label,workspace_type,max_uses,uses,is_active,expires_at,created_at'),
    'audit':('saas_admin_audit','id,actor_user_id,action,target_type,target_id,created_at'),
}


def _csv_value(value):
    # Protect administrators opening exports in Excel/Sheets from formula injection.
    value='' if value is None else str(value)
    if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):
        return "'"+value
    return value


def install(app,db,get_user):
    def require_admin(request,con):
        user=get_user(request,con)
        if user.get('platform_role')!='admin' or not user.get('is_active',1):
            raise HTTPException(status_code=403,detail='Platform administrator permission required')

    @app.get('/api/platform/analytics')
    def analytics(request:Request):
        with db() as con:
            require_admin(request,con)
            return snapshot(con)

    @app.patch('/api/platform/directory/vendors/{vendor_id}')
    def edit_vendor(request:Request,vendor_id:str,payload:dict):
        from phase7_billing import audit
        import re
        if not isinstance(payload,dict) or not payload or set(payload)-{'name','category','email','phone'}:
            raise HTTPException(422,'Invalid vendor settings')
        clean={}
        for k,v in payload.items():
            if not isinstance(v,str) or len(v)>180 or '\x00' in v:
                raise HTTPException(422,'Invalid vendor '+k)
            if k in ('name','category') and not v.strip():raise HTTPException(422,'Vendor name/category required')
            if k=='email' and v and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v.strip()):
                raise HTTPException(422,'Invalid email')
            clean[k]=v.strip()
        with db() as con:
            require_admin(request,con)
            row=con.execute('SELECT id FROM vendors WHERE id=?',(vendor_id,)).fetchone()
            if not row:raise HTTPException(404,'Vendor not found')
            con.execute('UPDATE vendors SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),vendor_id))
            actor=get_user(request,con)
            audit(con,actor['id'],'vendor.updated','vendor',vendor_id)
        return {'ok':True}

    @app.patch('/api/platform/directory/clients/{client_id}')
    def edit_client(request:Request,client_id:str,payload:dict):
        from phase7_billing import audit
        import re
        if not isinstance(payload,dict) or not payload or set(payload)-{'client_name','client_email','active'}:
            raise HTTPException(422,'Invalid client settings')
        clean={}
        for k,v in payload.items():
            if k=='active':
                if type(v) is not bool:raise HTTPException(422,'Invalid client state')
                clean[k]=int(v)
            elif not isinstance(v,str) or not v.strip() or len(v)>180 or '\x00' in v:
                raise HTTPException(422,'Invalid client '+k)
            else:
                if k=='client_email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v.strip()):
                    raise HTTPException(422,'Invalid email')
                clean[k]=v.strip()
        with db() as con:
            require_admin(request,con)
            row=con.execute('SELECT id FROM agency_clients WHERE id=?',(client_id,)).fetchone()
            if not row:raise HTTPException(404,'Client not found')
            con.execute('UPDATE agency_clients SET '+','.join(k+'=?' for k in clean)+' WHERE id=?',(*clean.values(),client_id))
            actor=get_user(request,con)
            audit(con,actor['id'],'client.updated','agency_client',client_id)
        return {'ok':True}

    @app.get('/api/platform/export/{dataset}')
    def export(request:Request,dataset:str):
        if dataset not in _EXPORTS:raise HTTPException(404,'Unsupported export')
        table,columns=_EXPORTS[dataset]
        fields=columns.split(',')
        with db() as con:
            require_admin(request,con)
            count=_count(con,table)
            if count>10000:
                raise HTTPException(413,'Dataset exceeds shared-hosting export limit (10,000 rows). Use a filtered administrative export.')
            results=_rows(con,f'SELECT {columns} FROM {table} LIMIT 10001')
        if len(results)>10000:
            raise HTTPException(413,'Dataset too large')
        buffer=io.StringIO(newline='')
        writer=csv.writer(buffer)
        writer.writerow(fields)
        for row in results:writer.writerow([_csv_value(row.get(f)) for f in fields])
        output=buffer.getvalue().encode('utf-8-sig')
        return StreamingResponse(iter([output]),media_type='text/csv; charset=utf-8',headers={
            'Content-Disposition':f'attachment; filename="eventos-{dataset}-export.csv"',
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

"""Additive Party Hub setup for WHC cPanel -> Setup Python App -> Execute Python Script.

Requires a verified backup, app code uploaded, and EVENTOS_DATABASE_ENGINE=mariadb.
Does not delete existing accounts, workspaces, events, invoices or demo data.
Can resume after a failed partial initialization using IF NOT EXISTS, but report
SQL errors before any further attempt.
"""
import os
from production_mariadb import connect
from party_hub import TABLES, INDEXES
from account_support import schema_statements

if os.environ.get('EVENTOS_DATABASE_ENGINE', '').lower()!='mariadb':
    raise SystemExit('STOP: This setup requires the staging MariaDB engine.')

con=connect()
try:
    before={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    if not {'users','workspaces','events','vendors','event_vendors','saas_billing_accounts'}.issubset(before):
        raise SystemExit('STOP: EventOS base schema is not initialized. No changes made.')
    print('PASS: Existing EventOS schema found. Existing rows will be preserved.')
    # Account/support installer was supplied with the prior consolidated release.
    # Run it here too in case the operator applies the new all-in-one release directly.
    for statement in schema_statements():con.execute(statement)
    con.commit()
    print('PASS: Account and support tables verified.')
    for name,statement in TABLES.items():
        con.execute(statement)
        print('READY:',name)
    for statement in INDEXES:con.execute(statement)
    con.commit()
    present={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    missing=set(TABLES)-present
    if missing:raise SystemExit('STOP: Party Hub tables missing; inspect schema setup output.')
    print('SUCCESS: All',len(TABLES),'Party Hub tables available and indexes prepared.')
    print('PASS: EventOS core tables and customer data were not dropped or reset.')
    print('NEXT: Restart staging and test marketplace, quote flow and Party Hub moderation.')
except Exception:
    con.rollback()
    print('STOP: Schema setup failed. MariaDB DDL may have been partially committed; investigate before retrying.')
    raise
finally:
    con.close()

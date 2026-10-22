"""WHC cPanel one-time additive setup for Party Hub + Account Support + DIY planner.

Run this from cPanel Setup Python App -> Execute Python Script AFTER uploading
all release files, BEFORE restarting the Passenger application. No DROP/DELETE.
Credentials come only from cPanel application environment variables.
"""
import os
from production_mariadb import connect
from account_support import schema_statements
from party_hub import TABLES as HUB_TABLES, INDEXES as HUB_INDEXES
from diy_planner import DDL as DIY_TABLES

if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
 raise SystemExit('STOP: This setup is only for MariaDB staging, not SQLite.')
con=connect()
try:
 before={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
 if not {'users','workspaces','events','vendors','tasks','budget_items','saas_billing_accounts'}.issubset(before):
  raise SystemExit('STOP: EventOS base schema missing; no changes were made.')
 print('PASS: Existing MariaDB EventOS tables detected; customer data will not be reset.')
 for stmt in schema_statements():con.execute(stmt)
 print('PASS: Account and Support Center tables checked.')
 for name,sql in HUB_TABLES.items():con.execute(sql)
 for sql in HUB_INDEXES:con.execute(sql)
 print('PASS: All',len(HUB_TABLES),'Party Hub tables checked.')
 for name,sql in DIY_TABLES.items():con.execute(sql)
 con.commit()
 after={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
 missing=set(HUB_TABLES)|set(DIY_TABLES)
 if missing-after:raise RuntimeError('Missing tables; do not restart.')
 count={name:con.execute('SELECT COUNT(*) AS n FROM '+name).fetchone()['n'] for name in DIY_TABLES}
 if any(count.values()):print('NOTE: Existing DIY plans preserved:',count)
 print('SUCCESS:',len(DIY_TABLES),'DIY planner tables available,',len(HUB_TABLES),'Party Hub tables available.')
 print('No existing EventOS users, events, quotes, accounts, contracts or invoices were dropped.')
 print('NEXT: Restart only the staging Python application and test the wizard and budgets.')
except Exception:
 con.rollback()
 print('STOP: Setup failed. DDL may have partially committed. Do NOT drop tables or rerun blindly.')
 raise
finally:con.close()

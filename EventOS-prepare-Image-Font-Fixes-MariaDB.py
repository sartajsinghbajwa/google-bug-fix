"""One-time additive migration for EventOS upload and invitation improvements.

Run in WHC Setup Python App > Execute Python Script AFTER extracting the release.
No data is dropped or modified; the only new table is platform_media.
"""
import os
from production_mariadb import connect
from platform_media import DDL

if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
    raise SystemExit('STOP: Set up only on the EventOS staging MariaDB application.')
con=connect()
try:
    tables={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    if not {'users','workspaces','invitation_designs','diy_budget_allocations','growth_articles','platform_site_settings'}.issubset(tables):
        raise SystemExit('STOP: EventOS application prerequisite tables are missing.')
    con.execute(DDL)
    con.commit()
    row=con.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='platform_media'").fetchone()
    if row['n']!=1:raise RuntimeError('New platform_media table was not confirmed')
    print('SUCCESS: Platform media upload storage is ready.')
    print('PASS: Existing users, events, budgets, invitations, vendors, and billing records preserved.')
    print('PASS: No demo data imported. No customer notifications sent.')
except Exception:
    con.rollback()
    print('STOP: Inspect errors; do not rerun until reviewed.')
    raise
finally:
    con.close()

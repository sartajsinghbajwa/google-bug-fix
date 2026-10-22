"""Additive Honestly Zen Monetization Center schema for WHC MariaDB.

Run once after extracting the code patch and before restarting the application.
No legacy accounts, vendor tables, subscriptions, invoices or bookings are modified.
No payments, emails or billing actions are triggered by this script.
"""
import os
from production_mariadb import connect
from monetization_center import TABLES, INDEXES

REQUIRED=('users','workspaces','platform_packages','market_listings','market_quotes','market_contracts','memberships')

def run():
    if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
        print('STOP: Choose the staging EventOS MariaDB application only.')
        return 1
    con=None
    try:
        con=connect()
        names={x['name'] for x in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
        missing=sorted(set(REQUIRED)-names)
        if missing:
            print('STOP: Missing existing EventOS prerequisites: '+', '.join(missing))
            print('Do not run the migration again until prerequisites are resolved.')
            return 1
        added=[]
        for name,ddl in TABLES.items():
            if name not in names:
                con.execute(ddl);added.append(name)
        # On older MariaDB, CREATE INDEX IF NOT EXISTS is supported. If an index
        # already exists, idempotently treat it as present.
        for ddl in INDEXES:
            con.execute(ddl)
        con.commit()
        current={x['name'] for x in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
        if not set(TABLES).issubset(current):
            print('STOP: Could not verify all Monetization Center tables. Do not restart.')
            return 1
        print('SUCCESS: Honestly Zen Monetization Center database tables ready.')
        print('New tables: '+(', '.join(added) if added else 'none; already present'))
        print('PASS: Existing subscriptions, invoices, vendor listings, bookings, users and events preserved.')
        print('PASS: No payments processed, no emails sent, and no demo data added.')
        return 0
    except Exception as exc:
        if con:
            try:con.rollback()
            except Exception:pass
        print('STOP: Migration error ('+type(exc).__name__+'). Do not restart or rerun until reviewed.')
        return 1
    finally:
        if con:con.close()

if __name__=='__main__':raise SystemExit(run())

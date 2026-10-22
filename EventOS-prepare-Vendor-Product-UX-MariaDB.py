"""Add the two vendor product merchandising tables to existing WHC staging MariaDB.

Only additive schema; requires previous Party Hub migration. Create a WHC backup first.
Do not run against live customer production without independent verification.
"""
import os
from production_mariadb import connect
from party_hub import TABLES

NEW_TABLES=('market_listing_details','market_listing_photos')
REQUIRED=('users','workspaces','market_listings','party_posts','party_replies')

def run():
    if os.getenv('EVENTOS_DATABASE_ENGINE','').strip().lower()!='mariadb':
        print('STOP: Run only for the EventOS staging MariaDB app.')
        return 1
    con=None
    try:
        con=connect()
        found={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
        missing=sorted(set(REQUIRED)-found)
        if missing:
            print('STOP: Required earlier Party Hub tables missing: '+', '.join(missing))
            print('Do not restart; install the missing prerequisites first.')
            return 1
        created=[]
        for name in NEW_TABLES:
            if name not in found:
                con.execute(TABLES[name]);created.append(name)
        con.commit()
        after={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
        if not set(NEW_TABLES).issubset(after):
            print('STOP: Vendor merchandising tables could not be verified.')
            return 1
        print('SUCCESS: Vendor product listings upgrade ready.')
        print('New tables: '+(', '.join(created) if created else 'already installed'))
        print('PASS: Existing users, events, listings, posts, quotes, invoices and subscriptions preserved.')
        print('PASS: No test data, payments or emails created.')
        return 0
    except Exception as error:
        if con is not None:
            try:con.rollback()
            except Exception:pass
        print('STOP: Vendor migration failed ('+type(error).__name__+').')
        print('Do not rerun or restart until the error is reviewed. Hide database credentials.')
        return 1
    finally:
        if con is not None:con.close()

if __name__=='__main__':raise SystemExit(run())

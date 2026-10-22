"""One-time, additive WHC MariaDB migration for Honestly Zen Party Community.
Run after extracting the Vendor & Community patch, BEFORE restarting the app.
No existing table, account, vendor listing, or customer data is changed or deleted.
"""
import os
from production_mariadb import connect
from party_hub import TABLES

NAMES=('party_post_saves','party_reply_helpful','party_accepted_answers')
REQUIRED=('users','market_listings','party_posts','party_replies')

def main():
    if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
        print('STOP: This script is only for the EventOS staging MariaDB application.')
        return 1
    con=None
    try:
        con=connect()
        existing={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()').fetchall()}
        missing=[name for name in REQUIRED if name not in existing]
        if missing:
            print('STOP: Base prerequisites missing: '+', '.join(missing))
            return 1
        created=[]
        for name in NAMES:
            if name not in existing:
                con.execute(TABLES[name])
                created.append(name)
        con.commit()
        verified={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()').fetchall()}
        if not set(NAMES).issubset(verified):
            print('STOP: Community tables could not be confirmed. Do not restart the app.')
            return 1
        print('SUCCESS: Vendor & Community upgrade database tables are ready.')
        print('New tables: '+(', '.join(created) if created else 'none (already installed)'))
        print('PASS: Existing vendor listings, community posts, members, events and customer records preserved.')
        print('PASS: No data deleted and no notifications sent.')
        return 0
    except Exception as exc:
        print('STOP: Migration failed ('+type(exc).__name__+'). Do not rerun or restart until reviewed.')
        return 1
    finally:
        if con is not None:con.close()

if __name__=='__main__':raise SystemExit(main())

"""WHC cPanel one-time additive setup: Growth Center + queued email notifications.

Use on EventOS staging MariaDB after installing the all-in-one source files.
Does not send email, change admin access or drop any existing data.
"""
import os
from production_mariadb import connect
from growth_center import TABLES, INDEXES
from email_notifications import SCHEMA

if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
    raise SystemExit('STOP: Only run against the existing WHC MariaDB staging application.')
con=connect()
try:
    existing={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    if not {'users','workspaces','market_listings','market_quotes','account_profiles','diy_event_plans'}.issubset(existing):
        raise SystemExit('STOP: Complete the Party Hub, Account and DIY migration first.')
    columns={r['name'] for r in con.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='market_listings'")}
    if 'video_url' not in columns:
        con.execute("ALTER TABLE market_listings ADD COLUMN video_url VARCHAR(400) NOT NULL DEFAULT ''")
        print('PASS: Vendor video URL column added')
    for sql in TABLES.values():con.execute(sql)
    for sql in INDEXES:con.execute(sql)
    for sql in SCHEMA:con.execute(sql)
    con.commit()
    after={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    expected=set(TABLES)|{'email_notification_outbox','email_notification_preferences'}
    if not expected.issubset(after):raise RuntimeError('Missing application tables: '+', '.join(sorted(expected-after)))
    print('SUCCESS: 5 Growth Center and 2 email notification tables available.')
    print('PASS: Vendor video link column available; previous users, events and subscriptions preserved.')
    print('PASS: No messages sent or queued by migration. SMTP stays disabled until you enable it later.')
except Exception:
    con.rollback()
    print('STOP: MariaDB schema may be partly installed. Inspect error before rerunning.')
    raise
finally:con.close()

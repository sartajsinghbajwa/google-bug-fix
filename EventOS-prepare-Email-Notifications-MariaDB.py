"""One-time additive WHC MariaDB setup for the EventOS email outbox + preferences.
Run AFTER extracting the all-in-one ZIP; does not contact SMTP or send mail.
"""
import os
from production_mariadb import connect
from email_notifications import SCHEMA
if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':
    raise SystemExit('STOP: Use only with the EventOS staging MariaDB database.')
con=connect()
try:
    existing={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    if not {'users','workspaces','account_profiles','growth_notifications'}.issubset(existing):
        raise SystemExit('STOP: Run earlier EventOS Account + Growth migrations first.')
    for statement in SCHEMA: con.execute(statement)
    con.commit()
    after={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
    if not {'email_notification_outbox','email_notification_preferences'}.issubset(after):
        raise RuntimeError('Required email tables are missing')
    print('SUCCESS: EventOS email queue and preference tables available; no previous data changed.')
    print('No email was sent. Delivery remains disabled until SMTP and EVENTOS_EMAIL_NOTIFICATIONS_ENABLED=1 are configured.')
except Exception:
    con.rollback()
    print('STOP: DDL may have partially completed. Inspect error before rerunning.')
    raise
finally:
    con.close()

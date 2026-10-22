"""WHC cPanel > Setup Python App > Execute Python Script: additive schema prep.

Run after backing up the MariaDB database and uploading the release, BEFORE
restarting EventOS. Never prints your database URL or credentials.
"""
import os
from production_mariadb import connect
from account_support import schema_statements

if os.getenv('EVENTOS_DATABASE_ENGINE', '').lower() != 'mariadb':
    raise SystemExit('STOP: staging must have EVENTOS_DATABASE_ENGINE=mariadb')

con=connect()
try:
    names={r['name'] for r in con.execute("SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()")}
    if not {'users','workspaces','saas_billing_accounts'}.issubset(names):
        raise SystemExit('STOP: not an initialized EventOS MariaDB database')
    print('PASS: Existing EventOS application tables verified.')
    for statement in schema_statements():
        con.execute(statement)
    con.commit()
    verified={r['name'] for r in con.execute("SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()")}
    new={'account_profiles','account_email_changes','support_tickets','support_ticket_messages'}
    if not new.issubset(verified):
        raise SystemExit('STOP: support tables are incomplete; investigate before restarting')
    print('SUCCESS: Four additive account/support tables are present.')
    print('PASS: No existing EventOS tables or customer rows were deleted or reset.')
    print('NEXT: Restart staging EventOS and test My Account + Support Center.')
finally:
    con.close()

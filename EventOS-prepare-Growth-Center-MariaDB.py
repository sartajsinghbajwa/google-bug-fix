"""WHC cPanel additive migration: run once on staging before app restart."""
import os
from production_mariadb import connect
from growth_center import TABLES,INDEXES
if os.getenv('EVENTOS_DATABASE_ENGINE','').lower()!='mariadb':raise SystemExit('STOP: MariaDB staging only')
con=connect()
try:
 existing={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
 if not {'users','workspaces','market_listings','market_quotes'}.issubset(existing):raise SystemExit('STOP: Deploy prior Party Hub migration first.')
 columns={r['name'] for r in con.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='market_listings'")}
 if 'video_url' not in columns:
  con.execute("ALTER TABLE market_listings ADD COLUMN video_url VARCHAR(400) NOT NULL DEFAULT ''")
  print('PASS: Video link column added')
 for name,sql in TABLES.items():con.execute(sql)
 for sql in INDEXES:con.execute(sql)
 con.commit()
 after={r['name'] for r in con.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=DATABASE()')}
 if set(TABLES)-after:raise RuntimeError('Growth Center tables missing')
 print('SUCCESS: Growth Center tables available:',len(TABLES),'and vendor video link ready.')
 print('Existing account, venue, quote, review, event and support data unchanged.')
except Exception:
 con.rollback();print('STOP: Partial DDL may exist. Inspect error before rerunning.');raise
finally:con.close()

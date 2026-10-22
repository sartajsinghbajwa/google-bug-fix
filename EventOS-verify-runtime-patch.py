#!/usr/bin/env python3
"""Read-only EventOS MariaDB runtime patch & schema verification (staging only)."""
from pathlib import Path
from urllib.parse import urlsplit,unquote
import os,hashlib,re,sys

BASE=Path(__file__).resolve().parent
HASHES={'production_mariadb.py': 'efdb306883c99f46078d165f38ee90a74cd87d07ef57a6e7197021af78d99496', 'saas_access.py': '3fc13623a48aeb35454dd5754fbdb78d2463fcc23823427fe995b057485cb8af', 'platform_studio.py': 'efd5869843380e90bb8896ce97366f470117429f1d0ee8bdf1ec3f6c52c7a85d'}
EXPECTED_TABLES=74
EXPECTED_FKS=85
EXPECTED_INDEXES=39

def stop(message):
    print('STOP: '+message,flush=True)
    return 1

def main():
    print('EventOS MariaDB runtime patch preflight (READ ONLY)',flush=True)
    if os.environ.get('EVENTOS_DATABASE_ENGINE','sqlite').lower()!='sqlite':
        return stop('Keep staging application on SQLite during preflight')
    for name,digest in HASHES.items():
        target=BASE/name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            return stop('Patch file missing or different: '+name)
    print('PASS: All three runtime files match the tested patch',flush=True)
    from production_mariadb import connect,translate,Connection
    for statement in (
        'SELECT `key`,value FROM platform_site_settings',
        'INSERT INTO platform_site_settings(`key`,value,updated_at) VALUES(?,?,?) ON CONFLICT(`key`) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
        "UPDATE saas_settings SET value=? WHERE `key`='require_access_code'",
    ):
        translated,_=translate(statement)
        if 'ON CONFLICT' in translated or '?' in translated or not translated:
            return stop('SQL translator preflight failed')
    if not hasattr(Connection,'in_transaction'):
        return stop('Missing agency transaction compatibility property')
    print('PASS: Settings SQL translation and connection interface verified',flush=True)
    try:
        url=urlsplit(os.environ.get('EVENTOS_DATABASE_URL',''))
        if url.scheme not in ('mariadb','mysql') or unquote(url.path.lstrip('/')).endswith('_eventos_fresh') is False:
            return stop('Expected dedicated staging _eventos_fresh database')
        con=connect()
        try:
            with con.raw.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'")
                tables=cursor.fetchone()['n']
                cursor.execute("SELECT COUNT(*) AS n FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL")
                fks=cursor.fetchone()['n']
                cursor.execute("SELECT COUNT(DISTINCT index_name) AS n FROM information_schema.statistics WHERE TABLE_SCHEMA=DATABASE() AND index_name LIKE 'idx_%'")
                named_indexes=cursor.fetchone()['n']
                cursor.execute("SELECT COUNT(*) AS n FROM users")
                users=cursor.fetchone()['n']
                cursor.execute("SELECT COUNT(*) AS n FROM events")
                events=cursor.fetchone()['n']
                cursor.execute("SELECT COUNT(*) AS n FROM workspaces")
                spaces=cursor.fetchone()['n']
        finally: con.close()
    except Exception as exc:
        code=exc.args[0] if getattr(exc,'args',None) and isinstance(exc.args[0],int) else 'unknown'
        return stop('MariaDB verification failed (error code: '+str(code)+')')
    print('Schema: %s tables, %s foreign key columns, %s named application indexes'%(tables,fks,named_indexes),flush=True)
    print('Records: %s users, %s events, %s workspaces'%(users,events,spaces),flush=True)
    if tables!=EXPECTED_TABLES or fks!=EXPECTED_FKS or named_indexes<EXPECTED_INDEXES:
        return stop('Database schema does not match expected initialization')
    if any((users,events,spaces)):
        return stop('Fresh test database is no longer empty of primary records')
    print('PASS: Schema and fresh database checks. NO DATA CHANGED.',flush=True)
    print('NEXT: Keep SQLite engine until controlled staging cutover has been tested.',flush=True)
    return 0

if __name__=='__main__':
    sys.exit(main())

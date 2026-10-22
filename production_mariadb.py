"""MariaDB compatibility adapter for the legacy EventOS SQLite SQL surface.

Use only with an independently migrated database; migration is never implicit.
This adapter supports the SQL patterns in EventOS, but requires staging QA against
WHC's actual MariaDB before processing customer payments.
"""
from __future__ import annotations
import os
import re
from urllib.parse import urlsplit, unquote


def enabled():
    return os.environ.get('EVENTOS_DATABASE_ENGINE','sqlite').lower() == 'mariadb'


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)


class Cursor:
    def __init__(self,cur):
        self.cur=cur
        self.rowcount=cur.rowcount
        self.lastrowid=cur.lastrowid
    def fetchone(self):
        row=self.cur.fetchone()
        return Row(row) if row is not None else None
    def fetchall(self):
        return [Row(x) for x in self.cur.fetchall()]
    def __iter__(self):
        for r in self.cur:
            yield Row(r)


def placeholders(sql):
    """Replace DBAPI question-mark placeholders, ignoring SQL literals."""
    out=[];quote=None;i=0
    while i<len(sql):
        ch=sql[i]
        if quote:
            out.append(ch)
            if ch==quote:
                if i+1<len(sql) and sql[i+1]==quote:
                    out.append(sql[i+1]);i+=1
                else:quote=None
        elif ch in "'\"`":quote=ch;out.append(ch)
        elif ch=='?':out.append('%s')
        else:out.append(ch)
        i+=1
    return ''.join(out)


def translate(sql):
    s=sql.strip()
    info=re.fullmatch(r'PRAGMA\s+table_info\(\s*([A-Za-z0-9_]+)\s*\)\s*;?',s,re.I)
    if info:
        return ('SELECT ordinal_position-1 AS cid,column_name AS name, data_type AS type, '
                'CASE WHEN is_nullable=\'NO\' THEN 1 ELSE 0 END AS `notnull`, '
                'column_default AS dflt_value, CASE WHEN column_key=\'PRI\' THEN 1 ELSE 0 END AS pk '
                'FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s '
                'ORDER BY ordinal_position'),(info.group(1),)
    if re.fullmatch(r'PRAGMA\s+(foreign_keys|busy_timeout)\s*=.*',s,re.I):
        return 'SELECT 1',()
    if re.fullmatch(r'BEGIN\s+IMMEDIATE;?',s,re.I):
        return "SELECT GET_LOCK('eventos_writer', 10) AS acquired",()
    if re.search(r'\b(ATTACH|DETACH|PRAGMA|rowid)\b',s,re.I):
        # checkin history uses SQLite rowid as a tie breaker; use timestamp only
        s=re.sub(r',\s*h\.rowid\s+DESC','',s,flags=re.I)
        if re.search(r'\b(ATTACH|DETACH|PRAGMA|rowid)\b',s,re.I):
            raise RuntimeError('Unsupported SQLite SQL: '+s[:120])
    s=re.sub(r'\s+COLLATE\s+NOCASE','',s,flags=re.I)
    s=s.replace("X''","''") # SQLite empty binary literal
    s=re.sub(r'json_extract\(\s*([\w.]+)\s*,\s*(\'\$\.[\w]+\')\s*\)',r'JSON_UNQUOTE(JSON_EXTRACT(\1,\2))',s,flags=re.I)
    if 'json_each(' in s.lower():
        # Legacy guest experience uses SQLite JSON1 table-valued function.
        # Refuse rather than silently give incorrect guest matching.
        raise RuntimeError('JSON array search needs MariaDB-specific rewrite')
    s=re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b','INSERT IGNORE INTO',s,flags=re.I)
    # SQLite UPSERT -> MariaDB ON DUPLICATE KEY UPDATE, including excluded.alias.
    s=re.sub(r'ON\s+CONFLICT\s*\([^)]*\)\s+DO\s+UPDATE\s+SET',
             'ON DUPLICATE KEY UPDATE',s,flags=re.I)
    s=re.sub(r'\bexcluded\.([A-Za-z_][\w]*)',r'VALUES(\1)',s,flags=re.I)
    if re.search(r'\bON\s+CONFLICT\s+DO\s+NOTHING',s,re.I):
        s=re.sub(r'\bINSERT\s+INTO\b','INSERT IGNORE INTO',s,count=1,flags=re.I)
        s=re.sub(r'\s*ON\s+CONFLICT\s+DO\s+NOTHING','',s,flags=re.I)
    s=re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b','BIGINT AUTO_INCREMENT PRIMARY KEY',s,flags=re.I)
    if s.upper().startswith('CREATE TABLE'):
        # TEXT primary/unique fields need bounded indexable types in MariaDB.
        s=re.sub(r'(\b[A-Za-z_][\w]*\s+)TEXT(\s+(?:NOT\s+NULL\s+)?(?:PRIMARY\s+KEY|UNIQUE)\b)',r'\1VARCHAR(191)\2',s,flags=re.I)
        # NOTE: normal TEXT columns in UNIQUE(a,b) must be converted by the
        # migration generator using SQLite index metadata, not inferred here.
    if re.search(r'CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_floor_unique_base',s,re.I):
        s='CREATE UNIQUE INDEX IF NOT EXISTS idx_floor_unique_base ON event_floor_elements(event_id,base_unique_kind)'
    # MariaDB CREATE INDEX IF NOT EXISTS syntax is supported on current WHC MariaDB.
    return placeholders(s),()


class Connection:
    def __init__(self, raw):
        self.raw=raw
        self._in_transaction=False

    @property
    def in_transaction(self):
        return self._in_transaction

    def execute(self,sql,params=()):
        code,supplied=translate(sql)
        cur=self.raw.cursor()
        cur.execute(code,supplied if supplied else (params or ()))
        if 'GET_LOCK(' in code and cur.fetchone()['acquired'] != 1:
            raise RuntimeError('Database writer lock timeout')
        if 'GET_LOCK(' in code or not re.match(r'^\s*(SELECT|SHOW|EXPLAIN|DESCRIBE)\b',code,re.I):
            self._in_transaction=True
        return Cursor(cur)
    def executemany(self,sql,values):
        code,supplied=translate(sql)
        if supplied:raise RuntimeError('Unsupported executemany SQL')
        cur=self.raw.cursor();cur.executemany(code,values);self._in_transaction=True;return Cursor(cur)
    def executescript(self,sql):
        from production_database import split_sql
        result=None
        for stmt in split_sql(sql):
            if stmt.strip():result=self.execute(stmt)
        return result
    def commit(self):
        self.raw.commit()
        self._in_transaction=False
    def rollback(self):
        self.raw.rollback()
        self._in_transaction=False
    def close(self):
        try:
            with self.raw.cursor() as cur:cur.execute("SELECT RELEASE_LOCK('eventos_writer')")
        except Exception:pass
        self.raw.close()


def connect():
    import pymysql
    url=os.getenv('EVENTOS_DATABASE_URL','')
    p=urlsplit(url)
    if p.scheme not in ('mariadb','mysql') or not p.hostname or not p.username or not p.path.strip('/'):
        raise RuntimeError('Set EVENTOS_DATABASE_URL=mariadb://DB_USER:DB_PASS@localhost/DB_NAME')
    if p.hostname not in ('localhost','127.0.0.1') and os.getenv('EVENTOS_MARIADB_REMOTE_TLS')!='1':
        raise RuntimeError('Remote MariaDB requires explicitly configured TLS')
    kwargs={}
    if p.hostname not in ('localhost','127.0.0.1'):
        import ssl
        kwargs['ssl']=ssl.create_default_context()
    raw=pymysql.connect(host=p.hostname,port=p.port or 3306,user=unquote(p.username),
        password=unquote(p.password or ''),database=unquote(p.path.lstrip('/')),
        charset='utf8mb4',autocommit=False,connect_timeout=8,
        cursorclass=pymysql.cursors.DictCursor,**kwargs)
    return Connection(raw)

"""PostgreSQL compatibility boundary for EventOS's existing SQLite-first modules.

This is an opt-in migration bridge, NOT a transparent proof that all production
data paths work on PostgreSQL. Only set EVENTOS_DATABASE_ENGINE=postgres after
staging tests and a verified data migration. Unsupported SQL fails visibly.
"""
from __future__ import annotations
import os
import re
from urllib.parse import urlsplit
from collections.abc import Mapping



def split_sql(sql):
    """Split SQL scripts respecting quoted literals, without runtime dependencies."""
    import sqlite3
    pending=''
    for line in sql.splitlines(keepends=True):
        pending+=line
        if sqlite3.complete_statement(pending):
            if pending.strip():yield pending
            pending=''
    if pending.strip():
        if not sqlite3.complete_statement(pending+';'):
            raise ValueError('Incomplete SQL script')
        yield pending

class HybridRow(dict):
    """SQLite-style mapping with both row['name'] and row[0] support."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def postgres_enabled():
    return os.environ.get("EVENTOS_DATABASE_ENGINE", "sqlite").lower() == "postgres"


def _replace_params(sql):
    """Rewrite ? placeholders without rewriting characters inside SQL strings."""
    out, quote, i = [], None, 0
    while i < len(sql):
        c = sql[i]
        if quote:
            out.append(c)
            if c == quote:
                if i+1 < len(sql) and sql[i+1] == quote:
                    out.append(sql[i+1]); i += 1
                else: quote = None
        elif c in ("'", '"'):
            quote = c; out.append(c)
        elif c == "?":
            out.append("%s")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _dialect(sql, args):
    sql = sql.strip()
    original = sql.upper()
    if not sql:
        return "SELECT 1 WHERE FALSE", ()
    match = re.fullmatch(r"PRAGMA\s+table_info\(\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*\)\s*;?", sql, re.I)
    if match:
        return ("""SELECT ordinal_position-1 AS cid, column_name AS name,
                    data_type AS type, CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull,
                    column_default AS dflt_value, 0 AS pk
                   FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name=%s
                   ORDER BY ordinal_position""", (match.group(1),))
    if re.fullmatch(r"PRAGMA\s+(foreign_keys|busy_timeout)\s*=.*", sql, re.I):
        return "SELECT 1", ()
    if re.fullmatch(r"BEGIN\s+IMMEDIATE\s*;?", sql, re.I):
        # Preserve SQLite writer serialization, conservatively, until per-tenant
        # locking is formally tested. Advisory transaction lock is released at commit.
        return "SELECT pg_advisory_xact_lock(881774721)", ()
    if re.search(r"\bPRAGMA\b|\bATTACH\b|\bDETACH\b", sql, re.I):
        raise RuntimeError("SQLite-only SQL is not supported on PostgreSQL: "+sql[:80])
    sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.I)
    sql = re.sub(r"(?<![a-zA-Z0-9_])X''", "''::bytea", sql, flags=re.I)
    # Legacy SQLite JSON1 numeric RSVP extraction. Cast only the known
    # numeric party-size counters, keeping textual JSON keys as text.
    def json_value(match):
        column,key=match.group(1),match.group(2)
        expression="("+column+"::jsonb ->> '"+key+"')"
        return "("+expression+")::integer" if key in ("total","adults","kids","children","plus_one") else expression
    sql = re.sub(r"json_extract\(\s*([A-Za-z_][\w.]*)\s*,\s*'\$\.([A-Za-z_][\w]*)'\s*\)",
                 json_value,sql,flags=re.I)
    sql = re.sub(r"\s+COLLATE\s+NOCASE", "", sql, flags=re.I)
    sql = re.sub(r"\b([a-z_][a-z0-9_]*\.)rowid\b", r"\1ctid", sql, flags=re.I)
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.I)
    if re.search(r"\bINSERT\s+OR\s+REPLACE\b|\bREPLACE\s+INTO\b", sql, re.I):
        raise RuntimeError("SQLite REPLACE is unsupported: "+sql[:80])
    if re.search(r"\browid\b|\bAUTOINCREMENT\b", sql, re.I):
        raise RuntimeError("SQLite-only rowid/identity SQL is not supported: "+sql[:80])
    if original.startswith("INSERT OR IGNORE") and "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip(";") + " ON CONFLICT DO NOTHING"
    # SQLite permits double-quoted strings, but PostgreSQL correctly reserves
    # them for identifiers. Known legacy scalar string-literal queries only.
    sql = re.sub(r"(?i)(\b(?:type|status|kind|role|rsvp)\s*=\s*)\"([a-z_]+)\"",r"\1'\2'",sql)
    return _replace_params(sql), args


class Result:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None

    def fetchone(self):
        r = self.cursor.fetchone()
        return HybridRow(r) if r is not None else None

    def fetchall(self):
        return [HybridRow(r) for r in self.cursor.fetchall()]

    def __iter__(self):
        for r in self.cursor:
            yield HybridRow(r)


class Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        from psycopg.rows import dict_row
        translated, args = _dialect(sql, params or ())
        cur = self.raw.cursor(row_factory=dict_row)
        cur.execute(translated, args)
        return Result(cur)

    def executemany(self, sql, values):
        from psycopg.rows import dict_row
        translated, _ = _dialect(sql, ())
        cur = self.raw.cursor(row_factory=dict_row)
        cur.executemany(translated, values)
        return Result(cur)

    def executescript(self, sql):
        # sqlite3.complete_statement keeps statements together across lines.
        result = None
        for statement in split_sql(sql):
            if statement.strip():
                result = self.execute(statement)
        return result

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def connect():
    import psycopg
    url = os.environ.get("EVENTOS_DATABASE_URL", "")
    if not url or not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("PostgreSQL requires EVENTOS_DATABASE_URL")
    if "sslmode=disable" in url:
        raise RuntimeError("Disabling PostgreSQL TLS is not allowed")
    local_ci = (os.getenv("CI")=="true" and
                os.getenv("EVENTOS_POSTGRES_LOCAL_TEST_ONLY")=="1" and
                urlsplit(url).hostname in ("localhost","127.0.0.1"))
    raw = psycopg.connect(url, connect_timeout=8, sslmode="prefer" if local_ci else "require")
    return Connection(raw)

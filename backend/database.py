"""Short SQLite transactions run on dedicated threads; no network I/O under locks."""
import asyncio
import json
import os
import sqlite3
from pathlib import Path

DATA = Path(os.getenv('MOSS_DATA_DIR', str(Path(__file__).parent / 'data')))

def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA / 'moss.sqlite3', timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

def initialize():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_profiles(user_id TEXT PRIMARY KEY REFERENCES users(id), nickname TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS token_sessions(token_hash TEXT PRIMARY KEY REFERENCES tokens(hash) ON DELETE CASCADE, session_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY, remaining INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS account_referrals(
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          invite_hash TEXT UNIQUE NOT NULL REFERENCES invites(hash) ON DELETE CASCADE,
          nonce TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS usage_events(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS usage_window ON usage_events(user_id,kind,at);
        CREATE INDEX IF NOT EXISTS usage_expiry ON usage_events(at);
        CREATE TABLE IF NOT EXISTS account_deletions(user_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS referral_owner_deleting AFTER INSERT ON account_deletions BEGIN
          DELETE FROM invites WHERE hash IN (SELECT invite_hash FROM account_referrals WHERE user_id=NEW.user_id);
        END;
        CREATE TRIGGER IF NOT EXISTS referral_owner_removed AFTER DELETE ON account_referrals BEGIN
          DELETE FROM invites WHERE hash=OLD.invite_hash;
        END;
        CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), fingerprint TEXT NOT NULL,
          snapshot TEXT, raw_input TEXT, deleted INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0,
          record_date TEXT NOT NULL, sent_epoch REAL NOT NULL, revision INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS single_pending ON records(user_id) WHERE complete=0 AND deleted=0;
        CREATE INDEX IF NOT EXISTS record_order ON records(user_id,record_date DESC,sent_epoch DESC,id DESC);
        CREATE TABLE IF NOT EXISTS tags(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS record_tags(record_id TEXT REFERENCES records(id), tag_id TEXT REFERENCES tags(id),
          source TEXT NOT NULL CHECK(source IN ('direct','derived')), PRIMARY KEY(record_id,tag_id,source));
        CREATE TABLE IF NOT EXISTS candidates(record_id TEXT REFERENCES records(id), step TEXT, value TEXT NOT NULL, PRIMARY KEY(record_id,step));
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), fingerprint TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id), data TEXT NOT NULL, frozen TEXT, path TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user_id TEXT, record_id TEXT, kind TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS search_jobs(record_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, error TEXT);
        CREATE TRIGGER IF NOT EXISTS search_record_insert AFTER INSERT ON records BEGIN
          INSERT OR REPLACE INTO search_jobs(record_id,revision) VALUES(NEW.id,NEW.revision);
        END;
        CREATE TRIGGER IF NOT EXISTS search_record_update AFTER UPDATE ON records BEGIN
          INSERT OR REPLACE INTO search_jobs(record_id,revision) VALUES(NEW.id,NEW.revision);
        END;
        ''')

async def tx(fn):
    def run():
        c = connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            result = fn(c)
            c.commit()
            return result
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    return await asyncio.to_thread(run)

def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)

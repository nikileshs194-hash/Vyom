"""
GigPilot - database layer (SQLite).

Everything that belongs to one person lives here, keyed by user id:
accounts, login sessions, shifts, every rupee earned, every recommendation
accepted or ignored, and the location trail. The file is created on first
run; set GIGPILOT_DB to put it somewhere else.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from pathlib import Path

DB_PATH = os.environ.get("GIGPILOT_DB", str(Path(__file__).parent / "gigpilot.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    target_earnings REAL NOT NULL,
    available_hours REAL NOT NULL,
    vehicle TEXT NOT NULL,
    base_earned REAL NOT NULL,
    base_hours REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS earnings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    shift_id INTEGER REFERENCES shifts(id),
    ts TEXT NOT NULL,
    amount REAL NOT NULL,
    zone_id TEXT,
    kind TEXT NOT NULL,
    note TEXT,
    platform TEXT,
    merchant TEXT,
    distance_km REAL
);
CREATE INDEX IF NOT EXISTS earnings_user_ts ON earnings(user_id, ts);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    ts TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    zone_id TEXT
);
CREATE INDEX IF NOT EXISTS locations_user_ts ON locations(user_id, ts);
"""

_lock = threading.RLock()
_conn = None


def init(path=None):
    """Open (or create) the database. Tests pass ':memory:'."""
    global _conn
    with _lock:
        if _conn:
            _conn.close()
        _conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        _add_missing_columns()
        _conn.commit()


def _add_missing_columns():
    """Bring a database created by an earlier version up to date."""
    wanted = {
        "earnings": {"platform": "TEXT", "merchant": "TEXT", "distance_km": "REAL"},
        "users": {"synced": "INTEGER NOT NULL DEFAULT 0"},
    }
    for table, columns in wanted.items():
        have = {row["name"] for row in _conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in have:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _run(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def _all(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def _one(sql, params=()):
    rows = _all(sql, params)
    return rows[0] if rows else None


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- accounts


def _hash_password(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1
    ).hex()


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(username, name, password, when):
    """Returns the new user, or None if the username is taken."""
    salt = secrets.token_hex(16)
    try:
        cur = _run(
            "INSERT INTO users (username, name, password_hash, salt, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username.lower(), name, _hash_password(password, salt), salt, stamp(when)),
        )
    except sqlite3.IntegrityError:
        return None
    return {"id": cur.lastrowid, "username": username.lower(), "name": name}


def verify_user(username, password):
    row = _one("SELECT * FROM users WHERE username = ?", (username.lower(),))
    # hash even when the user is unknown so both cases take the same time
    salt = row["salt"] if row else "00" * 16
    candidate = _hash_password(password, salt)
    if row and hmac.compare_digest(candidate, row["password_hash"]):
        return {"id": row["id"], "username": row["username"], "name": row["name"]}
    return None


def create_session(user_id, when):
    token = secrets.token_urlsafe(32)
    _run(
        "INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
        (_hash_token(token), user_id, stamp(when)),
    )
    return token


def user_for_token(token):
    return _one(
        "SELECT u.id, u.username, u.name FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ?",
        (_hash_token(token),),
    )


def delete_session(token):
    _run("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


# ------------------------------------------------------------------ shifts


def start_shift(user_id, goal, when):
    end_shift(user_id, when)
    cur = _run(
        "INSERT INTO shifts (user_id, started_at, target_earnings, available_hours, vehicle, "
        "base_earned, base_hours) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, stamp(when), goal["target_earnings"], goal["available_hours"],
         goal["vehicle"], goal["earned_so_far"], goal["hours_elapsed"]),
    )  # fmt: skip
    return cur.lastrowid


def end_shift(user_id, when):
    _run(
        "UPDATE shifts SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
        (stamp(when), user_id),
    )


def active_shifts():
    return _all("SELECT * FROM shifts WHERE ended_at IS NULL")


def shift_totals(shift_id):
    row = _one(
        "SELECT COALESCE(SUM(amount), 0) AS earned, "
        "COALESCE(SUM(kind = 'order'), 0) AS orders FROM earnings WHERE shift_id = ?",
        (shift_id,),
    )
    return row["earned"], row["orders"]


# ---------------------------------------------------------------- earnings


def add_earning(user_id, shift_id, when, amount, zone_id, kind, note=None,
                platform=None, merchant=None, distance_km=None):  # fmt: skip
    _run(
        "INSERT INTO earnings (user_id, shift_id, ts, amount, zone_id, kind, note, platform, "
        "merchant, distance_km) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, shift_id, stamp(when), amount, zone_id, kind, note, platform, merchant,
         distance_km),
    )  # fmt: skip


def is_synced(user_id):
    row = _one("SELECT synced FROM users WHERE id = ?", (user_id,))
    return bool(row and row["synced"])


def add_synced_orders(user_id, orders):
    """Store a rider's order history from the partner apps, once."""
    with _lock:
        _conn.executemany(
            "INSERT INTO earnings (user_id, shift_id, ts, amount, zone_id, kind, platform, "
            "merchant, distance_km) VALUES (?, NULL, ?, ?, ?, 'order', ?, ?, ?)",
            [
                (user_id, stamp(o["ts"]), o["amount"], o["zone_id"], o["platform"],
                 o["merchant"], o["distance_km"])
                for o in orders
            ],
        )  # fmt: skip
        _conn.execute("UPDATE users SET synced = 1 WHERE id = ?", (user_id,))
        _conn.commit()


def earnings_by_platform(user_id, since):
    return _all(
        "SELECT platform, SUM(amount) AS amount, SUM(kind = 'order') AS orders FROM earnings "
        "WHERE user_id = ? AND ts >= ? AND platform IS NOT NULL GROUP BY platform "
        "ORDER BY amount DESC",
        (user_id, stamp(since)),
    )


def order_count(user_id):
    return _one(
        "SELECT COUNT(*) AS n FROM earnings WHERE user_id = ? AND kind = 'order'", (user_id,)
    )["n"]


def earnings_by_day(user_id, since):
    return _all(
        "SELECT substr(ts, 1, 10) AS day, SUM(amount) AS amount, COUNT(*) AS entries "
        "FROM earnings WHERE user_id = ? AND ts >= ? GROUP BY day",
        (user_id, stamp(since)),
    )


def earnings_by_day_hour(user_id, since):
    return _all(
        "SELECT substr(ts, 1, 10) AS day, CAST(substr(ts, 12, 2) AS INTEGER) AS hour, "
        "SUM(amount) AS amount, COUNT(*) AS entries FROM earnings "
        "WHERE user_id = ? AND ts >= ? GROUP BY day, hour",
        (user_id, stamp(since)),
    )


def earnings_by_zone(user_id, since):
    return _all(
        "SELECT zone_id, SUM(amount) AS amount, COUNT(*) AS entries FROM earnings "
        "WHERE user_id = ? AND ts >= ? AND zone_id IS NOT NULL GROUP BY zone_id "
        "ORDER BY amount DESC",
        (user_id, stamp(since)),
    )


def recent_earnings(user_id, limit=15):
    return _all(
        "SELECT ts, amount, zone_id, kind, note, platform, merchant, distance_km "
        "FROM earnings WHERE user_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
        (user_id, limit),
    )


# ------------------------------------------------- decisions and locations


def add_decision(user_id, when, action, outcome, confidence):
    _run(
        "INSERT INTO decisions (user_id, ts, action, outcome, confidence) VALUES (?, ?, ?, ?, ?)",
        (user_id, stamp(when), action, outcome, confidence),
    )


def decisions(user_id, limit=50):
    return _all(
        "SELECT ts, action, outcome, confidence FROM decisions WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


def add_location(user_id, when, lat, lon, zone_id):
    _run(
        "INSERT INTO locations (user_id, ts, lat, lon, zone_id) VALUES (?, ?, ?, ?, ?)",
        (user_id, stamp(when), lat, lon, zone_id),
    )


def last_location(user_id):
    return _one(
        "SELECT lat, lon, zone_id FROM locations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


def trail(user_id, since, limit=400):
    rows = _all(
        "SELECT lat, lon FROM locations WHERE user_id = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
        (user_id, stamp(since), limit),
    )
    return [[r["lat"], r["lon"]] for r in reversed(rows)]

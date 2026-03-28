import sqlite3
import os
import secrets
import time

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "copyserver.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            push_subscription TEXT,
            last_seen_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clips (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            content TEXT,
            filename TEXT,
            filepath TEXT,
            filesize INTEGER,
            mimetype TEXT,
            device_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            FOREIGN KEY (device_id) REFERENCES devices(id)
        );

        CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_clips_expires ON clips(expires_at);
    """)
    conn.close()


def gen_id(n=16):
    return secrets.token_urlsafe(n)


def gen_token():
    return secrets.token_urlsafe(32)


# --- Devices ---

def create_device(name):
    conn = get_db()
    device_id = gen_id()
    token = gen_token()
    now = int(time.time())
    conn.execute(
        "INSERT INTO devices (id, name, token, last_seen_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (device_id, name, token, now, now),
    )
    conn.commit()
    conn.close()
    return {"id": device_id, "name": name, "token": token}


def get_device_by_token(token):
    conn = get_db()
    row = conn.execute("SELECT * FROM devices WHERE token = ?", (token,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def touch_device(device_id):
    conn = get_db()
    conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (int(time.time()), device_id))
    conn.commit()
    conn.close()


def list_devices():
    conn = get_db()
    rows = conn.execute("SELECT id, name, last_seen_at, created_at FROM devices ORDER BY last_seen_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_device(device_id):
    conn = get_db()
    conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    conn.commit()
    conn.close()


def set_push_subscription(device_id, subscription_json):
    conn = get_db()
    conn.execute("UPDATE devices SET push_subscription = ? WHERE id = ?", (subscription_json, device_id))
    conn.commit()
    conn.close()


def get_push_subscriptions(exclude_device_id=None):
    conn = get_db()
    if exclude_device_id:
        rows = conn.execute(
            "SELECT id, push_subscription FROM devices WHERE push_subscription IS NOT NULL AND id != ?",
            (exclude_device_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, push_subscription FROM devices WHERE push_subscription IS NOT NULL"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Clips ---

def create_clip(clip_type, device_id, content=None, filename=None, filepath=None, filesize=None, mimetype=None, expire_days=None):
    conn = get_db()
    clip_id = gen_id()
    now = int(time.time())
    expires_at = int(now + expire_days * 86400) if expire_days else None
    conn.execute(
        "INSERT INTO clips (id, type, content, filename, filepath, filesize, mimetype, device_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (clip_id, clip_type, content, filename, filepath, filesize, mimetype, device_id, now, expires_at),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    return dict(row)


def list_clips(limit=50, before=None):
    conn = get_db()
    if before:
        rows = conn.execute(
            "SELECT c.*, d.name as device_name FROM clips c JOIN devices d ON c.device_id = d.id WHERE c.created_at < ? ORDER BY c.created_at DESC LIMIT ?",
            (before, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, d.name as device_name FROM clips c JOIN devices d ON c.device_id = d.id ORDER BY c.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_clip(clip_id):
    conn = get_db()
    row = conn.execute("SELECT filepath FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.commit()
    conn.close()
    if row and row["filepath"]:
        try:
            os.remove(row["filepath"])
        except OSError:
            pass


def cleanup_expired():
    conn = get_db()
    now = int(time.time())
    rows = conn.execute("SELECT filepath FROM clips WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)).fetchall()
    conn.execute("DELETE FROM clips WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
    conn.commit()
    conn.close()
    for row in rows:
        if row["filepath"]:
            try:
                os.remove(row["filepath"])
            except OSError:
                pass

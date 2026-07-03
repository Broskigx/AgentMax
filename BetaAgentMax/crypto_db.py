#!/usr/bin/env python3
"""
CryptoDB — SQLite cifrada con AES-256-GCM + HMAC-SHA256 por registro.
Requiere: pip install cryptography
"""

import hashlib
import hmac
import json
import os
import sqlite3
import time
from typing import Any, Optional

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

SCHEMA_VERSION = 1


def _derive_key(password: bytes, salt: bytes) -> bytes:
    if not _CRYPTO_OK:
        raise RuntimeError("cryptography no instalado: pip install cryptography")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return kdf.derive(password)


class CryptoDB:
    def __init__(self, db_path: str, password: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        # Derivar clave desde password
        salt_path = db_path + ".salt"
        if os.path.exists(salt_path):
            with open(salt_path, "rb") as f:
                salt = f.read()
        else:
            salt = os.urandom(16)
            with open(salt_path, "wb") as f:
                f.write(salt)

        if _CRYPTO_OK:
            self._key = _derive_key(password.encode(), salt)
            self._hmac_key = _derive_key((password + "_hmac").encode(), salt)
        else:
            self._key = self._hmac_key = None

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    # ── Schema ────────────────────────────────────────────────────────────────
    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS schema_info (
                version INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                device_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                encrypted BLOB NOT NULL,
                signature TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_session ON chat_messages(session_id, created_at);
            CREATE TABLE IF NOT EXISTS bugs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_type TEXT NOT NULL,
                severity TEXT DEFAULT 'medium',
                encrypted BLOB NOT NULL,
                signature TEXT NOT NULL,
                fixed INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                encrypted_value BLOB NOT NULL,
                confidence REAL DEFAULT 1.0,
                updated_at REAL NOT NULL
            );
        """)
        self.conn.commit()

        row = self.conn.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
        if not row:
            self.conn.execute("INSERT INTO schema_info VALUES (?,?)", (SCHEMA_VERSION, time.time()))
            self.conn.commit()

    # ── Cifrado / Descifrado ──────────────────────────────────────────────────
    def _encrypt(self, plaintext: str) -> bytes:
        if not _CRYPTO_OK or self._key is None:
            return plaintext.encode()
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, plaintext.encode(), None)
        return nonce + ct

    def _decrypt(self, data: bytes) -> str:
        if not _CRYPTO_OK or self._key is None:
            return data.decode()
        nonce, ct = data[:12], data[12:]
        return AESGCM(self._key).decrypt(nonce, ct, None).decode()

    def _sign(self, data: str) -> str:
        if self._hmac_key is None:
            return ""
        return hmac.new(self._hmac_key, data.encode(), hashlib.sha256).hexdigest()

    def _verify(self, data: str, sig: str) -> bool:
        if self._hmac_key is None:
            return True
        expected = self._sign(data)
        return hmac.compare_digest(expected, sig)

    # ── Chat ──────────────────────────────────────────────────────────────────
    def create_session(self, session_id: str, device_id: str = "") -> str:
        self.conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id, device_id, created_at) VALUES (?,?,?)",
            (session_id, device_id, time.time()),
        )
        self.conn.commit()
        return session_id

    def save_message(self, session_id: str, role: str, content: str) -> int:
        payload = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        encrypted = self._encrypt(payload)
        signature = self._sign(payload)
        cur = self.conn.execute(
            "INSERT INTO chat_messages (session_id, encrypted, signature, created_at) VALUES (?,?,?,?)",
            (session_id, encrypted, signature, time.time()),
        )
        self.conn.commit()
        # Update session updated_at
        self.conn.execute(
            "UPDATE chat_sessions SET updated_at=? WHERE id=?", (time.time(), session_id)
        )
        self.conn.commit()
        return cur.lastrowid

    def load_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, encrypted, signature FROM chat_messages "
            "WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        messages = []
        for row in reversed(rows):
            try:
                payload = self._decrypt(row["encrypted"])
                if not self._verify(payload, row["signature"]):
                    continue  # tampered, skip
                messages.append(json.loads(payload))
            except Exception:
                pass
        return messages

    def list_sessions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, device_id, created_at, updated_at, "
            "(SELECT COUNT(*) FROM chat_messages WHERE session_id=chat_sessions.id) as msg_count "
            "FROM chat_sessions ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Bugs ──────────────────────────────────────────────────────────────────
    def save_bug(self, bug_type: str, data: dict, severity: str = "medium") -> int:
        payload = json.dumps(data, ensure_ascii=False)
        encrypted = self._encrypt(payload)
        signature = self._sign(payload)
        cur = self.conn.execute(
            "INSERT INTO bugs (bug_type, severity, encrypted, signature, created_at) VALUES (?,?,?,?,?)",
            (bug_type, severity, encrypted, signature, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def load_bugs(self, only_unfixed: bool = True, limit: int = 500) -> list[dict]:
        q = "SELECT id, bug_type, severity, encrypted, signature, fixed, created_at FROM bugs"
        if only_unfixed:
            q += " WHERE fixed=0"
        q += " ORDER BY created_at DESC LIMIT ?"
        rows = self.conn.execute(q, (limit,)).fetchall()
        bugs = []
        for row in rows:
            try:
                payload = self._decrypt(row["encrypted"])
                data = json.loads(payload)
                data["_id"] = row["id"]
                data["_bug_type"] = row["bug_type"]
                data["_severity"] = row["severity"]
                bugs.append(data)
            except Exception:
                pass
        return bugs

    def mark_bug_fixed(self, bug_id: int):
        self.conn.execute("UPDATE bugs SET fixed=1 WHERE id=?", (bug_id,))
        self.conn.commit()

    def bug_count(self, only_unfixed: bool = True) -> int:
        q = "SELECT COUNT(*) FROM bugs"
        if only_unfixed:
            q += " WHERE fixed=0"
        return self.conn.execute(q).fetchone()[0]

    # ── Preferencias ─────────────────────────────────────────────────────────
    def set_pref(self, key: str, value: Any, confidence: float = 1.0):
        payload = json.dumps(value, ensure_ascii=False)
        encrypted = self._encrypt(payload)
        self.conn.execute(
            "INSERT OR REPLACE INTO preferences (key, encrypted_value, confidence, updated_at) VALUES (?,?,?,?)",
            (key, encrypted, confidence, time.time()),
        )
        self.conn.commit()

    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT encrypted_value FROM preferences WHERE key=?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(self._decrypt(row["encrypted_value"]))
        except Exception:
            return default

    # ── Stats ─────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        msg_count = self.conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        session_count = self.conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
        bug_count = self.bug_count()
        bug_total = self.conn.execute("SELECT COUNT(*) FROM bugs").fetchone()[0]
        return {
            "sessions": session_count,
            "messages": msg_count,
            "bugs_open": bug_count,
            "bugs_total": bug_total,
            "db_path": self.db_path,
            "encrypted": _CRYPTO_OK,
        }

    def close(self):
        self.conn.close()


# ── Singleton helpers ─────────────────────────────────────────────────────────
_db: Optional["CryptoDB"] = None


def get_db(db_path: str = "data/AgentMax.db", password: str = "AgentMax_default") -> "CryptoDB":
    global _db
    if _db is None:
        _db = CryptoDB(db_path, password)
    return _db


if __name__ == "__main__":
    import uuid

    db = get_db("data/test.db", "test_password_123")
    sid = db.create_session(str(uuid.uuid4()))
    db.save_message(sid, "user", "Hola AgentMax!")
    db.save_message(sid, "assistant", "Hola! En que puedo ayudarte?")
    db.save_bug(
        "training_error", {"stage": "dataloader", "error": "OOM", "vram": 8.2}, severity="high"
    )

    print("Stats:", json.dumps(db.stats(), indent=2))
    print("Messages:", db.load_messages(sid))
    print("Bugs:", len(db.load_bugs()))
    db.close()
    print("[OK] CryptoDB funcionando.")

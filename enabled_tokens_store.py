import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enabled_tokens.db")


def db_path() -> str:
    return DB_FILE


def _secure_db_file() -> None:
    # Owner read/write only. Best-effort: not all platforms support chmod semantics.
    try:
        os.chmod(DB_FILE, 0o600)
    except OSError:
        pass


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS enabled_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            currency TEXT NOT NULL,
            issuer TEXT NOT NULL,
            trust_limit TEXT NOT NULL,
            tx_hash TEXT,
            enabled_at TEXT NOT NULL,
            UNIQUE(username, currency, issuer)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_enabled_tokens_username ON enabled_tokens (username)"
    )
    conn.commit()
    conn.close()

    _secure_db_file()


def upsert_enabled_token(
    username: str,
    wallet_address: str,
    currency: str,
    issuer: str,
    trust_limit: str,
    tx_hash: Optional[str] = None,
) -> None:
    username_value = str(username or "").strip().lower()
    wallet_value = str(wallet_address or "").strip()
    currency_value = str(currency or "").strip().upper()
    issuer_value = str(issuer or "").strip()
    limit_value = str(trust_limit or "").strip() or "0"

    if not username_value or not wallet_value or not currency_value or not issuer_value:
        raise ValueError("Missing required enabled token fields")

    enabled_at = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO enabled_tokens (
            username,
            wallet_address,
            currency,
            issuer,
            trust_limit,
            tx_hash,
            enabled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username, currency, issuer) DO UPDATE SET
            wallet_address = excluded.wallet_address,
            trust_limit = excluded.trust_limit,
            tx_hash = COALESCE(excluded.tx_hash, enabled_tokens.tx_hash),
            enabled_at = excluded.enabled_at
        """,
        (
            username_value,
            wallet_value,
            currency_value,
            issuer_value,
            limit_value,
            tx_hash,
            enabled_at,
        ),
    )
    conn.commit()
    conn.close()


def get_enabled_tokens_by_username(username: str) -> List[Dict[str, str]]:
    username_value = str(username or "").strip().lower()
    if not username_value:
        return []

    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT currency, issuer, trust_limit, tx_hash, enabled_at
        FROM enabled_tokens
        WHERE username = ?
        ORDER BY enabled_at DESC
        """,
        (username_value,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

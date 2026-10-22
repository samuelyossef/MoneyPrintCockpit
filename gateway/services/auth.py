"""Admin credentials, sessions and login throttling."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from fastapi import FastAPI, HTTPException, Request, Response
from gateway.auth_crypto import admin_session_version, password_hash as password_hash, session_token_hash, verify_password

from gateway import main as m


def configured_admin() -> dict[str, str] | None:
    try:
        data = json.loads(m.ADMIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = {"name", "email", "salt", "hash"}
    return data if required.issubset(data) else None


def open_session() -> dict[str, str]:
    return {
        "token_hash": session_token_hash("cockpit-login-disabled"),
        "csrf_token": m._OPEN_SESSION_CSRF,
        "expires_at": (datetime.now(UTC) + timedelta(days=3650)).isoformat(),
    }


def session_from_request(request: Request) -> sqlite3.Row | dict[str, str] | None:
    if m.LOGIN_DISABLED:
        return m.open_session()
    token = request.cookies.get("cockpit_session")
    if not token:
        return None
    admin = m.configured_admin()
    if not admin:
        return None
    expected_version = admin_session_version(admin)
    with m.database() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (session_token_hash(token), m.now())
        ).fetchone()
        if row and not secrets.compare_digest(row["admin_version"], expected_version):
            db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
            return None
        if row:
            db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (m.now(), row["token_hash"]))
        return row


def login_is_limited() -> bool:
    cutoff = (datetime.now(UTC) - timedelta(minutes=m.LOGIN_WINDOW_MINUTES)).isoformat()
    with m.database() as db:
        failed = db.execute("SELECT count(*) AS count FROM login_attempts WHERE success=0 AND attempted_at>=?", (cutoff,)).fetchone()["count"]
    return failed >= m.LOGIN_ATTEMPTS

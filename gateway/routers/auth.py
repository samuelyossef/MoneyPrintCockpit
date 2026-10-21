"""Session login, logout and current-session routes."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator, Literal
from fastapi import APIRouter, HTTPException, Request, Response
from gateway.auth_crypto import admin_session_version, password_hash as password_hash, session_token_hash, verify_password

from gateway import main as m

router = APIRouter()


@router.post("/api/auth/login")
def login(input: m.LoginInput, response: Response) -> dict[str, Any]:
    if m.LOGIN_DISABLED:
        raise HTTPException(status_code=404, detail="Login desabilitado.")
    if m.login_is_limited():
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos.")
    admin = m.configured_admin()
    success = bool(admin and input.email.strip().lower() == str(admin["email"]).lower() and verify_password(input.password, admin))
    with m.database() as db:
        db.execute("INSERT INTO login_attempts(attempted_at, success) VALUES (?, ?)", (m.now(), int(success)))
        db.execute("DELETE FROM login_attempts WHERE attempted_at<?", ((datetime.now(UTC) - timedelta(days=1)).isoformat(),))
    if not success:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = datetime.now(UTC) + timedelta(hours=m.SESSION_HOURS)
    with m.database() as db:
        db.execute("INSERT INTO sessions(token_hash, csrf_token, admin_version, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (session_token_hash(token), csrf, admin_session_version(admin), m.now(), expires.isoformat(), m.now()))
    response.set_cookie("cockpit_session", token, max_age=m.SESSION_HOURS * 3600, httponly=True, samesite="lax", secure=False, path="/")
    return {"name": admin["name"], "email": admin["email"], "csrf_token": csrf, "expires_at": expires.isoformat()}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get("cockpit_session")
    if token:
        with m.database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (session_token_hash(token),))
    response.delete_cookie("cockpit_session", path="/")
    response.status_code = 204
    return response


@router.get("/api/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    if m.LOGIN_DISABLED:
        session = m.open_session()
        admin = m.configured_admin() or {}
        return {
            "name": admin.get("name") or "Local",
            "email": admin.get("email") or "sem login",
            "csrf_token": session["csrf_token"],
            "expires_at": session["expires_at"],
            "login_disabled": True,
        }
    session = m.session_from_request(request)
    admin = m.configured_admin()
    if not session or not admin:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    return {"name": admin["name"], "email": admin["email"], "csrf_token": session["csrf_token"], "expires_at": session["expires_at"]}

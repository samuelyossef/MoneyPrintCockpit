"""Project row helpers."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, AsyncIterator, Literal
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def row_as_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "niche": row["niche"], "language": row["language"],
        "prompt_base": row["prompt_base"], "defaults": json.loads(row["defaults_json"]),
        "publish": json.loads(row["publish_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def get_project_or_404(project_id: str) -> sqlite3.Row:
    with m.database() as db:
        row = db.execute("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return row


def get_or_create_project(name: str, language: str) -> dict[str, Any]:
    with m.database() as db:
        row = db.execute("SELECT * FROM projects WHERE name=? AND deleted_at IS NULL ORDER BY created_at LIMIT 1", (name,)).fetchone()
    if row:
        return m.row_as_project(row)
    return m.create_project(m.ProjectCreate(name=name, language=language, publish={"auto_publish": False, "platforms": [], "youtube_privacy": "private"}))

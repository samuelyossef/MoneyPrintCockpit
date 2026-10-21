"""Project CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter
import json
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4

from gateway import main as m

router = APIRouter()


@router.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    with m.database() as db:
        return [m.row_as_project(row) for row in db.execute("SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY updated_at DESC")]


@router.post("/api/projects", status_code=201)
def create_project(input: m.ProjectCreate) -> dict[str, Any]:
    project_id = str(uuid4())
    timestamp = m.now()
    with m.database() as db:
        db.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (project_id, input.name.strip(), input.niche.strip(), input.language, input.prompt_base.strip(), json.dumps(input.defaults), json.dumps(input.publish), timestamp, timestamp),
        )
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return m.row_as_project(row)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return m.row_as_project(m.get_project_or_404(project_id))


@router.patch("/api/projects/{project_id}")
def patch_project(project_id: str, input: m.ProjectPatch) -> dict[str, Any]:
    current = m.row_as_project(m.get_project_or_404(project_id))
    updated = {**current, **input.model_dump(exclude_none=True)}
    with m.database() as db:
        db.execute(
            "UPDATE projects SET name=?, niche=?, language=?, prompt_base=?, defaults_json=?, publish_json=?, updated_at=? WHERE id=?",
            (updated["name"].strip(), updated["niche"].strip(), updated["language"], updated["prompt_base"].strip(), json.dumps(updated["defaults"]), json.dumps(updated["publish"]), m.now(), project_id),
        )
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return m.row_as_project(row)


@router.delete("/api/projects/{project_id}", status_code=204, response_model=None)
def delete_project(project_id: str) -> None:
    m.get_project_or_404(project_id)
    with m.database() as db:
        db.execute("UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?", (m.now(), m.now(), project_id))

# Technical Documentation: Projects SaaS Module

This document outlines the technical design, database models, permission structures, backend logic, and integration flows for the Projects unit in Copyxyz AgentOps.

---

## 1. Tenant & Resource Hierarchy

All operational resources are constrained under a strict three-tier hierarchy:
```txt
Organization (organization_id)
  └── Workspace (workspace_id)
      └── Project (project_id)
          ├── Conversation
          ├── Knowledge (file)
          ├── Run
          ├── Artifact
          ├── Memory
          └── Audit Event
```

### Constraints & Security Boundaries:
1. **Multi-tenant isolation**: The system checks user tokens (JWT) to extract `workspace_id` and `organization_id`.
2. **Access validation**: Every operational request verifies that the requested `workspace_id` matches the user's token session workspace. Cross-workspace calls are immediately blocked with `HTTP 403 Forbidden`.
3. **Project boundaries**: Resources like knowledge, memory, or runs are scoped explicitly by `project_id` and `workspace_id`. No data leakage or crossover can occur between projects without explicit system elevation.

---

## 2. Database Models (SQLite)

New SQL tables have been migrated to support SaaS multitenancy.

### `projects`
Stores metadata and configurations for projects.
- `id` (TEXT PRIMARY KEY) - Unique identifier (slug derived from name).
- `organization_id` (TEXT NOT NULL) - Owning organization.
- `workspace_id` (TEXT NOT NULL) - Owning workspace.
- `name` (TEXT NOT NULL) - User-facing name.
- `description` (TEXT) - Brief description.
- `instructions` (TEXT) - Custom prompt instructions injected into executions.
- `default_model` (TEXT) - Primary model for chat/agent tasks (e.g. `lattice-1 · pro`).
- `status` (TEXT NOT NULL) - Project state: `active`, `archived`, `deleted` (soft-delete indicator).
- `created_by` (TEXT NOT NULL) - ID of the creating user.
- `created_at` (INTEGER) - Millisecond timestamp of creation.
- `updated_at` (INTEGER) - Millisecond timestamp of last update.

### `project_contexts`
Operational tools and scopes allowed for a project.
- `id` (TEXT PRIMARY KEY) - Context ID.
- `project_id` (TEXT UNIQUE) - Link to project.
- `allowed_tools` (TEXT) - JSON-serialized list of permitted tools (e.g. `["Bash", "Grep", "Read", "Web", "Query"]`).
- `knowledge_scope` (TEXT) - Scope constraint (default `project`).
- `memory_scope` (TEXT) - Scope constraint (default `project`).
- `settings_json` (TEXT) - JSON object for generic toggles (auto-archive rules, sharing).

### `project_memories`
Stores operational memories generated automatically upon run completions.
- `id` (TEXT PRIMARY KEY) - Memory ID.
- `workspace_id` (TEXT) - Owning workspace.
- `project_id` (TEXT) - Scoped project.
- `run_id` (TEXT) - Linked Run identifier.
- `memory_type` (TEXT) - Categorization: `decision`, `preference`, `technical_pattern`, `run_summary`, `lesson_learned`.
- `content` (TEXT) - Plain-text description of the memory.
- `metadata` (TEXT) - JSON-serialized metadata.
- `created_by` (TEXT) - User/Agent ID.
- `created_at` (INTEGER) - Timestamp.

### `runs`
Tracks executions spawned by Agents or Swarms.
- `id` (TEXT PRIMARY KEY) - Run ID.
- `workspace_id` (TEXT) - Owning workspace.
- `project_id` (TEXT) - Linked project.
- `conversation_id` (TEXT) - Linked chat window.
- `mode` (TEXT) - Execution type: `chat`, `agent`, `swarm`.
- `status` (TEXT) - Current lifecycle: `queued`, `running`, `waiting_approval`, `completed`, `failed`, `canceled`.
- `created_by` (TEXT) - Creator.
- `created_at` (INTEGER) - Created timestamp.
- `updated_at` (INTEGER) - Updated timestamp.

### `artifacts`
Files and code outputs produced during project runs.
- `id` (TEXT PRIMARY KEY) - Artifact ID.
- `workspace_id` (TEXT) - Owning workspace.
- `project_id` (TEXT) - Linked project.
- `conversation_id` (TEXT) - Spawning conversation.
- `run_id` (TEXT) - Spawning run.
- `name` (TEXT) - File name.
- `type` (TEXT) - Content format: `html`, `csv`, `markdown`, `code`.
- `content` (TEXT) - String representation of the artifact body.
- `version` (INTEGER) - Incremental version identifier.
- `created_by` (TEXT) - Creator.
- `created_at`/`updated_at` (INTEGER) - Timestamps.

### `audit_events`
Immutable record of all operational actions.
- `id` (TEXT PRIMARY KEY) - Event ID.
- `organization_id` (TEXT) - Scoped org.
- `workspace_id` (TEXT) - Scoped workspace.
- `project_id` (TEXT) - Scoped project.
- `event_type` (TEXT) - Audit event code (e.g. `project.created`, `run.completed`, `tool.used`).
- `actor` (TEXT) - Executing user or agent.
- `target_id` (TEXT) - Affected resource ID.
- `metadata_json` (TEXT) - Safe metadata (no secrets logged).
- `created_at` (INTEGER) - Timestamp.

---

## 3. Permissions & Role Mapping (RBAC)

RBAC controls access to projects and related resources:

| Role | Permissions |
|---|---|
| **owner** | `project.read`, `project.write`, `project.delete`, `knowledge.write`, `run.create`, `run.cancel`, `artifact.read`, `artifact.write`, `memory.write`, `audit.read`, `settings.manage` |
| **admin** | `project.read`, `project.write`, `knowledge.write`, `run.create`, `run.cancel`, `artifact.read`, `artifact.write`, `memory.write`, `audit.read`, `settings.manage` |
| **member** | `project.read`, `knowledge.write`, `run.create`, `run.cancel`, `artifact.read`, `artifact.write`, `memory.write`, `audit.read` |
| **viewer** | `project.read`, `artifact.read`, `audit.read` |

---

## 4. Run Summarization & Memory Generation

Upon completion of any chat/agent/swarm execution, a run log payload is generated containing:
- User prompt
- Tool execution logs (if applicable)
- Assistant response

To optimize response latency and prevent blocking FastAPI's main event loop during external network calls, this lifecycle completion is offloaded to a FastAPI `BackgroundTasks` worker thread (`complete_run_in_background`).

This background worker calls OpenRouter (`google/gemini-2.5-flash`) with the run log, extracting structured information in JSON format:
```json
{
  "summary": "operational summary",
  "decisions": ["engineering choices made"],
  "lessons": ["learnings/lessons observed"]
}
```
These values are saved in `project_memories` under types `run_summary`, `decision`, and `lesson_learned` respectively, linked directly to the run.


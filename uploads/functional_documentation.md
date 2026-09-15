# Functional Documentation: Projects User Guide

Welcome to the Projects Module for Copyxyz AgentOps. This guide explains how to use Projects as operational units, organize your work, execute runs, manage knowledge, and review project audit logs and memories.

---

## 1. Projects Dashboard

Navigate to the **Projects** tab in the sidebar (or press `⌘K` / `Ctrl+K` and search "Projects").

- **Create a Project**: Click **New Project** at the top right, enter a Name and Description, and click Create.
- **Projects Grid**: You will see all active projects in your workspace. Each card displays:
  - Project name and description.
  - Number of linked conversations.
  - Number of indexed files (knowledge sources).

---

## 2. Project Details Tabs

Opening any project reveals the details dashboard, consisting of 9 dedicated tabs:

### 1. Overview
A high-level dashboard summarizing project stats:
- Active chats, total runs, indexed knowledge sources, and generated artifacts.
- Metadata block detailing the default model, project ID, workspace scope, and owner.

### 2. Chats
Lists all active chats scoped to this project. 
- You can launch a **New Chat** inside this project, ensuring it automatically inherits all project instructions, knowledge sources, and permissions.

### 3. Knowledge
Allows uploading documents directly into the project's isolated context:
- Drag and drop or upload PDF, TXT, MD, DOCX, and CSV files (up to 25MB each).
- Re-sync files or delete them using the contextual action icons on each row.
- Search knowledge sources dynamically using the search bar (filtering content by relevance).

### 4. Runs
Tracks the lifecycle of executions spawned in this project:
- Each chat, agent, or swarm execution creates a **Run**.
- Displays the Run ID, Execution Mode (chat, agent, swarm), Status (queued, running, completed, failed), Actor, and Timestamp.

### 5. Artifacts
Aggregates all code, data files, and HTML outputs produced by runs in this project:
- Filter artifacts by Run ID or file type (HTML, CSV, Markdown, Code).
- Operations: **Preview** (renders HTML or Markdown inside a sandbox), **Copy** (places the content directly onto your clipboard), and **Download** (exports the file locally).

### 6. Memory
The project's operational memory, populated automatically on Run completion:
- Automatically logs decisions, preferences, technical patterns, run summaries, and lessons learned.
- Filter by memory type to quickly recall engineering choices or retrospectives.

### 7. Instructions
Configure project-specific prompts. 
- Enter details regarding the agent's role, instructions, voice, and system constraints.
- Instructions are saved automatically as you type and injected into all executions in this project.

### 8. Settings
Manage project-level configurations:
- **Default model**: Select the primary model (fast, pro, max) for execution.
- **Auto-archive**: Automatically archive chats with no activity after 7, 30, or 90 days.
- **Sharing**: Enable/disable sharing with other members in the workspace.
- **Delete project**: Soft-deletes the project and archives all child resources.

### 9. Audit
Immutable compliance logs tracking actions taken in this project:
- Events logged: `project.created`, `project.deleted`, `knowledge.added`, `run.completed`, `artifact.created`, `memory.created`, etc.
- Displays the event name, actor, target ID, metadata payload, and timestamp.

"""One-shot migration: extract FastAPI routes from gateway/main.py into gateway/routers/.

Mechanical, AST-based transformation (no hand transcription):
- Route handlers move to gateway/routers/<area>.py as APIRouter modules.
- References to names defined at gateway.main top level are rewritten to ``m.<name>``,
  resolved at call time, so tests and workers that mutate ``gateway.main`` attributes
  (DATA_DIR, DATABASE, ADMIN_FILE, RETRY_BASE_SECONDS, ...) keep working.
- gateway/main.py keeps the domain services, app assembly, static routes and the
  middleware, and re-exports every moved handler as a backwards-compatible facade.
"""
import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "gateway" / "main.py"
ROUTERS_DIR = ROOT / "gateway" / "routers"

ROUTE_MODULE = {
    # system
    "app_version": "system", "health_live": "system", "health": "system",
    "system_status": "system", "list_observability_events": "system",
    "list_notifications": "system", "read_notification": "system",
    "read_all_notifications": "system",
    # auth
    "login": "auth", "logout": "auth", "auth_session": "auth",
    # projects
    "list_projects": "projects", "create_project": "projects", "get_project": "projects",
    "patch_project": "projects", "delete_project": "projects",
    # settings
    "generation_defaults": "settings", "put_generation_defaults": "settings",
    "provider_readiness": "settings", "list_nvidia_models": "settings",
    "test_nvidia_model": "settings", "validate_provider_key": "settings",
    "update_providers": "settings",
    # jobs
    "create_job": "jobs", "list_jobs": "jobs", "get_job": "jobs", "retry_job": "jobs",
    "cancel_job": "jobs", "delete_job": "jobs", "job_events": "jobs",
    "artifact_proxy": "jobs", "put_metadata": "jobs", "generate_metadata": "jobs",
    "approve_job": "jobs", "derive_shorts": "jobs", "publish_job": "jobs",
    "cancel_schedule": "jobs",
    # assistant
    "assistant_chat": "assistant",
    # scripts
    "script_proposal": "scripts", "script_reference": "scripts", "short_script": "scripts",
    "long_script": "scripts", "long_script_stream": "scripts", "script_terms": "scripts",
    # channels
    "list_channels": "channels", "patch_channel": "channels", "delete_channel": "channels",
    "youtube_connect": "channels", "youtube_auth_callback": "channels",
    "tiktok_connect": "channels", "tiktok_reconnect_link": "channels",
    "refresh_channel": "channels",
    # agents / analytics
    "strategy": "agents", "analytics_platform_overview": "agents",
    "analytics_overview": "agents", "refresh_analytics": "agents",
    "list_schedule": "agents", "list_operators": "agents", "put_operator": "agents",
    "run_operator": "agents",
}

MODULE_DOCSTRINGS = {
    "system": "Version, health, status, logs and notifications endpoints.",
    "auth": "Login, logout and session endpoints.",
    "projects": "Project CRUD endpoints.",
    "settings": "Generation defaults and provider settings endpoints.",
    "jobs": "Video job lifecycle: creation, polling, artifacts, review and publication.",
    "assistant": "Natural-language assistant endpoint.",
    "scripts": "Script proposal, reference, short and long script generation.",
    "channels": "Channel management and platform (YouTube/TikTok) connection endpoints.",
    "agents": "Strategy, analytics, schedule and channel operator endpoints.",
}

# Routes that stay in main.py (app assembly concerns).
KEEP_IN_MAIN = {"no_cache_for_static_app", "root", "login_page", "image_slots_state", "favicon"}

# Router registration order (mirrors the original ordering in main.py).
MODULE_ORDER = ["system", "auth", "projects", "settings", "jobs", "assistant", "scripts", "channels", "agents"]

SKIP_IMPORTS = {
    "from __future__ import annotations",
    "from gateway.auto_generation import router as auto_generation_router",
    "from gateway.growth.api import router as growth_router",
}

src = MAIN_PATH.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

import_nodes = []
prefix_names = set()
routes = []
kept_routes = set()


def is_app_route(node):
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
        and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"
        for d in node.decorator_list
    )


def add_assign_target_names(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name):
                    prefix_names.add(t.id)
        elif isinstance(sub, ast.AnnAssign):
            if isinstance(sub.target, ast.Name):
                prefix_names.add(sub.target.id)


for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        import_nodes.append(node)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if is_app_route(node):
            if node.name in KEEP_IN_MAIN:
                kept_routes.add(node.name)
            elif node.name in ROUTE_MODULE:
                routes.append((node, ROUTE_MODULE[node.name]))
            else:
                raise SystemExit(f"Unmapped route: {node.name}")
        prefix_names.add(node.name)
    elif isinstance(node, ast.ClassDef):
        prefix_names.add(node.name)
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Try)):
        add_assign_target_names(node)

if kept_routes != KEEP_IN_MAIN:
    raise SystemExit(f"Kept routes mismatch: {sorted(kept_routes)}")
missing = set(ROUTE_MODULE) - {n for n, _ in routes}
if missing:
    raise SystemExit(f"Routes not found in main.py: {sorted(missing)}")


def collect_locals(fn):
    loc = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                loc.add(arg.arg)
            if a.vararg:
                loc.add(a.vararg.arg)
            if a.kwarg:
                loc.add(a.kwarg.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            loc.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                loc.add(al.asname or al.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            loc.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            raise SystemExit(f"global/nonlocal inside route {fn.name}: {ast.unparse(n)}")
        elif isinstance(n, ast.MatchAs) and n.name:
            loc.add(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            loc.add(n.name)
    return loc


conflicts = []
prepared = []  # (module, node, dec_start, end_line, edits, used_names)

for node, mod in routes:
    loc = collect_locals(node)
    if loc & {"m", "main"}:
        raise SystemExit(f"Route {node.name} defines a local named 'm'/'main'; alias collision.")
    overlap = loc & prefix_names
    if overlap:
        conflicts.append((node.name, sorted(overlap)))
    edits = []
    used = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            used.add(n.id)
            if isinstance(n.ctx, ast.Load) and n.id in prefix_names and n.id not in loc and n.lineno >= node.lineno:
                edits.append((n.lineno, n.col_offset, n.id, f"m.{n.id}"))
    dec_start = min(d.lineno for d in node.decorator_list)
    for d in node.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"):
            edits.append((d.func.value.lineno, d.func.value.col_offset, "app", "router"))
        else:
            raise SystemExit(f"Unexpected decorator on {node.name}: {ast.unparse(d)}")
    prepared.append((mod, node, dec_start, node.end_lineno, edits, used))

def offset_within(decl_line, target_line, col):
    return sum(len(l) for l in lines[decl_line - 1: target_line - 1]) + col


modules = defaultdict(list)
for item in prepared:
    modules[item[0]].append(item)

ROUTERS_DIR.mkdir(exist_ok=True)
(ROUTERS_DIR / "__init__.py").write_text(
    '"""HTTP routers for the MoneyPrinter Cockpit gateway.\n\n'
    'Handlers stay thin; domain services live in gateway.main and are reached via the\n'
    "`m` module alias so runtime overrides (tests, workers) keep working. Import\n"
    'gateway.main, not these modules directly.\n"""\n',
    encoding="utf-8",
)

bound_cache = {}


def bound_names(node):
    key = id(node)
    if key not in bound_cache:
        if isinstance(node, ast.Import):
            bound_cache[key] = {al.asname or al.name.split(".")[0] for al in node.names}
        else:
            bound_cache[key] = {al.asname or al.name for al in node.names}
    return bound_cache[key]


module_functions = {}

for mod in MODULE_ORDER:
    items = sorted(modules[mod], key=lambda it: it[2])
    used = set().union(*(it[5] for it in items)) | {"m"}
    emitted = []
    for node in import_nodes:
        text = ast.unparse(node)
        if text in SKIP_IMPORTS:
            continue
        if bound_names(node) & used:
            emitted.append(text)
    import_block = "\n".join(emitted)
    body_parts = []
    for _mod2, node, dec_start, end_line, edits, _used in items:
        seg = "".join(lines[dec_start - 1: end_line])
        for ln, col, old, new in sorted(edits, key=lambda e: offset_within(dec_start, e[0], e[1]), reverse=True):
            i = offset_within(dec_start, ln, col)
            if seg[i:i + len(old)] != old:
                raise SystemExit(f"Edit mismatch in {node.name} at {ln}:{col}: {seg[i:i+20]!r}")
            seg = seg[:i] + new + seg[i + len(old):]
        body_parts.append(seg.rstrip("\n"))
    content = (
        f'"""{MODULE_DOCSTRINGS[mod]}"""\n'
        "from __future__ import annotations\n\n"
        "from fastapi import APIRouter\n\n"
        "# Defined before importing gateway.main so importing this module directly\n"
        "# cannot observe a half-built router during gateway.main's own import.\n"
        "router = APIRouter()\n\n"
    )
    if import_block:
        content += import_block + "\n\n"
    content += "from gateway import main as m\n\n\n"
    content += "\n\n\n".join(body_parts) + "\n"
    (ROUTERS_DIR / f"{mod}.py").write_text(content, encoding="utf-8")
    module_functions[mod] = [it[1].name for it in items]

# ---- rebuild main.py -------------------------------------------------------
del_ranges = []
for _mod, node, dec_start, end_line, _edits, _used in prepared:
    start_idx = dec_start - 1
    consumed = 0
    while start_idx > 0 and lines[start_idx - 1].strip() == "" and consumed < 2:
        start_idx -= 1
        consumed += 1
    del_ranges.append((start_idx, end_line))

keep = []
cursor = 0
for s, e in sorted(del_ranges):
    keep.extend(lines[cursor:s])
    cursor = e
keep.extend(lines[cursor:])

insert_at = next(i for i, l in enumerate(keep) if "app.include_router(auto_generation_router)" in l)

block = [
    "# HTTP routers: handlers live in gateway/routers; domain services stay in this module.\n",
    "from gateway.routers import agents, assistant, auth, channels, jobs, projects, scripts, settings, system\n\n",
]
for mod in MODULE_ORDER:
    block.append(f"app.include_router({mod}.router)\n")
block.append("\n# Backwards-compatible facade: keep every moved handler reachable as gateway.main.<name>.\n")
for mod in MODULE_ORDER:
    names = ", ".join(module_functions[mod])
    block.append(f"from gateway.routers.{mod} import {names}  # noqa: F401\n")

keep = keep[:insert_at] + block + keep[insert_at:]
MAIN_PATH.write_text("".join(keep), encoding="utf-8")

print(f"Moved {len(prepared)} route handlers into {len(MODULE_ORDER)} router modules.")
for mod in MODULE_ORDER:
    print(f"  {mod}.py: {len(module_functions[mod])} routes")
if conflicts:
    print("\nLocal/global name overlaps (renames skipped inside these functions):")
    for name, overlap in conflicts:
        print(f"  {name}: {overlap}")

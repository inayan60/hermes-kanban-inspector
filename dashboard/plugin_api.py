"""Hermes Bot Inspector — scoped Desktop API. No LLM calls, no DB writes.

The only mutable operation is a deliberate, identity-checked termination of a
verified Git child of a running Kanban managed_worker, after preview + consent.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
_ID = re.compile(r"^[A-Za-z0-9_\-.]{1,100}$")
_TASK = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
_PREVIEWS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_LIMIT = 220
_IS_WINDOWS = os.name == "nt"


def home() -> Path:
    explicit = os.environ.get("HERMES_HOME", "").strip()
    if explicit:
        active_home = Path(explicit).expanduser().resolve()
        # Multi-profile servers use <root>/profiles/<profile> as HERMES_HOME.
        if active_home.parent.name.casefold() == "profiles":
            return active_home.parent.parent
        return active_home
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
    return Path.home() / ".hermes"


def clean_id(value: str, kind: str = "identifier") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise HTTPException(400, f"Invalid {kind}")
    return value


def state_db(profile: str) -> Path:
    profile = clean_id(profile, "profile")
    if profile == "default":
        root_db = home() / "state.db"
        return root_db if root_db.exists() else home() / "profiles" / "default" / "state.db"
    return home() / "profiles" / profile / "state.db"


def board_db(board: str) -> Path:
    board = clean_id(board, "board")
    if board == "default":
        root_db = home() / "kanban.db"
        return root_db if root_db.exists() else home() / "kanban" / "boards" / "default" / "kanban.db"
    return home() / "kanban" / "boards" / board / "kanban.db"


def db_open(path: Path):
    if not path.is_file():
        raise HTTPException(404, f"Database not found: {path.name}")
    # Read-only URI honors WAL, and never writes to Hermes' live databases.
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=2000")
    return con


def cols(con: sqlite3.Connection, table: str) -> set[str]:
    if not re.fullmatch(r"[a-z_]+", table):
        raise ValueError("invalid table")
    return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})")}


def record(row: Any) -> dict:
    return dict(row) if row else {}


def clip(s: Any, n=1500) -> str:
    if s is None:
        return ""
    if isinstance(s, (dict, list)):
        s = json.dumps(s, ensure_ascii=False, default=str)
    if not isinstance(s, str):
        s = str(s)
    # Conservative best-effort redaction. Some secrets cannot be identified.
    s = re.sub(r'(?i)("(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|authorization|client_secret|cookie)"\s*:\s*")([^"]*)',
               r'\1[REDACTED]', s)
    s = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._+\-/=]{10,}", r"\1[REDACTED]", s)
    s = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|secret|password|authorization|client_secret|cookie)\s*[=:]\s*)[^\s,;&\"']+", r"\1[REDACTED]", s)
    s = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b", "[REDACTED]", s)
    return s[:n] + ("… [truncated]" if len(s) > n else "")


def dt(value: Any) -> str:
    try:
        t = float(value)
        if t > 10**12:
            t /= 1000
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def load_json(v: Any) -> Any:
    if not v:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return None


def boards_profiles() -> dict:
    h = home()
    boards = []
    if board_db("default").is_file():
        boards.append("default")
    folder = h / "kanban" / "boards"
    if folder.is_dir():
        boards.extend(p.name for p in folder.iterdir() if p.is_dir() and _ID.fullmatch(p.name) and (p / "kanban.db").is_file())
    profiles = []
    if state_db("default").is_file():
        profiles.append("default")
    folder = h / "profiles"
    if folder.is_dir():
        profiles.extend(p.name for p in folder.iterdir() if p.is_dir() and _ID.fullmatch(p.name) and (p / "state.db").is_file())
    return {"boards": sorted(set(boards)), "profiles": sorted(set(profiles)), "os": os.name, "home": str(h), "py_spy": bool(find_pyspy()), "version": "0.2.0"}


def tasks(board: str) -> list[dict]:
    with closing(db_open(board_db(board))) as con:
        if not {"id", "status"}.issubset(cols(con, "tasks")):
            return []
        rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 250").fetchall()
        return [{"id": r["id"], "title": clip(r["title"], 140) if "title" in r.keys() else r["id"],
                 "assignee": r["assignee"] if "assignee" in r.keys() else None,
                 "status": r["status"], "created_at": dt(r["created_at"]) if "created_at" in r.keys() else ""}
                for r in rows]


def sessions(profile: str) -> list[dict]:
    with closing(db_open(state_db(profile))) as con:
        names = cols(con, "sessions")
        if "id" not in names:
            return []
        order = "updated_at" if "updated_at" in names else "rowid"
        rows = con.execute(f"SELECT * FROM sessions ORDER BY {order} DESC LIMIT 200").fetchall()
        return [{"id": r["id"], "title": clip(r["title"], 120) if "title" in names else "",
                 "model": r["model"] if "model" in names else "",
                 "source": r["source"] if "source" in names else ""} for r in rows]


def task_info(board: str, task_id: str) -> dict:
    clean_id(task_id, "task")
    with closing(db_open(board_db(board))) as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Task not found")
        t = record(row)
        runs = [record(r) for r in con.execute("SELECT * FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 20", (task_id,))] if "task_runs" in _tables(con) else []
        events = [record(r) for r in con.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 160", (task_id,))] if "task_events" in _tables(con) else []
        comments = [record(r) for r in con.execute("SELECT * FROM task_comments WHERE task_id=? ORDER BY id DESC LIMIT 80", (task_id,))] if "task_comments" in _tables(con) else []
    sid = ""
    for event in events:
        payload = load_json(event.get("payload")) or {}
        if event.get("kind") == "owner_admitted" and isinstance(payload, dict) and payload.get("session_id"):
            sid = str(payload["session_id"])
            break
    return {"task": t, "runs": runs, "events": events, "comments": comments, "session_id": sid,
            "profile": t.get("assignee") or (runs[0].get("profile") if runs else "")}


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _category(tool: str) -> str:
    n = tool.lower()
    if any(x in n for x in ("terminal", "execute", "shell", "process", "bash", "powershell", "command")):
        return "terminal"
    if any(x in n for x in ("write", "edit", "patch", "replace", "apply")):
        return "write"
    if any(x in n for x in ("read", "view", "list", "grep", "search", "find", "file")):
        return "read"
    return "tool"


def parse_calls(raw: Any) -> list[dict]:
    val = load_json(raw) if isinstance(raw, str) else raw
    if isinstance(val, dict):
        val = val.get("tool_calls", [])
    if not isinstance(val, list):
        return []
    out = []
    for call in val[:50]:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or call.get("tool_name") or "tool"
        args = load_json(fn.get("arguments")) if isinstance(fn, dict) else None
        if args is None:
            args = fn.get("arguments") if isinstance(fn, dict) else None
        out.append({"kind": _category(str(name)), "name": clip(name, 120), "detail": clip(args, 700), "call_id": clip(call.get("id") or call.get("call_id"), 80)})
    return out


def timeline_profile(profile: str, sid: str, limit=160) -> dict:
    clean_id(profile, "profile")
    if not sid or len(sid) > 256:
        raise HTTPException(400, "Invalid session")
    with closing(db_open(state_db(profile))) as con:
        names = cols(con, "messages")
        if not {"session_id", "role"}.issubset(names):
            return {"timeline": [], "total": 0, "notice": "Message schema unavailable"}
        row = con.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,)).fetchone()
        total = row[0] if row else 0
        order = "id" if "id" in names else "rowid"
        messages = [dict(r) for r in con.execute(f"SELECT * FROM messages WHERE session_id=? ORDER BY {order} DESC LIMIT ?", (sid, min(max(limit, 1), 300)))][::-1]
        executions = []
        if "worker_executions" in _tables(con) and "session_id" in cols(con, "worker_executions"):
            executions = [dict(r) for r in con.execute("SELECT * FROM worker_executions WHERE session_id=? LIMIT 5", (sid,))]
    result = []
    for r in messages:
        role = str(r.get("role", ""))
        stamp = dt(r.get("created_at") or r.get("timestamp"))
        content = clip(r.get("content"), 1100)
        calls = parse_calls(r.get("tool_calls"))
        if calls:
            for call in calls:
                result.append({"time": stamp, "kind": call["kind"], "title": call["name"], "detail": call["detail"], "call_id": call["call_id"]})
        if role == "tool":
            result.append({"time": stamp, "kind": "result", "title": clip(r.get("tool_name") or "Tool result", 100), "detail": content})
        elif role in ("assistant", "user", "system") and content.strip():
            result.append({"time": stamp, "kind": role if role != "system" else "message", "title": role, "detail": content})
    return {"timeline": result, "total": total, "executions": [{k: e.get(k) for k in ("execution_id", "kind", "status", "last_sequence")} for e in executions],
            "notice": "Zero persisted steps" if total == 0 else ""}


def board_timeline(board: str, task_id: str) -> dict:
    info = task_info(board, task_id)
    out = []
    for ev in info["events"]:
        payload = load_json(ev.get("payload"))
        out.append({"time": dt(ev.get("created_at")), "kind": "board", "title": clip(ev.get("kind"), 100), "detail": clip(payload or ev.get("payload"), 700)})
    for cm in info["comments"]:
        out.append({"time": dt(cm.get("created_at")), "kind": "comment", "title": clip(cm.get("author"), 60), "detail": clip(cm.get("body"), 700)})
    return {"timeline": sorted(out, key=lambda x: x["time"]), "info": info}


def find_pyspy() -> str | None:
    direct = shutil.which("py-spy")
    if direct:
        return direct
    roots = [home() / "tools", Path(sys.executable).parent, Path.home() / "AppData" / "Roaming" / "Python"]
    for root in roots:
        if root.is_dir():
            # bounded search only; avoid recursively walking all of AppData
            for path in ([root / "py-spy.exe", root / "Scripts" / "py-spy.exe"] +
                         [p / "Scripts" / "py-spy.exe" for p in list(root.glob("Python*"))[:6]]):
                if path.is_file():
                    return str(path)
    return None


def processes() -> list[dict]:
    if not _IS_WINDOWS:
        return []
    ps = shutil.which("powershell.exe") or "powershell.exe"
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate | "
              "ConvertTo-Json -Compress -Depth 3")
    try:
        run = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, check=False)
        if run.returncode != 0:
            return []
        data = json.loads(run.stdout.strip() or "[]")
        return data if isinstance(data, list) else [data]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def proc_map(all_p: list[dict]) -> dict[int, dict]:
    return {int(p["ProcessId"]): p for p in all_p if p.get("ProcessId") is not None}


def tree_relation(p: dict[int, dict], pid: int, parent: int, cap=12) -> bool:
    seen = set()
    current = pid
    for _ in range(cap):
        if current <= 0 or current in seen or current not in p:
            return False
        seen.add(current)
        current = int(p[current].get("ParentProcessId") or 0)
        if current == parent:
            return True
    return False


def relevant_processes(all_p: list[dict], anchor: list[int]) -> list[dict]:
    p = proc_map(all_p)
    selected = []
    for pid, obj in p.items():
        if pid > 0 and (pid in anchor or any(tree_relation(p, pid, a) for a in anchor if a > 0)):
            selected.append({"pid": pid, "ppid": obj.get("ParentProcessId"), "name": obj.get("Name"),
                             "command": clip(obj.get("CommandLine"), 260), "created": str(obj.get("CreationDate") or "")})
    return selected[:90]


def git_patch() -> dict:
    file = home() / "hermes-agent" / "hermes_cli" / "version_info.py"
    if not file.is_file():
        return {"file_found": False, "patched": None}
    source = file.read_text(encoding="utf-8-sig", errors="replace")
    i = source.find("def _run_git(")
    j = source.find("\ndef ", i + 1)
    frag = source[i:j if j > i else i+2800] if i >= 0 else ""
    return {"file_found": True, "patched": "tempfile.TemporaryFile" in frag,
            "captures_pipes": "capture_output=True" in frag, "relative_path": "hermes-agent/hermes_cli/version_info.py"}


def diagnose(board: str, task_id: str) -> dict:
    info = task_info(board, task_id)
    profile = info["profile"] or "default"
    sid = info["session_id"]
    events = info["events"]
    worker_pid = int(info["runs"][0].get("worker_pid") or info["task"].get("worker_pid") or 0) if info["runs"] else int(info["task"].get("worker_pid") or 0)
    ll = timeline_profile(profile, sid, 1) if sid and state_db(profile).is_file() else {"total": None, "executions": []}
    managed_pid = 0
    gateway_pid = 0
    for ev in events:
        if ev.get("kind") == "owner_admitted":
            payload = load_json(ev.get("payload")) or {}
            gateway_pid = int(payload.get("pid") or 0)
        if ev.get("kind") == "worker_bound":
            payload = load_json(ev.get("payload")) or {}
            managed_pid = int(payload.get("pid") or 0)
            break
    all_p = processes()
    pm = proc_map(all_p)
    related = relevant_processes(all_p, [worker_pid, managed_pid])
    if gateway_pid > 0 and gateway_pid in pm and gateway_pid not in {q["pid"] for q in related}:
        g = pm[gateway_pid]
        related.insert(0, {"pid": gateway_pid, "ppid": g.get("ParentProcessId"), "name": g.get("Name"),
                           "command": clip(g.get("CommandLine"), 260), "created": str(g.get("CreationDate") or "")})
    git = [x for x in related if str(x["name"]).lower() == "git.exe"]
    other_git = [x for x in all_p if str(x.get("Name", "")).lower() == "git.exe" and int(x.get("ProcessId") or 0) not in {x["pid"] for x in git}]
    latest_run = info['runs'][0] if info['runs'] else {}
    started = latest_run.get('started_at')
    ended = latest_run.get('ended_at')
    if started is not None and ended is not None:
        elapsed = max(0, int(float(ended) - float(started)))
        elapsed_source = 'run end-start'
    elif started is not None and info['task'].get('status') == 'running':
        elapsed = max(0, int(time.time() - float(started)))
        elapsed_source = 'active run duration at snapshot'
    else:
        elapsed = None
        elapsed_source = 'run end time unavailable (historical duration unknown)'
    no_steps = ll.get("total") == 0
    stale = elapsed is not None and elapsed > 300 and no_steps and info["task"].get("status") == "running"
    return {"board": board, "task_id": task_id, "profile": profile, "session_id": sid,
            "status": info["task"].get("status"), "run_id": info["runs"][0].get("id") if info["runs"] else None,
            "worker_pid": worker_pid, "managed_pid": managed_pid, "gateway_pid": gateway_pid, "worker_alive": (worker_pid in pm) if worker_pid > 0 and info["task"].get("status") == "running" and all_p else None,
            "last_heartbeat_at": dt(info["runs"][0].get("last_heartbeat_at")) if info["runs"] else "",
            "elapsed_seconds": elapsed, "elapsed_source": elapsed_source, "message_count": ll.get("total"), "worker_executions": ll.get("executions", []),
            "suspected_stall": stale, "related_processes": related,
            "verified_git_descendants": git, "unrelated_git_count": len(other_git), "patch": git_patch(),
            "py_spy_available": bool(find_pyspy()),
            "assessment": ("Active run without persisted messages for >5 min; inspect managed_worker stack." if stale
                           else "No conclusive stall from persisted state alone.")}


def stack(pid: int) -> dict:
    if pid < 1 or pid > 2**31:
        raise HTTPException(400, "Invalid PID")
    all_p = processes()
    pm = proc_map(all_p)
    if pid not in pm:
        raise HTTPException(404, "PID not found")
    name = str(pm[pid].get("Name") or "").lower()
    if name not in ("python.exe", "pythonw.exe"):
        raise HTTPException(400, "py-spy is only available for Python processes")
    spy = find_pyspy()
    if not spy:
        raise HTTPException(412, "py-spy not installed; the main diagnostics still work")
    try:
        run = subprocess.run([spy, "dump", "--pid", str(pid)],
                             capture_output=True, encoding="utf-8", errors="replace", text=True,
                             timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(504, f"py-spy unavailable: {type(exc).__name__}")
    return {"pid": pid, "exit_code": run.returncode, "stack": clip((run.stdout or "") + "\n" + (run.stderr or ""), 20000),
            "git_header_deadlock": "_run_git" in run.stdout and "codex_cloudflare_headers" in run.stdout}


def recovery_preview(board: str, task_id: str) -> dict:
    data = diagnose(board, task_id)
    if not _IS_WINDOWS or data["status"] != "running" or not data["worker_alive"]:
        return {"available": False, "reason": "Only a live Windows Kanban run is supported"}
    # Reject missing/unverified targets — never infer ownership from name alone.
    candidates = [p for p in data["verified_git_descendants"]
                  if re.search(r"\bgit(?:\.exe)?\s+(?:-C\s+[^\s]+\s+)?(?:rev-parse|status)\b", p["command"], re.I)]
    if not candidates:
        return {"available": False, "reason": "No verifiable Git descendant of the active run. No process will be terminated.",
                "unrelated_git_count": data["unrelated_git_count"]}
    target = candidates[0]
    nonce = secrets.token_urlsafe(24)
    with _LOCK:
        _PREVIEWS.clear()  # single outstanding preview, strictly short-lived
        _PREVIEWS[nonce] = {"at": time.time(), "board": board, "task_id": task_id,
                            "worker_pid": data["worker_pid"], "managed_pid": data["managed_pid"], "run_id": data["run_id"],
                            "pid": target["pid"], "created": target["created"], "ppid": target["ppid"]}
    return {"available": True, "token": nonce, "target": target,
            "message": "Terminate only this verified Git descendant. May fail the run; no automatic retry."}


def recovery_apply(token: str, confirm: str) -> dict:
    if confirm != "ENCERRAR GIT":
        raise HTTPException(400, "Explicit confirmation required")
    with _LOCK:
        item = _PREVIEWS.pop(token, None)
    if not item or time.time() - item["at"] > 60:
        raise HTTPException(409, "Preview expired; inspect again")
    info = task_info(item["board"], item["task_id"])
    run = info["runs"][0] if info["runs"] else {}
    if info["task"].get("status") != "running" or run.get("id") != item["run_id"] or int(run.get("worker_pid") or 0) != item["worker_pid"]:
        raise HTTPException(409, "Run changed since preview; operation refused")
    p = proc_map(processes())
    target = p.get(item["pid"])
    if not target or str(target.get("CreationDate") or "") != item["created"] or str(target.get("Name") or "").lower() != "git.exe":
        raise HTTPException(409, "Git process identity changed; operation refused")
    if not (tree_relation(p, item["pid"], item["worker_pid"]) or
            (item["managed_pid"] and tree_relation(p, item["pid"], item["managed_pid"]))):
        raise HTTPException(409, "Git no longer belongs to this worker; operation refused")
    cmd = str(target.get("CommandLine") or "")
    if not re.search(r"\bgit(?:\.exe)?\s+(?:-C\s+[^\s]+\s+)?(?:rev-parse|status)\b", cmd, re.I):
        raise HTTPException(409, "Unexpected Git command; operation refused")
    try:
        os.kill(item["pid"], signal.SIGTERM)
    except (OSError, PermissionError) as exc:
        raise HTTPException(500, f"Unable to stop verified Git process: {type(exc).__name__}")
    return {"ok": True, "pid": item["pid"], "message": "Only the verified Git subprocess was terminated. Refresh the run to check recovery."}


# A single bounded, read-only audit across every attempt on a Kanban card.
# Data provenance is explicit; we do not backfill absent session links with guesses.
_AUDIT_MAX_RUNS = 100
_AUDIT_MAX_EVENTS = 10000
_AUDIT_MAX_MESSAGES = 4500
_AUDIT_MAX_ARTIFACTS = 300
_USAGE_FIELDS = ('api_call_count', 'input_tokens', 'output_tokens', 'cache_read_tokens',
                 'cache_write_tokens', 'reasoning_tokens')


def _num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _event_run_id(e: dict, board: str, task_id: str) -> int | None:
    run = _num(e.get('run_id'))
    if run > 0:
        return run
    payload = load_json(e.get('payload')) or {}
    if not isinstance(payload, dict):
        return None
    run = _num(payload.get('run_id'))
    if run > 0:
        return run
    request = str(payload.get('request_id') or '')
    if request.startswith('kanban:'):
        try:
            keys = json.loads(request[7:])
            if keys[:2] == [board, task_id]:
                run = _num(keys[2])
                return run if run > 0 else None
        except (ValueError, TypeError, IndexError):
            pass
    return None


def _usage_row(r: dict, source: str) -> dict:
    data = {field: _num(r.get(field)) for field in _USAGE_FIELDS}
    data.update(model=clip(r.get('model'), 140), provider=clip(r.get('billing_provider'), 80),
                task=clip(r.get('task'), 80), source=source,
                estimated_cost_usd=float(r.get('estimated_cost_usd') or 0),
                actual_cost_usd=float(r.get('actual_cost_usd') or 0),
                cost_status=clip(r.get('cost_status'), 60),
                first_seen=dt(r.get('first_seen')), last_seen=dt(r.get('last_seen')))
    return data


def _load_session(profile: str, sid: str, run_id: int, role='primary') -> dict:
    clean_id(profile, 'profile')
    if not sid or len(sid) > 256:
        return {'id': clip(sid, 100), 'profile': profile, 'run_id': run_id,
                'error': 'invalid session identifier', 'usage': [], 'events': []}
    if not state_db(profile).is_file():
        return {'id': sid, 'profile': profile, 'run_id': run_id, 'role': role,
                'error': 'state.db unavailable for this profile', 'usage': [], 'events': []}
    with closing(db_open(state_db(profile))) as con:
        tables = _tables(con)
        sr = record(con.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()) if 'sessions' in tables else {}
        if not sr:
            return {'id': sid, 'profile': profile, 'run_id': run_id, 'role': role,
                    'error': 'session not present in state.db', 'usage': [], 'events': []}
        usage = []
        if 'session_model_usage' in tables:
            usage = [_usage_row(dict(r), 'session_model_usage') for r in
                     con.execute('SELECT * FROM session_model_usage WHERE session_id=? ORDER BY model, task', (sid,))]
        if not usage:
            usage = [_usage_row(sr, 'sessions aggregate (per-model breakdown unavailable)')]
        names = cols(con, 'messages') if 'messages' in tables else set()
        total = 0
        messages = []
        if {'session_id', 'role'}.issubset(names):
            total = con.execute('SELECT COUNT(*) FROM messages WHERE session_id=?', (sid,)).fetchone()[0]
            order = 'id' if 'id' in names else 'rowid'
            messages = [dict(r) for r in con.execute(
                f'SELECT * FROM messages WHERE session_id=? ORDER BY {order} ASC LIMIT ?',
                (sid, _AUDIT_MAX_MESSAGES))]
        executions = []
        receipts = []
        if 'worker_executions' in tables:
            executions = [dict(r) for r in con.execute(
                'SELECT * FROM worker_executions WHERE session_id=? ORDER BY execution_id LIMIT 30', (sid,))]
            if 'worker_receipts' in tables:
                for e in executions:
                    rows = con.execute('SELECT sequence, result_json FROM worker_receipts WHERE execution_id=? ORDER BY sequence DESC LIMIT 30', (e.get('execution_id'),))
                    for r in rows:
                        payload = load_json(r['result_json'])
                        excerpt = clip(payload if payload is not None else r['result_json'], 850)
                        if any(term in excerpt.lower() for term in ('error','traceback','exception','failed','exit_code','stderr')):
                            receipts.append({'execution_id': clip(e.get('execution_id'), 140),
                                             'sequence': r['sequence'], 'detail': excerpt,
                                             'source': 'worker_receipts.result_json'})
        child_ids = []
        if 'sessions' in tables and 'parent_session_id' in cols(con, 'sessions'):
            child_ids = [str(r[0]) for r in con.execute(
                'SELECT id FROM sessions WHERE parent_session_id=? ORDER BY started_at LIMIT 24', (sid,))]

    timeline = []
    calls = []
    tool_responses = []
    for row in messages:
        mid = _num(row.get('id'))
        timestamp = dt(row.get('timestamp') if row.get('timestamp') is not None else row.get('created_at'))
        role_msg = str(row.get('role') or '')
        body = clip(row.get('content'), 1250)
        for idx, call in enumerate(parse_calls(row.get('tool_calls'))):
            c = {'time': timestamp, 'kind': call['kind'], 'title': call['name'],
                 'detail': call['detail'], 'call_id': call['call_id'],
                 'session_id': sid, 'profile': profile, 'run_id': run_id,
                 'message_id': mid, 'source': 'messages.tool_calls'}
            timeline.append(c)
            calls.append(c)
        if role_msg == 'tool':
            target = clip(row.get('tool_name') or 'tool_result', 100)
            call_id = clip(row.get('tool_call_id'), 120)
            failure = bool(re.search(r'(?im)^\s*(?:error|exception|traceback|failed|exit code\s*[1-9])\b', body))
            result = {'time': timestamp, 'kind': 'result', 'title': target, 'detail': body,
                      'call_id': call_id, 'session_id': sid, 'profile': profile,
                      'run_id': run_id, 'message_id': mid, 'source': 'messages.tool',
                      'possible_error': failure}
            timeline.append(result)
            tool_responses.append(result)
        elif role_msg in ('assistant', 'user', 'system') and body:
            timeline.append({'time': timestamp, 'kind': role_msg if role_msg != 'system' else 'message',
                             'title': role_msg, 'detail': body, 'session_id': sid, 'profile': profile,
                             'run_id': run_id, 'message_id': mid, 'source': 'messages.content'})
    # A repeated tool+sanitized argument is a signal, not proof of wasted work.
    repeated = {}
    for c in calls:
        key = (c['title'], c['detail'])
        repeated[key] = repeated.get(key, 0) + 1
    duplicates = [{'tool': name, 'count': n, 'argument_preview': args[:240]}
                  for (name, args), n in repeated.items() if n > 1]
    return {'id': sid, 'profile': profile, 'run_id': run_id, 'role': role,
            'model': clip(sr.get('model'), 150), 'title': clip(sr.get('title'), 150),
            'source': clip(sr.get('source'), 90), 'started_at': dt(sr.get('started_at')),
            'ended_at': dt(sr.get('ended_at')), 'usage': usage, 'events': timeline,
            'message_count': total, 'returned_messages': len(messages),
            'message_truncated': total > len(messages),
            'tool_calls': len(calls), 'tool_results': len(tool_responses),
            'possible_tool_errors': sum(int(e['possible_error']) for e in tool_responses),
            'repeated_tool_signals': duplicates[:60], 'executions': [
                {k: e.get(k) for k in ('execution_id', 'kind', 'status', 'last_sequence')} for e in executions],
            'error_receipts': receipts[:50], 'child_session_ids': child_ids}


def _read_log_evidence(task_id: str, runs: list[dict]) -> list[dict]:
    """Bounded log snippets, not a full-folder scan. Only records matching task/session IDs."""
    root = home()
    files = [root / 'gateway.log', root / 'logs' / 'gateway.log',
             root / 'logs' / 'hermes.log', root / 'logs' / 'gateway.jsonl']
    # Run-specific logs are frequently under the profile's own logs directory.
    for run in runs:
        profile = str(run.get('profile') or '')
        if profile and _ID.fullmatch(profile):
            files.extend([root / 'profiles' / profile / 'gateway.log',
                          root / 'profiles' / profile / 'logs' / 'gateway.log'])
    needles = [task_id.lower()]
    for run in runs:
        for link in run.get('session_links', []):
            if link.get('session_id'):
                needles.append(str(link['session_id']).lower())
    snippets = []
    for p in list(dict.fromkeys(files))[:15]:
        if not p.is_file() or p.is_symlink():
            continue
        try:
            with p.open('rb') as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size-1_000_000))
                data = f.read(1_000_000).decode('utf-8', errors='replace')
            for line in data.splitlines():
                if any(n and n in line.lower() for n in needles):
                    snippets.append({'source': str(p), 'detail': clip(line, 1100)})
                    if len(snippets) >= 40:
                        return snippets
        except OSError:
            continue
    return snippets


def _live_worker_status(pid: int, fingerprint: Any) -> str:
    """Only the Hermes dispatcher fingerprint can prove a PID is the same worker."""
    if pid <= 0:
        return 'unknown (PID absent or invalid)'
    if not fingerprint or fingerprint == 'unverified':
        return 'unknown (no verifiable Hermes process fingerprint)'
    try:
        # Native 0.22.7 function; read-only and resilient to unavailable platform probes.
        from hermes_cli.kanban_db_dispatch import _process_fingerprint
        live = _process_fingerprint(pid)
    except (ImportError, OSError, ValueError, TypeError):
        return 'unknown (cannot inspect native process fingerprint)'
    if live is None:
        return 'unknown (process identity unavailable)'
    return 'active (Hermes fingerprint verified)' if str(live) == str(fingerprint) else 'ended/recycled (fingerprint mismatch)'


def audit_task(board: str, task_id: str) -> dict:
    clean_id(board, 'board')
    clean_id(task_id, 'task')
    with closing(db_open(board_db(board))) as con:
        tables = _tables(con)
        task = record(con.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
        if not task:
            raise HTTPException(404, 'Task not found')
        runs = [record(r) for r in con.execute('SELECT * FROM task_runs WHERE task_id=? ORDER BY id ASC LIMIT ?',
                                                (task_id, _AUDIT_MAX_RUNS))] if 'task_runs' in tables else []
        events = [record(r) for r in con.execute('SELECT * FROM task_events WHERE task_id=? ORDER BY id ASC LIMIT ?',
                                                 (task_id, _AUDIT_MAX_EVENTS))] if 'task_events' in tables else []
        comments = [record(r) for r in con.execute('SELECT * FROM task_comments WHERE task_id=? ORDER BY id ASC LIMIT 1000',
                                                   (task_id,))] if 'task_comments' in tables else []
        attachments = [record(r) for r in con.execute('SELECT * FROM task_attachments WHERE task_id=? ORDER BY id ASC LIMIT ?',
                                                      (task_id, _AUDIT_MAX_ARTIFACTS))] if 'task_attachments' in tables else []
    run_map = {}
    for row in runs:
        rid = _num(row.get('id'))
        run_map[rid] = {'id': rid, 'profile': row.get('profile') or '', 'status': row.get('status') or 'unknown',
                        'outcome': row.get('outcome'), 'error': clip(row.get('error'), 1300),
                        'summary': clip(row.get('summary'), 1200),
                        'start': dt(row.get('started_at')), 'end': dt(row.get('ended_at')),
                        'started_at_raw': row.get('started_at'), 'ended_at_raw': row.get('ended_at'),
                        'worker_pid': _num(row.get('worker_pid')), 'last_heartbeat_at': dt(row.get('last_heartbeat_at')),
                        'session_links': [], 'sessions': [], 'errors': [], 'event_count': 0,
                        'process_state': 'finished (historical, not probed)' if row.get('status') != 'running' else _live_worker_status(_num(row.get('worker_pid')), row.get('worker_started_at')), 
                        'messages': 0, 'tool_calls': 0, 'tool_results': 0,
                        'usage': [], 'usage_attribution': 'direct session association'}
    board_tl = []
    participants = {}
    def participation(who, role, source):
        name = str(who or '').strip()
        if not name or name.lower() in {'agent', 'desktop', 'system', 'user'} or len(name) > 100:
            return
        value = participants.setdefault(name, {'name': name, 'roles': [], 'sources': []})
        if role not in value['roles']:
            value['roles'].append(role)
        if source not in value['sources']:
            value['sources'].append(source)
    participation(task.get('created_by'), 'creator', 'tasks.created_by')
    participation(task.get('assignee'), 'assignee', 'tasks.assignee')
    for r in runs:
        participation(r.get('profile'), 'run executor', f'task_runs #{r.get("id")}')
    for c in comments:
        participation(c.get('author'), 'comment author', 'task_comments')
        board_tl.append({'time': dt(c.get('created_at')), 'kind': 'comment', 'title': clip(c.get('author'), 90),
                         'detail': clip(c.get('body'), 1400), 'run_id': None, 'source': 'task_comments'})
    for e in events:
        rid = _event_run_id(e, board, task_id)
        payload = load_json(e.get('payload'))
        parsed = payload if isinstance(payload, dict) else {}
        if rid in run_map:
            run_map[rid]['event_count'] += 1
            if e.get('kind') == 'owner_admitted' and parsed.get('session_id'):
                link = {'session_id': str(parsed['session_id']),
                        'profile': run_map[rid]['profile'] or task.get('assignee') or '',
                        'source': 'task_events.owner_admitted',
                        'request_id': clip(parsed.get('request_id'), 160)}
                dbname = str(parsed.get('db') or '')
                m = re.search(r'[/\\]profiles[/\\]([^/\\]+)[/\\]state\.db$', dbname, re.I)
                if m:
                    link['profile'] = m.group(1)
                if not any(s['session_id'] == link['session_id'] for s in run_map[rid]['session_links']):
                    run_map[rid]['session_links'].append(link)
            if e.get('kind') in {'worker_lost', 'spawn_failed', 'worker_failed', 'timed_out'} or (e.get('kind') == 'worker_result' and _num(parsed.get('exit_code')) != 0):
                run_map[rid]['errors'].append({'kind': e.get('kind'), 'time': dt(e.get('created_at')),
                                               'payload': clip(parsed or e.get('payload'), 1600),
                                               'source': 'task_events'})
        if e.get('kind') in {'review_requested', 'review_approved'}:
            participation(parsed.get('reviewer'), 'reviewer', 'task_events')
            participation(parsed.get('implementer'), 'implementer', 'task_events')
        board_tl.append({'time': dt(e.get('created_at')), 'kind': 'board',
                         'title': clip(e.get('kind'), 90), 'detail': clip(parsed or e.get('payload'), 1600),
                         'run_id': rid, 'source': 'task_events'})
    # A task's final human/agent acceptor may never have had a Kanban run.
    # Mention in the card narrative is evidence of participation only, NEVER a session link.
    narrative = str(task.get('result') or '') + '\n' + str(task.get('title') or '')
    narrative += '\n' + '\n'.join(str(c.get('body') or '') for c in comments[-150:])
    narrative += '\n' + '\n'.join(str(e.get('payload') or '') for e in events[-100:])
    pf = home() / 'profiles'
    if pf.is_dir():
        for folder in pf.iterdir():
            if folder.is_dir() and _ID.fullmatch(folder.name) and re.search(
                    r'(?<![\w-])' + re.escape(folder.name) + r'(?![\w-])', narrative, re.I):
                participation(folder.name, 'mentioned in card (no session inferred)', 'task narrative')

    # Session access is read-only. One owner_admitted session belongs to its recorded run,
    # independently of what profile now appears on the task card.
    all_sessions = {}
    limits = []
    for run in run_map.values():
        for link in run['session_links']:
            sid = link['session_id']
            prof = link['profile']
            if not _ID.fullmatch(prof):
                limits.append(f'Run {run["id"]}: missing valid profile for session {sid}')
                continue
            try:
                data = _load_session(prof, sid, run['id'])
            except (sqlite3.Error, OSError) as exc:
                limits.append(f'Run {run["id"]}: state.db read failed ({type(exc).__name__})')
                continue
            run['sessions'].append(data)
            identity = (prof, sid)
            all_sessions[identity] = data
            participation(prof, 'session owner', 'owner_admitted')
            # Only direct child lineage is included; no cross-profile lookups by vague name.
            for child_id in data.get('child_session_ids', [])[:24]:
                child_key = (prof, child_id)
                if child_key in all_sessions:
                    continue
                try:
                    child = _load_session(prof, child_id, run['id'], role='child (parent_session_id)')
                    run['sessions'].append(child)
                    all_sessions[child_key] = child
                except (sqlite3.Error, OSError):
                    limits.append(f'Run {run["id"]}: child session could not be read')
    appearances = {}
    for run in run_map.values():
        for ses in run['sessions']:
            appearances.setdefault((ses['profile'], ses['id']), set()).add(run['id'])
    totals = {key: 0 for key in _USAGE_FIELDS}
    cost_total = 0.0
    actual_cost_total = 0.0
    timeline = list(board_tl)
    for run in run_map.values():
        run_usage = []
        for ses in run['sessions']:
            key = (ses['profile'], ses['id'])
            shared = len(appearances.get(key, set())) > 1
            if shared:
                run['usage_attribution'] = 'shared session across runs; no exclusive run attribution'
            run['messages'] += _num(ses.get('message_count'))
            run['tool_calls'] += _num(ses.get('tool_calls'))
            run['tool_results'] += _num(ses.get('tool_results'))
            timeline.extend(ses.get('events', []))
            if ses.get('message_truncated'):
                limits.append(f'Run {run["id"]}: messages limited to {_AUDIT_MAX_MESSAGES} for {ses["id"]}')
            if ses.get('error'):
                limits.append(f'Run {run["id"]}: {ses["error"]}')
            for u in ses.get('usage', []):
                run_usage.append({**u, 'session_id': ses['id'], 'profile': ses['profile'],
                                  'session_role': ses['role'], 'shared_between_runs': shared})
        run['usage'] = run_usage
        st, en = run.get('started_at_raw'), run.get('ended_at_raw')
        if st is not None and en is not None and float(en) >= float(st):
            run['duration_seconds'] = round(float(en) - float(st), 1)
            run['duration_source'] = 'task_runs.ended_at - task_runs.started_at'
        elif st is not None and run['status'] == 'running':
            run['duration_seconds'] = round(max(0, time.time() - float(st)), 1)
            run['duration_source'] = 'running as of audit time'
        else:
            # A terminal worker_result can supply a bounded fallback when ended_at is NULL.
            terminal = [e for e in events if _event_run_id(e, board, task_id) == run['id']
                        and e.get('kind') == 'worker_result' and e.get('created_at') is not None]
            if st is not None and terminal and float(terminal[-1]['created_at']) >= float(st):
                run['duration_seconds'] = round(float(terminal[-1]['created_at']) - float(st), 1)
                run['duration_source'] = 'task_events.worker_result time - task_runs.started_at (fallback)'
            else:
                run['duration_seconds'] = None
                run['duration_source'] = 'no recorded end; not inferred from card age' 
        if run['error']:
            run['errors'].append({'kind': 'task_runs.error', 'payload': run['error'], 'source': 'task_runs'})
        if run['worker_pid'] <= 0:
            run['process_state'] = 'unknown (invalid/absent PID)' if run['status'] == 'running' else 'finished (historical, PID absent)'
    for ses in all_sessions.values():
        for u in ses.get('usage', []):
            for field in _USAGE_FIELDS:
                totals[field] += u[field]
            cost_total += u.get('estimated_cost_usd', 0.0)
            actual_cost_total += u.get('actual_cost_usd', 0.0)
    total_duration = None
    if task.get('created_at') is not None and task.get('completed_at') is not None:
        total_duration = max(0, float(task['completed_at']) - float(task['created_at']))
    metrics = {'sessions': len(all_sessions), 'run_count': len(runs), 'events': len(events),
               'comments': len(comments), 'artifacts': len(attachments),
               'usage': totals, 'estimated_cost_usd': round(cost_total, 6), 'actual_cost_usd': round(actual_cost_total, 6),
               'card_duration_seconds': round(total_duration, 1) if total_duration is not None else None,
               'card_duration_source': 'tasks.completed_at - tasks.created_at' if total_duration is not None else 'not recorded'}
    event_types = [str(e.get('kind')) for e in events]
    findings = []
    if 'blocked' in event_types and 'unblocked' in event_types:
        findings.append({'type': 'blocked_unblocked', 'severity': 'info', 'text': 'Task blocked and later unblocked; inspect comments for scope changes.'})
    if event_types.count('review_requested'):
        findings.append({'type': 'review', 'severity': 'info', 'text': f'{event_types.count("review_requested")} review request(s) in task events.'})
    for run in run_map.values():
        if any(e['kind'] == 'worker_lost' for e in run['errors']):
            findings.append({'type': 'worker_failure', 'severity': 'warning',
                             'text': f'Run {run["id"]}: worker lost; check run error and log evidence. Card completion does not make this run successful.'})
        repeated = sum(len(s.get('repeated_tool_signals', [])) for s in run['sessions'])
        if repeated:
            findings.append({'type': 'repeated_tool_calls', 'severity': 'info',
                             'text': f'Run {run["id"]}: {repeated} repeated tool/argument pattern(s); possible retry, not proof of waste.'})
    logs = _read_log_evidence(task_id, list(run_map.values()))
    if any(e['kind'] == 'worker_lost' for run in run_map.values() for e in run['errors']) and not logs:
        limits.append('Worker failure may have no persisted traceback; task events and exit_code alone do not establish the technical cause.')
    artifacts_out = [{'id': _num(a.get('id')), 'filename': clip(a.get('filename'), 150),
                      'bytes': _num(a.get('size')), 'time': dt(a.get('created_at')),
                      'type': clip(a.get('content_type'), 100), 'uploaded_by': clip(a.get('uploaded_by'), 90),
                      'previewable': str(a.get('filename') or '').lower().endswith(('.md','.txt','.json','.csv','.log'))}
                     for a in attachments]
    timeline.sort(key=lambda x: (x.get('time') or '', x.get('run_id') or 0))
    # The global timeline is authoritative; avoid duplicating thousands of entries
    # under every run/session in the response and JSON export.
    for run in run_map.values():
        for session_data in run['sessions']:
            session_data.pop('events', None)
    return {'version': '0.2.0', 'board': board, 'task_id': task_id,
            'task': {'title': clip(task.get('title'), 190), 'status': task.get('status'),
                     'assignee': task.get('assignee'), 'created_at': dt(task.get('created_at')),
                     'completed_at': dt(task.get('completed_at'))},
            'runs': list(run_map.values()), 'participants': list(participants.values()),
            'artifacts': artifacts_out, 'timeline': timeline,
            'summary': metrics, 'findings': findings, 'error_log_evidence': logs,
            'limitations': limits, 'audited_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'attribution_note': 'Only sessions directly linked by owner_admitted or parent_session_id are counted. Cache tokens are shown separately, never added a second time to input tokens.'}


def artifact_preview(board: str, task_id: str, attachment_id: int) -> dict:
    clean_id(board, 'board')
    clean_id(task_id, 'task')
    if attachment_id < 1:
        raise HTTPException(400, 'Invalid attachment ID')
    with closing(db_open(board_db(board))) as con:
        if 'task_attachments' not in _tables(con):
            raise HTTPException(404, 'Attachments not available')
        row = con.execute('SELECT * FROM task_attachments WHERE id=? AND task_id=?', (attachment_id, task_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Attachment not found')
    a = record(row)
    path = Path(a.get('stored_path') or '')
    allowed = (home() / 'kanban' / 'attachments' / task_id).resolve()
    file = path.resolve()
    if not file.is_relative_to(allowed) or path.is_symlink() or not file.is_file():
        raise HTTPException(403, 'Attachment path outside task or unavailable')
    if file.suffix.lower() not in {'.md', '.txt', '.json', '.csv', '.log'}:
        return {'id': attachment_id, 'filename': clip(a.get('filename'),150), 'previewable': False,
                'notice': 'Binary/ZIP/PDF files are metadata-only. No automatic extraction.'}
    size = file.stat().st_size
    if size > 262144:
        raise HTTPException(413, 'Artifact exceeds 256 KiB preview limit')
    raw = file.read_text(encoding='utf-8-sig', errors='replace')
    return {'id': attachment_id, 'filename': clip(a.get('filename'),150),
            'previewable': True, 'bytes': size, 'content': clip(raw, 200000),
            'notice': 'Best-effort sanitization only; inspect before copying externally.'}


class RecoveryRequest(BaseModel):
    board: str = Field(max_length=100)
    task_id: str = Field(max_length=100)


class ApplyRequest(BaseModel):
    token: str = Field(max_length=80)
    confirm: str = Field(max_length=32)


@router.get("/inventory")
async def inventory():
    return await asyncio.to_thread(boards_profiles)


@router.get("/tasks")
async def list_tasks(board: str = "default"):
    return {"items": await asyncio.to_thread(tasks, board)}


@router.get("/sessions")
async def list_sessions(profile: str = "default"):
    return {"items": await asyncio.to_thread(sessions, profile)}


@router.get("/timeline")
async def timeline(mode: str = "board", board: str = "default", task_id: str = "", profile: str = "default", session_id: str = ""):
    if mode == "board":
        data = await asyncio.to_thread(board_timeline, board, task_id)
        inf = data.pop("info")
        steps = data["timeline"]
        p = inf["profile"]
        if inf["session_id"] and p and state_db(p).is_file():
            sub = await asyncio.to_thread(timeline_profile, p, inf["session_id"], _LIMIT)
            steps.extend(sub["timeline"])
        else:
            sub = {"total": 0, "executions": [], "notice": "No session linked to this task"}
        steps.sort(key=lambda x: x["time"])
        return {"timeline": steps[-400:], "total_messages": sub["total"], "executions": sub["executions"],
                "profile": p, "session_id": inf["session_id"], "task": {"title": clip(inf["task"].get("title"), 130), "status": inf["task"].get("status")}}
    if mode == "profile":
        data = await asyncio.to_thread(timeline_profile, profile, session_id, _LIMIT)
        return {"timeline": data["timeline"], "total_messages": data["total"], "executions": data["executions"], "profile": profile, "session_id": session_id}
    raise HTTPException(400, "Invalid mode")


@router.get("/diagnose")
async def get_diagnosis(board: str = "default", task_id: str = ""):
    return await asyncio.to_thread(diagnose, board, task_id)


@router.get("/stack")
async def get_stack(pid: int):
    return await asyncio.to_thread(stack, pid)


@router.post("/recover/preview")
async def get_recover_preview(req: RecoveryRequest):
    return await asyncio.to_thread(recovery_preview, req.board, req.task_id)


@router.post("/recover/apply")
async def apply_recovery(req: ApplyRequest):
    return await asyncio.to_thread(recovery_apply, req.token, req.confirm)


@router.get("/audit")
async def audit_endpoint(board: str = "default", task_id: str = ""):
    return await asyncio.to_thread(audit_task, board, task_id)


@router.get("/artifact")
async def artifact_endpoint(board: str = "default", task_id: str = "", attachment_id: int = 0):
    return await asyncio.to_thread(artifact_preview, board, task_id, attachment_id)
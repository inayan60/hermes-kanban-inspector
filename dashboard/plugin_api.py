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
        return Path(explicit).expanduser().resolve()
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
    return {"boards": sorted(set(boards)), "profiles": sorted(set(profiles)), "os": os.name, "home": str(h), "py_spy": bool(find_pyspy()), "version": "0.1.0"}


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
        if current in seen or current not in p:
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
        if pid in anchor or any(tree_relation(p, pid, a) for a in anchor if a):
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
    if gateway_pid in pm and gateway_pid not in {q["pid"] for q in related}:
        g = pm[gateway_pid]
        related.insert(0, {"pid": gateway_pid, "ppid": g.get("ParentProcessId"), "name": g.get("Name"),
                           "command": clip(g.get("CommandLine"), 260), "created": str(g.get("CreationDate") or "")})
    git = [x for x in related if str(x["name"]).lower() == "git.exe"]
    other_git = [x for x in all_p if str(x.get("Name", "")).lower() == "git.exe" and int(x.get("ProcessId") or 0) not in {x["pid"] for x in git}]
    elapsed = max(0, int(time.time()) - int(info["runs"][0].get("started_at") or info["task"].get("started_at") or time.time())) if info["runs"] else 0
    no_steps = ll.get("total") == 0
    stale = elapsed > 300 and no_steps and info["task"].get("status") == "running"
    return {"board": board, "task_id": task_id, "profile": profile, "session_id": sid,
            "status": info["task"].get("status"), "run_id": info["runs"][0].get("id") if info["runs"] else None,
            "worker_pid": worker_pid, "managed_pid": managed_pid, "gateway_pid": gateway_pid, "worker_alive": worker_pid in pm if all_p else None,
            "last_heartbeat_at": dt(info["runs"][0].get("last_heartbeat_at")) if info["runs"] else "",
            "elapsed_seconds": elapsed, "message_count": ll.get("total"), "worker_executions": ll.get("executions", []),
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
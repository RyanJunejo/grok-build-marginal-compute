"""State features at segment boundaries, from grok session updates.jsonl.

Calibrated against real grok 1.0.5 sessions (2026-08-25). Event envelope:
  {"method": "session/update", "params": {"update": {
      "sessionUpdate": "tool_call" | "tool_call_update" | "agent_thought_chunk"
                       | "user_message_chunk" | "turn_completed",
      "toolCallId": ..., "rawInput": {...}, "locations": [{"path": ...}],
      "_meta": {"x.ai/tool": {"name": ..., "kind": ..., "read_only": bool}}}},
   "timestamp": ...}

Tool identity comes from _meta["x.ai/tool"] (name/kind/read_only). Test-failure
detection applies regexes to the raw line text of tool_call_update events for
shell commands, since the output payload shape varies by tool.
"""

import json
import re
from pathlib import Path

TEST_CMD_RE = re.compile(
    r"\b(pytest|py\.test|python\s+-m\s+pytest|python\s+-m\s+unittest|runtests\.py|tox\b|"
    r"bin/test|manage\.py\s+test|django\s+test)\b"
)
FAIL_TEXT_RE = re.compile(
    r"(\b\d+\s+failed\b|\bFAILED\b|\bERRORS?\b|FAILED \((failures|errors)=|"
    r"Traceback \(most recent call last\)|exit code [1-9]|exit_code[\"']?: [1-9])",
    re.I,
)
PASS_TEXT_RE = re.compile(r"(\b\d+\s+passed\b|(?<![A-Za-z])OK(?![A-Za-z])|\bPASSED\b)")
SHELL_NAMES = {"run_terminal_command", "run_terminal_cmd", "terminal", "shell", "bash"}
EDIT_NAME_RE = re.compile(r"(search_replace|edit|write|create_file|apply_patch|str_replace|delete)", re.I)


def _tool_meta(update):
    return (update.get("_meta") or {}).get("x.ai/tool") or {}


def _edit_path(update):
    ri = update.get("rawInput") or {}
    for key in ("file_path", "target_file", "path", "filePath"):
        if isinstance(ri.get(key), str):
            return ri[key]
    locs = update.get("locations") or []
    if locs and isinstance(locs[0], dict) and locs[0].get("path"):
        return locs[0]["path"]
    return None


def _command(update):
    ri = update.get("rawInput") or {}
    for key in ("command", "cmd"):
        if isinstance(ri.get(key), str):
            return ri[key]
    return None


def _content_text(update):
    texts = []
    for c in update.get("content") or []:
        inner = c.get("content") if isinstance(c, dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            texts.append(inner["text"])
    return "\n".join(texts)


def extract_events(updates_path, upto_bytes=None):
    """One summary dict per COMPLETED tool call.

    Protocol (observed on 1.0.5): tool_call opens (has _meta + rawInput);
    tool_call_update with no `status` is an in-progress detail update (has
    _meta, refines rawInput); tool_call_update with status completed/failed is
    the result — it carries the output in `content` blocks but has NO _meta,
    so call identity must be inherited via toolCallId.
    """
    raw = Path(updates_path).read_bytes()
    if upto_bytes is not None:
        raw = raw[:upto_bytes]
    calls = {}  # toolCallId -> {name, kind, read_only, command, path}
    events = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        u = (obj.get("params") or {}).get("update") or {}
        su = u.get("sessionUpdate")
        if su not in ("tool_call", "tool_call_update"):
            continue
        meta = _tool_meta(u)
        cid = u.get("toolCallId")
        if su == "tool_call" or u.get("status") is None:
            info = calls.setdefault(cid, {})
            if meta.get("name"):
                info["name"] = meta["name"]
                info["kind"] = meta.get("kind")
                info["read_only"] = meta.get("read_only", True)
            info["command"] = _command(u) or info.get("command")
            info["path"] = _edit_path(u) or info.get("path")
            continue
        # completion event (status: completed | failed)
        call = calls.get(cid, {})
        name = call.get("name") or ""
        command = call.get("command")
        path = call.get("path")
        kind = call.get("kind")
        failed_status = u.get("status") == "failed"
        is_shell = kind == "execute" or name in SHELL_NAMES
        is_edit = (kind == "edit") or (
            not call.get("read_only", True) and not is_shell and path is not None
            and bool(EDIT_NAME_RE.search(name))
        )
        is_test = is_shell and bool(command and TEST_CMD_RE.search(command))
        text = _content_text(u)
        events.append({
            "tool": name,
            "status": u.get("status"),
            "is_shell": is_shell,
            "is_edit": is_edit,
            "edit_path": path if is_edit else None,
            "command": command if is_shell else None,
            "is_test_run": is_test,
            "saw_fail": (failed_status or bool(FAIL_TEXT_RE.search(text))) if is_shell else False,
            "saw_pass": bool(PASS_TEXT_RE.search(text)) if is_shell else False,
        })
    return events


def _is_verification(ev):
    """Outcome-bearing execution: a formal test command, or any shell command
    whose output shows pass/fail evidence (agents often verify with `python -c`
    repro scripts rather than the test suite — a failing repro is the signal)."""
    return ev["is_test_run"] or (ev["is_shell"] and (ev["saw_fail"] or ev["saw_pass"]))


def _summarize(events):
    test_runs = test_failures = 0
    edits = {}
    last_test_failed = None
    failure_streak = 0
    for ev in events:
        if ev["is_edit"]:
            edits[ev["edit_path"]] = edits.get(ev["edit_path"], 0) + 1
        if _is_verification(ev):
            test_runs += 1
            failed = ev["saw_fail"]
            if failed:
                test_failures += 1
                failure_streak += 1
            else:
                failure_streak = 0
            last_test_failed = failed
    return {
        "test_runs": test_runs,
        "test_failures": test_failures,
        "failure_streak": failure_streak,
        "last_test_failed": last_test_failed,
        "edits": sum(edits.values()),
        "files_edited": len(edits),
        "same_file_repeat_edits": max(edits.values(), default=0),
    }


def state_at_segments(row):
    """Cumulative state at each segment boundary of a fork_source run.

    signal (pre-registered): the most recent test execution as of this boundary
    failed, OR >=2 edits landed on one file within this segment with no passing
    test run inside the segment.
    """
    if not row.get("updates_path") or not Path(row["updates_path"]).exists():
        return []
    states = []
    prev_count = 0
    for seg in row.get("segments", []):
        upto = seg.get("updates_bytes")
        if upto is None:
            continue
        cum_events = extract_events(row["updates_path"], upto)
        cum = _summarize(cum_events)
        seg_events = cum_events[prev_count:]
        win = _summarize(seg_events)
        signal = bool(cum["last_test_failed"]) or (
            win["same_file_repeat_edits"] >= 2 and not any(
                _is_verification(e) and e["saw_pass"] and not e["saw_fail"]
                for e in seg_events
            )
        )
        states.append({
            "segment": seg["segment"],
            "stop_reason": seg.get("stop_reason"),
            "num_turns_cum": seg.get("num_turns_cum"),
            "output_tokens_cum": seg.get("output_tokens_cum"),
            **cum,
            "window_edits": win["edits"],
            "window_test_runs": win["test_runs"],
            "signal": signal,
        })
        prev_count = len(cum_events)
    return states


def debug_events(updates_path, limit=80):
    for i, ev in enumerate(extract_events(updates_path)[:limit]):
        flags = [k for k in ("is_shell", "is_edit", "is_test_run", "saw_fail", "saw_pass") if ev[k]]
        extra = ev.get("edit_path") or (ev.get("command") or "")[:70]
        print(f"{i:3d} {ev['tool']:20s} {','.join(flags):30s} {extra}")


if __name__ == "__main__":
    import sys

    debug_events(sys.argv[1])

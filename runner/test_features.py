"""Regression tests for the verification classifier.

Run: .venv/bin/python runner/test_features.py
Covers the audit finding that read-only shell commands (git log, grep) whose
output quotes 'FAILED'/'error' were classified as failed verifications, and
locks in known-good behavior on a real recorded session.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def synth_line(cid, kind, command, output_text, status="completed"):
    """One tool_call line + one completion line for a shell command."""
    call = {"method": "session/update", "params": {"update": {
        "sessionUpdate": "tool_call", "toolCallId": cid,
        "rawInput": {"command": command},
        "_meta": {"x.ai/tool": {"name": "run_terminal_command", "kind": kind,
                                "read_only": False}}}}}
    done = {"method": "session/update", "params": {"update": {
        "sessionUpdate": "tool_call_update", "toolCallId": cid, "status": status,
        "content": [{"type": "content", "content": {"type": "text", "text": output_text}}]}}}
    return json.dumps(call) + "\n" + json.dumps(done) + "\n"


def synth_file(cases):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                    dir=str(ROOT / "results"))
    for i, (kind, cmd, out, status) in enumerate(cases):
        f.write(synth_line(f"c{i}", kind, cmd, out, status))
    f.close()
    return f.name


def main():
    failures = []

    def check(name, cond):
        (print(f"  ok   {name}") if cond else failures.append(name)) or (
            cond or print(f"  FAIL {name}"))

    # --- synthetic: the git-log false positive and friends ---
    path = synth_file([
        ("execute", "cd /testbed; git log --oneline -S foo | head -20",
         "abc123 Fix FAILED test in bar\ndef456 error handling MERGED", "completed"),
        ("execute", "grep -rn 'FAILED' tests/", "tests/x.py:12: FAILED marker", "completed"),
        ("execute", "cat runtests_output.txt", "3 failed, 2 passed", "completed"),
        ("execute", "cd /testbed; python -c 'import x; x.check()'",
         "Traceback (most recent call last):\n  ValueError", "completed"),
        ("execute", "python -m pytest tests/test_a.py", "2 passed", "completed"),
        ("execute", "ls /nonexistent", "", "failed"),
        ("execute", "cd /testbed && ./tests/runtests.py app.Tests", "FAILED (failures=1)", "completed"),
    ])
    evs = features.extract_events(path)
    Path(path).unlink()
    v = [features._is_verification(e) for e in evs]
    check("git log quoting FAILED is NOT a verification", v[0] is False)
    check("grep hit on FAILED is NOT a verification", v[1] is False)
    check("cat of an output file is NOT a verification", v[2] is False)
    check("failing python -c repro IS a failed verification", v[3] and evs[3]["saw_fail"])
    check("passing pytest IS a passing verification", v[4] and evs[4]["saw_pass"] and not evs[4]["saw_fail"])
    check("failed ls (status=failed, read-only-ish) is NOT a verification", v[5] is False)
    check("django runtests failure IS a failed verification", v[6] and evs[6]["saw_fail"])

    # --- real recorded session: lock in the known-good django-11999 story ---
    real = ROOT / ("results/sessions/django__django-11999__medium__r1__fork_source__9e178a82"
                   "/sessions/%2Ftestbed/98a0ff8d-a497-4717-b993-4ce61e581186/updates.jsonl")
    if real.exists():
        row = {"updates_path": str(real), "segments": [
            {"segment": 1, "updates_bytes": None}]}
        evs = features.extract_events(str(real))
        gitlogs = [e for e in evs if e["is_shell"] and e["command"] and "git log" in e["command"]]
        check("real session: git log events exist to test against", len(gitlogs) >= 1)
        check("real session: no git log event counts as verification",
              not any(features._is_verification(e) for e in gitlogs))
        repro = [e for e in evs if e["is_shell"] and e["command"] and "python -c" in e["command"]]
        check("real session: python -c repro events detected", len(repro) >= 1)
        check("real session: at least one failing repro verification",
              any(features._is_verification(e) and e["saw_fail"] for e in repro))
    else:
        print("  skip real-session checks (session file not present)")

    if failures:
        print(f"\n{len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("\nall regression checks passed")


if __name__ == "__main__":
    main()

"""Run one Grok Build agent attempt against one SWE-bench Verified instance.

One invocation = one runs.jsonl row. Two phases:
  static       one headless grok call at a fixed effort (Layer 1 arms)
  fork_source  segmented run (--max-turns per segment, resume between segments)
               with a `docker commit` snapshot after every segment (Layer 3 source)

Single-variable discipline: --no-subagents always; same model, prompt, and turn
budget everywhere; only --effort varies between arms.
"""

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROK_LINUX_BIN = ROOT / "bin" / "grok-1.0.5-linux-x86_64"
GROK_VERSION = "1.0.5"
RUNS_JSONL = ROOT / "results" / "runs.jsonl"

CONTAINER_PATH = (
    "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
EFFORT_WARNING = "does not support reasoning effort"
TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*$|_test\.py$|(^|/)conftest\.py$")


def sh(args, timeout=None, check=True):
    """Run a host command, capturing output."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=check)


def load_manifest():
    return json.loads((ROOT / "tasks" / "manifest.json").read_text())


def load_env_key():
    """XAI_API_KEY from the environment, else from ROOT/.env (never printed)."""
    import os

    if os.environ.get("XAI_API_KEY"):
        return os.environ["XAI_API_KEY"]
    envfile = ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            line = line.strip()
            if line.startswith("XAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("FATAL: XAI_API_KEY not set (export it or put XAI_API_KEY=... in fastmodel/.env)")


def append_run_row(row):
    RUNS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_JSONL, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)


class TaskContainer:
    """A kept-alive amd64 eval container with the grok binary installed."""

    def __init__(self, image, host_grok_dir, api_key, name=None, from_snapshot=False):
        self.image = image
        self.name = name or f"ag_{uuid.uuid4().hex[:12]}"
        self.host_grok_dir = Path(host_grok_dir)
        self.api_key = api_key
        self.from_snapshot = from_snapshot

    def start(self):
        self.host_grok_dir.mkdir(parents=True, exist_ok=True)
        sh([
            "docker", "run", "-d", "--name", self.name, "--platform", "linux/amd64",
            "-v", f"{self.host_grok_dir}:/root/.grok",
            self.image, "tail", "-f", "/dev/null",
        ])
        if not self.from_snapshot:  # snapshots already contain the binary
            sh(["docker", "cp", str(GROK_LINUX_BIN), f"{self.name}:/usr/local/bin/grok"])
            sh(["docker", "exec", self.name, "chmod", "+x", "/usr/local/bin/grok"])
        return self

    def put_file(self, host_path, container_path):
        sh(["docker", "cp", str(host_path), f"{self.name}:{container_path}"])

    def exec(self, cmd, timeout=None, workdir="/testbed", extra_env=None):
        """docker exec with the testbed PATH and API key. cmd is a list."""
        env_args = [
            "-e", f"XAI_API_KEY={self.api_key}",
            "-e", "GROK_DISABLE_AUTOUPDATER=1",
            "-e", f"PATH={CONTAINER_PATH}",
        ]
        for k, v in (extra_env or {}).items():
            env_args += ["-e", f"{k}={v}"]
        return sh(
            ["docker", "exec", "-w", workdir, *env_args, self.name, *cmd],
            timeout=timeout, check=False,
        )

    def grok(self, grok_args, timeout):
        return self.exec(["grok", *grok_args], timeout=timeout)

    def commit(self, tag):
        sh(["docker", "commit", self.name, tag])
        return tag

    def extract_patch(self, base_commit):
        r = self.exec(
            ["sh", "-c",
             f"cd /testbed && git add -A >/dev/null 2>&1; "
             f"git -c core.fileMode=false diff --cached {base_commit}"],
            timeout=300,
        )
        return r.stdout

    def remove(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


def grok_headless_args(effort, model, max_turns, session_flag, prompt_source):
    """Shared flag set. Only `effort` ever differs between experiment arms."""
    return [
        *prompt_source,           # ["--prompt-file", "/tmp/prompt.md"] or ["-p", "..."]
        *session_flag,            # ["-s", sid] | ["-r", sid] | ["-r", sid, "--fork-session", "-s", child]
        "--cwd", "/testbed",
        "--yolo", "--no-auto-update", "--no-subagents",
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--effort", effort,
        "-m", model,
    ]


def parse_grok_json(stdout):
    """The headless JSON object is the last JSON value on stdout."""
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # tolerate stray leading lines: take from the first '{'
        idx = stdout.find("{")
        if idx >= 0:
            try:
                return json.loads(stdout[idx:])
            except json.JSONDecodeError:
                return None
    return None


def accumulate_usage(total, usage):
    if not usage:
        return total
    for k, v in usage.items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    return total


def touched_test_files(patch_text):
    files = re.findall(r"^diff --git a/(\S+) b/", patch_text, flags=re.M)
    return [f for f in files if TEST_PATH_RE.search(f)]


def run_one(instance_id, effort, rep, phase, model, max_turns, segment_turns,
            snapshot=None, resume_session=None, branch=None, checkpoint_id=None,
            tag_extra="", timeout_static=5400, timeout_segment=2400,
            layer=None, snapshots=True):
    """Execute one run; returns the runs.jsonl row (already appended)."""
    manifest = load_manifest()
    inst = manifest["instances"][instance_id]
    api_key = load_env_key()

    run_id = f"{instance_id}__{effort}__r{rep}__{phase}{tag_extra}__{uuid.uuid4().hex[:8]}"
    sessions_dir = ROOT / "results" / "sessions" / run_id
    events_dir = ROOT / "results" / "events" / run_id
    events_dir.mkdir(parents=True, exist_ok=True)

    image = snapshot or inst["image"]
    # Branch containers reuse the fork-source's session dir so `-r <sid>` resolves.
    if resume_session:
        sessions_dir = Path(resume_session["host_grok_dir"])
    ctr = TaskContainer(image, sessions_dir, api_key, from_snapshot=bool(snapshot))

    row = {
        "run_id": run_id, "task_id": instance_id, "layer": layer, "effort": effort,
        "rep": rep, "phase": phase, "branch": branch, "checkpoint_id": checkpoint_id,
        "session_id": None, "parent_session": (resume_session or {}).get("session_id"),
        "segment": None, "model": model, "grok_version": GROK_VERSION, "max_turns": max_turns,
        "image": image, "difficulty": inst["difficulty"],
        "started_at": datetime.now(timezone.utc).isoformat(), "wall_s": None,
        "num_turns": 0, "stop_reason": None, "usage": {}, "cost_usd": 0.0,
        "usage_flags": [], "test_file_edits": [], "status": "ok",
        "patch_path": None, "resolved": None, "grade_run_id": None,
        "snapshots": [], "segments": [], "updates_path": None,
    }

    t0 = time.time()
    try:
        ctr.start()
        prompt_file = ROOT / "tasks" / "prompts" / f"{instance_id}.md"
        ctr.put_file(prompt_file, "/tmp/prompt.md")

        if phase == "static":
            segments = [(1, ["--prompt-file", "/tmp/prompt.md"], ["-s", str(uuid.uuid4())], max_turns)]
        elif phase == "fork_source":
            segments = None  # built iteratively below
        elif phase == "fork_branch":
            child_sid = str(uuid.uuid4())
            segments = [(1, ["-p", "Continue working on the task."],
                         ["-r", resume_session["session_id"], "--fork-session", "-s", child_sid],
                         max_turns)]
        else:
            raise ValueError(phase)

        def run_segment(seg_idx, prompt_source, session_flag, turns, timeout):
            args = grok_headless_args(effort, model, turns, session_flag, prompt_source)
            r = ctr.grok(args, timeout=timeout)
            (events_dir / f"seg{seg_idx}.stdout.json").write_text(r.stdout)
            (events_dir / f"seg{seg_idx}.stderr.log").write_text(r.stderr)
            if EFFORT_WARNING in r.stderr:
                row["status"] = "error_effort"
            out = parse_grok_json(r.stdout)
            if out is None:
                row["status"] = "error"
                return None
            row["session_id"] = row["session_id"] or out.get("sessionId")
            row["num_turns"] += out.get("num_turns") or 0
            accumulate_usage(row["usage"], out.get("usage"))
            row["cost_usd"] += out.get("total_cost_usd") or out.get("costUSD") or 0.0
            if out.get("usage_is_incomplete") or out.get("cost_is_partial"):
                row["usage_flags"].append(f"seg{seg_idx}_incomplete")
            row["stop_reason"] = out.get("stopReason")
            return out

        if phase in ("static", "fork_branch"):
            seg_idx, prompt_source, session_flag, turns = segments[0]
            timeout = timeout_static
            run_segment(seg_idx, prompt_source, session_flag, turns, timeout)
        else:  # fork_source: segment loop with snapshots
            sid = str(uuid.uuid4())
            max_segments = max(1, max_turns // segment_turns)
            for k in range(1, max_segments + 1):
                if k == 1:
                    src, sess = ["--prompt-file", "/tmp/prompt.md"], ["-s", sid]
                else:
                    src, sess = ["-p", "Continue working on the task."], ["-r", sid]
                out = run_segment(k, src, sess, segment_turns, timeout_segment)
                row["segment"] = k
                if row["updates_path"] is None:
                    hits = list(ctr.host_grok_dir.glob(f"sessions/*/{sid}/updates.jsonl"))
                    if hits:
                        row["updates_path"] = str(hits[0])
                row["segments"].append({
                    "segment": k,
                    "stop_reason": row["stop_reason"],
                    "num_turns_cum": row["num_turns"],
                    "output_tokens_cum": row["usage"].get("output_tokens"),
                    "updates_bytes": (Path(row["updates_path"]).stat().st_size
                                      if row["updates_path"] else None),
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                })
                if out is None or row["status"] != "ok":
                    break
                if snapshots:
                    tag = f"snap_{run_id.split('__')[-1]}:seg{k}"
                    for attempt in (1, 2):
                        try:
                            ctr.commit(tag)
                            row["snapshots"].append({"segment": k, "image": tag})
                            break
                        except subprocess.CalledProcessError:
                            if attempt == 2:  # snapshot lost; run stays valid for L1
                                row["usage_flags"].append(f"seg{k}_snapshot_failed")
                            else:
                                time.sleep(10)
                # Observed on 1.0.5: a --max-turns stop reports stopReason
                # "cancelled" (not the documented "max_turn_requests"); both
                # mean "budget exhausted, keep segmenting".
                if row["stop_reason"] not in ("max_turn_requests", "cancelled"):
                    break  # end_turn (finished), refusal, or anything unexpected

        patch = ctr.extract_patch(inst["base_commit"])
        if patch.strip():
            patch_path = ROOT / "results" / "preds" / f"{run_id}.diff"
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(patch)
            row["patch_path"] = str(patch_path.relative_to(ROOT))
            row["test_file_edits"] = touched_test_files(patch)
    except subprocess.TimeoutExpired:
        row["status"] = "error_timeout"
    except Exception as e:  # noqa: BLE001 — record, never crash the sweep
        row["status"] = "error"
        row["error"] = repr(e)
    finally:
        row["wall_s"] = round(time.time() - t0, 1)
        row["host_grok_dir"] = str(ctr.host_grok_dir)
        ctr.remove()
        append_run_row(row)

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--effort", required=True)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--phase", default="static", choices=["static", "fork_source"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--segment-turns", type=int, default=8)
    ap.add_argument("--layer", default=None)
    ap.add_argument("--no-snapshots", action="store_true")
    args = ap.parse_args()

    row = run_one(args.instance, args.effort, args.rep, args.phase, args.model,
                  args.max_turns, args.segment_turns,
                  layer=args.layer, snapshots=not args.no_snapshots)
    print(json.dumps({k: row[k] for k in
                      ("run_id", "status", "stop_reason", "num_turns", "cost_usd", "wall_s",
                       "patch_path", "session_id", "snapshots")}, indent=2))


if __name__ == "__main__":
    main()

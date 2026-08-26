"""Materialize tasks/manifest.json + per-task prompt files from SWE-bench_Verified.

The instance lists are frozen here (difficulty verified against the dataset's
`difficulty` column on 2026-08-25). Never swap instances after seeing results.
"""

import json
import sys
from pathlib import Path

from datasets import load_dataset

ROOT = Path(__file__).resolve().parent.parent
DATASET = "SWE-bench/SWE-bench_Verified"

FIRST_WAVE = {
    "django__django-10914": "<15 min fix",
    "psf__requests-1921": "<15 min fix",
    "django__django-11999": "15 min - 1 hour",
    "django__django-13590": "15 min - 1 hour",
    "sympy__sympy-20590": "15 min - 1 hour",
    "sphinx-doc__sphinx-8551": "15 min - 1 hour",
    "django__django-11400": "1-4 hours",
    "pytest-dev__pytest-6197": "1-4 hours",
}

SCALE_POOL = {
    "pallets__flask-5014": "<15 min fix",
    "sympy__sympy-13480": "<15 min fix",
    "pytest-dev__pytest-5262": "<15 min fix",
    "pydata__xarray-4094": "<15 min fix",
    "matplotlib__matplotlib-23299": "15 min - 1 hour",
    "scikit-learn__scikit-learn-14894": "15 min - 1 hour",
    "astropy__astropy-14365": "15 min - 1 hour",
    "mwaskom__seaborn-3069": "15 min - 1 hour",
    "django__django-15128": "1-4 hours",
    "sympy__sympy-18199": "1-4 hours",
    "pylint-dev__pylint-4551": "1-4 hours",
    "pydata__xarray-3993": "1-4 hours",
}

PROMPT_TEMPLATE = """You are working in /testbed, a checkout of {repo} at a fixed commit with its \
test environment already installed (conda env `testbed`). If `python` or `pytest` is not on PATH, \
use /opt/miniconda3/envs/testbed/bin/python or run `source /opt/miniconda3/bin/activate && conda activate testbed` first.

Fix the issue described below.

Requirements:
- Modify the repository source code to resolve the issue.
- Run relevant existing tests to verify your fix before finishing.
- Do NOT modify any test files.

<issue>
{problem_statement}
</issue>
"""


def main() -> None:
    wanted = {**FIRST_WAVE, **SCALE_POOL}
    ds = load_dataset(DATASET, split="test")
    rows = {r["instance_id"]: r for r in ds if r["instance_id"] in wanted}

    missing = sorted(set(wanted) - set(rows))
    if missing:
        sys.exit(f"FATAL: instances not found in {DATASET}: {missing}")

    mismatched = {
        iid: (wanted[iid], rows[iid]["difficulty"])
        for iid in wanted
        if rows[iid]["difficulty"] != wanted[iid]
    }
    if mismatched:
        sys.exit(f"FATAL: difficulty mismatch (expected, actual): {mismatched}")

    prompts_dir = ROOT / "tasks" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    instances = {}
    for iid, row in rows.items():
        instances[iid] = {
            "repo": row["repo"],
            "difficulty": row["difficulty"],
            "image": row["image"],
            "base_commit": row["base_commit"],
            "wave": "first" if iid in FIRST_WAVE else "scale",
        }
        prompt = PROMPT_TEMPLATE.format(
            repo=row["repo"], problem_statement=row["problem_statement"].strip()
        )
        (prompts_dir / f"{iid}.md").write_text(prompt)

    manifest = {
        "dataset": DATASET,
        "frozen_at": "2026-08-25",
        "prompt_template_sha": __import__("hashlib").sha256(PROMPT_TEMPLATE.encode()).hexdigest()[:12],
        "first_wave": sorted(FIRST_WAVE),
        "scale_pool": sorted(SCALE_POOL),
        "instances": dict(sorted(instances.items())),
    }
    out = ROOT / "tasks" / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out} ({len(instances)} instances, {len(FIRST_WAVE)} first-wave)")
    for iid in sorted(FIRST_WAVE):
        print(f"  {iid:45s} {instances[iid]['difficulty']:18s} {instances[iid]['image']}")


if __name__ == "__main__":
    main()

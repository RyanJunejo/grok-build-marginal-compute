You are working in /testbed, a checkout of pallets/flask at a fixed commit with its test environment already installed (conda env `testbed`). If `python` or `pytest` is not on PATH, use /opt/miniconda3/envs/testbed/bin/python or run `source /opt/miniconda3/bin/activate && conda activate testbed` first.

Fix the issue described below.

Requirements:
- Modify the repository source code to resolve the issue.
- Run relevant existing tests to verify your fix before finishing.
- Do NOT modify any test files.

<issue>
Require a non-empty name for Blueprints
Things do not work correctly if a Blueprint is given an empty name (e.g. #4944).
It would be helpful if a `ValueError` was raised when trying to do that.
</issue>

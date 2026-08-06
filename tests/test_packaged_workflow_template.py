"""Guards against the workflow template being excluded from the built package.

`tomax init`'s dashboard registration reads this file from inside the
installed package at runtime — a user who installed via pip/uv from a
built wheel has no `templates/` source checkout to fall back on, so if
packaging silently drops this file, dashboard registration breaks for
every non-source install.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path


def test_workflow_template_is_included_in_the_built_wheel(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "dist"
    subprocess.run(
        [shutil.which("uv"), "build", "--wheel", "-o", str(out_dir)],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    wheel_path = next(out_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
    assert "tomax/templates/github-workflow.yml" in names

"""The Docker image ships only a few files of the desktop package (see server/Dockerfile).

Importing the server with just those files must work, or the container crashes at start-up.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _shipped_desktop_files() -> list[str]:
    dockerfile = (REPO / "server" / "Dockerfile").read_text(encoding="utf-8")
    line = next(l for l in dockerfile.splitlines() if l.startswith("COPY mamarmiton/"))
    return re.findall(r"mamarmiton/[\w.]+\.py", line)


def test_dockerignore_lets_every_copied_file_through():
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8")
    for name in _shipped_desktop_files():
        assert f"!{name}" in ignore, f"{name} is copied by the Dockerfile but excluded by .dockerignore"


def test_server_imports_with_only_the_shipped_desktop_files(tmp_path):
    pkg = tmp_path / "mamarmiton"
    pkg.mkdir()
    for name in _shipped_desktop_files():
        shutil.copy(REPO / name, pkg / Path(name).name)
    code = ("import mamarmiton, mamarmiton_server.api, mamarmiton_server.__main__;"
            f"assert mamarmiton.__file__.startswith({str(tmp_path)!r}), mamarmiton.__file__")
    env = {"PYTHONPATH": f"{tmp_path}:{REPO / 'server'}", "PATH": "/usr/bin:/bin"}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

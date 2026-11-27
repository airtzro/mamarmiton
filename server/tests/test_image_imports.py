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
    lines = [l for l in dockerfile.splitlines() if l.startswith("COPY mamarmiton/")]
    assert lines
    return [name for line in lines for name in re.findall(r"mamarmiton/[\w./]+\.py", line)]


def test_dockerignore_lets_every_copied_file_through():
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8")
    for name in _shipped_desktop_files():
        assert f"!{name}" in ignore, f"{name} is copied by the Dockerfile but excluded by .dockerignore"


def test_dockerfile_ships_the_importers_the_server_uses():
    shipped = set(_shipped_desktop_files())
    for name in ("documents", "heuristic", "pdf_text", "llm", "pipeline", "web"):
        assert f"mamarmiton/importers/{name}.py" in shipped


def test_server_imports_with_only_the_shipped_desktop_files(tmp_path):
    for name in _shipped_desktop_files():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, target)
    # an editable install of the desktop package would quietly fill in missing modules: drop its finder
    code = ("import sys;"
            "sys.meta_path[:] = [f for f in sys.meta_path if 'editable___mamarmiton_4' not in type(f).__module__];"
            "import mamarmiton, mamarmiton_server.api, mamarmiton_server.importer, mamarmiton_server.__main__;"
            "import mamarmiton.importers.pipeline as p;"
            f"assert mamarmiton.__file__.startswith({str(tmp_path)!r}), mamarmiton.__file__;"
            f"assert p.__file__.startswith({str(tmp_path)!r}), p.__file__")
    env = {"PYTHONPATH": f"{tmp_path}:{REPO / 'server'}", "PATH": "/usr/bin:/bin"}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

"""The check reads the repository and never runs it: how it starts Python."""
import ast
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wingbox_pr as pr  # noqa: E402


class SafeStartTest(unittest.TestCase):
    def test_a_folder_of_the_repository_named_like_wingbox_is_not_run(self) -> None:
        """/* REQ-ACTION-084: a Wingbox command the check starts in a repository that holds a folder `wingbox/` (or `pip/`) runs the installed Wingbox or fails, and never a file of that folder: the folder's files leave no mark */"""
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "repo"
            for relative in ("wingbox/__init__.py", "wingbox/__main__.py", "wingbox/cli.py", "pip/__init__.py", "pip/__main__.py"):
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("import os\nopen(os.path.join(%r, 'RAN-' + %r), 'w').close()\n" % (folder, relative.replace("/", "-")),
                                encoding="utf-8")
            errors = list(pr.TOOL_ERRORS)
            try:
                pr._wingbox(str(repo), "--version")
            finally:
                pr.TOOL_ERRORS[:] = errors
            self.assertEqual([], sorted(name for name in os.listdir(folder) if name.startswith("RAN-")))

    def test_every_start_of_python_leaves_the_starting_folder_out(self) -> None:
        """/* REQ-ACTION-085: every `python -m` of the scripts, of action.yml and of the GitLab templates has -P before it, so the folder it starts in (the checked repository, the workspace) is not on the module search path */"""
        offenders = []
        for path in sorted(ROOT.glob("*.py")) + sorted((ROOT / "scripts").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                    continue
                words = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in node.elts]
                if words[0] is None and "-m" in words and "-P" not in words[: words.index("-m")]:
                    offenders.append(f"{path.name}:{node.lineno}")
        for path in [ROOT / "action.yml", *sorted((ROOT / "gitlab").glob("*.yml")), *sorted((ROOT / "examples").rglob("*.yml"))]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                for before in re.findall(r"(\S+)\s+-m\s+(?:pip|venv|wingbox)\b", line):
                    if before != "-P":
                        offenders.append(f"{path.name}:{number}")
        self.assertEqual([], offenders)
        self.assertIn('"$WINGBOX_PYTHON" -P -m pip install', (ROOT / "action.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

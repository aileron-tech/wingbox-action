"""A TypeScript project in four layers, as text, with the changes the policy tests make to it.

The base holds `src/routes` (API), `src/services`, `src/models` (domain) and `src/repositories` (infrastructure),
fourteen modules that keep the rules of a layered style: Wingbox finds the style itself, there is no
`.wingbox/` folder. BREAK makes a domain module import the API layer. The other texts are what a change can
add to accept that break itself: a baseline entry without terms, one with an owner, a reason and an end
date, and a `.wingbox/config.yaml` whose layers no longer hold the API folder.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BASE = {
    "package.json": '{"name": "shop"}\n',
    "src/routes/routes0.ts": "import { services0 } from '../services/services0';\nexport const routes0 = services0;\n",
    "src/services/services0.ts": "import { models0 } from '../models/models0';\nexport const services0 = models0;\n",
    **{f"src/{layer}/{layer}{index}.ts": f"export const {layer}{index} = {index};\n"
       for layer, numbers in (("routes", (1, 2, 3)), ("services", (1, 2, 3)), ("models", (0, 1, 2)), ("repositories", (0, 1, 2)))
       for index in numbers},
}
BREAK = {"src/models/models0.ts": "import { routes1 } from '../routes/routes1';\nexport const models0 = routes1;\n"}
UNRELATED = {"src/services/services1.ts": "export const services1 = 10;\n"}
ENTRY = '(baseline "." src/models/models0 src/routes/routes1 :rule "domain -> api" :from align'
TERMS = ' :owner "lead" :reason "moves with the next release" :expires 2099-01-01'
BASELINE = ".wingbox/baseline.sexp"
CONFIG = ".wingbox/config.yaml"
WITHOUT_TERMS = {**BREAK, BASELINE: f"; rule breaks accepted on purpose\n{ENTRY})\n"}
WITH_TERMS = {**BREAK, BASELINE: f"; rule breaks accepted on purpose\n{ENTRY}{TERMS})\n"}
# The layers without the API folder: a domain module that imports a route is then no violation of a rule.
RULES_EDIT = {**BREAK, CONFIG: "parts:\n  .:\n    level: container\n    style: layered\n    layers:\n"
                               "      service: [services]\n      infra: [repositories]\n      domain: [models]\n"}
VIOLATION = "`src/models/models0` -> `src/routes/routes1` (domain -> api)"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
                          check=True, capture_output=True, text=True)
    return done.stdout.strip()


def write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    """Write the files, commit everything and return the commit."""
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def repository(folder: Path) -> tuple[Path, str]:
    """A new repository with the base commit on the branch `main`: (its folder, the commit)."""
    repo = folder / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo, commit(repo, BASE, "base")


def branch(repo: Path, name: str, files: dict[str, str], start: str = "main") -> str:
    """A branch from start with one commit that holds the files; returns the commit. The checkout stays on it."""
    git(repo, "checkout", "-q", "-B", name, start)
    return commit(repo, files, name)

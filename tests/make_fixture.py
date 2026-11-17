#!/usr/bin/env python3
"""A small git repository with a base and a head commit, for the test run of the GitHub Action.

    python tests/make_fixture.py DIR

The base commit holds a Python package in three layers (models, services, api); the head commit
changes the model and its direct user and adds a document. Prints `base=<sha>` and `head=<sha>`
(the lines GITHUB_OUTPUT takes).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = {
    "app/__init__.py": "",
    "app/models/__init__.py": "",
    "app/models/order.py": "class Order:\n    def total(self):\n        return 0\n",
    "app/services/__init__.py": "",
    "app/services/billing.py": "from app.models.order import Order\n\n\ndef bill(order: Order):\n    return order.total()\n",
    "app/api/__init__.py": "",
    "app/api/routes.py": "from app.services.billing import bill\n\n\ndef post(order):\n    return bill(order)\n",
    "app/api/admin.py": "from app.services.billing import bill\n\n\ndef refund(order):\n    return -bill(order)\n",
}
HEAD = {
    "app/models/order.py": "class Order:\n    def total(self, discount=0):\n        return 0 - discount\n",
    "app/services/billing.py": "from app.models.order import Order\n\n\ndef bill(order: Order, discount=0):\n    return order.total(discount)\n",
    "docs/notes.md": "Orders take a discount.\n",
}


def commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git = ["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def main(args: list[str]) -> int:
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(args[0])
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    print(f"base={commit(root, BASE, 'base')}")
    print(f"head={commit(root, HEAD, 'head')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

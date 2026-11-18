#!/usr/bin/env python3
"""What the report on the fixture repository (tests/make_fixture.py) must say.

    python tests/check_fixture.py REPORT UNTOUCHED NOT_CHECKED

REPORT is the path of the Markdown report (the `report` output of the action), UNTOUCHED and
NOT_CHECKED its outputs `untouched-dependents` and `not-checked-parts`. Used by the test workflow
after it ran the action, and by the local end-to-end test.
"""
from __future__ import annotations

import sys
from pathlib import Path

EXPECTED = (
    "<!-- wingbox-pull-request-report -->",
    "## Wingbox: `bill` changed, and 2 modules outside this pull request use it\n",
    "`bill` (function, `app/services/billing.py:4`) changed. These modules use it and are not in the pull request:",
    "```mermaid\n",
    '    c0["app/services/billing.py"]\n',
    "  m0 --> c0\n  m1 --> c0\n",
    "- `app/api/admin` calls it at `app/api/admin.py:5` and in 1 more place\n",
    "- `app/api/routes` calls it at `app/api/routes.py:5` and in 1 more place\n",
    "Not in the code graph (1: documents, configuration or deleted files): `docs/notes.md`.",
    "**Not checked**: part `.`",
    "<sub>[Wingbox](https://wingbox.dev) ",
    "Built and checked on the runner, without a language model; no code left it.",
)


def missing(report: str) -> list[str]:
    """The expected pieces the report does not have."""
    return [piece for piece in EXPECTED if piece not in report]


def main(args: list[str]) -> int:
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    report = Path(args[0]).read_text(encoding="utf-8")
    problems = [f"missing in the report: {piece!r}" for piece in missing(report)]
    if (args[1], args[2]) != ("2", "1"):
        problems.append(f"outputs untouched-dependents, not-checked-parts: {args[1]!r}, {args[2]!r}, expected '2', '1'")
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(report, file=sys.stderr)
        return 1
    print("report as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

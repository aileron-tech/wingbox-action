#!/usr/bin/env python3
"""Replay the action on a past pull request, locally, and write what it would post.

    python tools/replay.py CLONE BASE HEAD --out DIR [--hops N] [--paths PATTERN ...]

CLONE is a local git repository; it is only read: the replay clones it into a temporary folder,
checks out HEAD there and runs the action's report (wingbox_pr.collect and build_comment) as on a
runner, with the wingbox installed in this Python. DIR receives comment.md (the pull request
comment, cut to GitHub's limit), summary.md (the job summary: every dependent listed) and
case.json (changed files, dependents not changed by kind, findings, comment size, seconds).
Nothing is posted anywhere.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wingbox_pr  # noqa: E402
import wingbox_report  # noqa: E402

DESCRIPTION = "Replay the action on a past pull request, locally, and write what it would post."


def replay(clone: str, base: str, head: str, hops: int, patterns: list[str]) -> tuple[wingbox_pr.Report, float]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp) / "repo")
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", clone, root], check=True)
        subprocess.run(["git", "-C", root, "checkout", "--quiet", "--detach", head], check=True)
        report = wingbox_pr.collect(root, base, head, hops, patterns, metadata.version("wingbox"))
    return report, time.monotonic() - started


def dependents_by_kind(report: wingbox_pr.Report) -> dict[str, list[str]]:
    """The dependents the comment lists, as the report sorts them: direct users of a changed file, users
    through a facade or declaration, farther ones, tests (module names, for comparing two replays)."""
    found = wingbox_report.uses(report) if wingbox_report.needs_comment(report) else ()
    return {
        "direct": [use.module for use in found if use.kind == wingbox_report.DIRECT],
        "through_facade": [use.module for use in found if use.kind == wingbox_report.FACADE],
        "farther": [use.module for use in found if use.kind == wingbox_report.FARTHER],
        "tests": [use.module for use in found if use.kind == wingbox_report.TEST],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("clone")
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("--out", required=True)
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--paths", nargs="*", default=[])
    args = parser.parse_args(argv)
    report, seconds = replay(args.clone, args.base, args.head, args.hops, args.paths)
    body = wingbox_report.cut(wingbox_report.build_comment(report), wingbox_report.COMMENT_LIMIT)
    summary = wingbox_report.cut(wingbox_report.build_comment(report, full=True), wingbox_report.SUMMARY_LIMIT)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comment.md").write_text(body, encoding="utf-8", newline="\n")
    (out / "summary.md").write_text(summary, encoding="utf-8", newline="\n")
    case = {
        "base": args.base, "head": args.head, "hops": args.hops,
        "changed": [c.path for c in report.changed],
        "in_graph": [c.path for c in report.changed if c.module is not None],
        "untouched_dependents": wingbox_report.untouched_count(report),
        "direct_untouched": len({d.module for c in report.changed for d in c.untouched if d.depth == 1}),
        "introduced": [f"{f.head}: {f.text}" for f in report.introduced],
        "resolved": report.resolved,
        "not_checked": [f"{p.part}: {p.reason}" for p in report.not_checked],
        **dependents_by_kind(report),
        "comment_chars": len(body), "comment_lines": body.count("\n") + 1,
        "diagram": "```mermaid" in body,
        "seconds": round(seconds, 1),
    }
    (out / "case.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: case[k] for k in ("untouched_dependents", "comment_lines", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

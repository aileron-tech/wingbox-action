#!/usr/bin/env python3
"""Wingbox pull request check: what a pull request touches but does not change, and what it breaks.

Runs inside the GitHub Action of this repository (action.yml), on a checkout of the pull request
with its history. It builds the code graph of the head (and of the base, in a git worktree), then:

- for every changed file, the modules that depend on it within N hops and are not changed by
  the pull request, with what each direct one uses from it (`wingbox impact --sexp`);
- inside every changed file, the items that hold a changed line of code (the diff against the
  items of `wingbox module`) and the modules outside the pull request that use each of them
  (`wingbox usages`);
- the blast radius as a Mermaid diagram inside the report;
- the layer, capability and pattern findings of `wingbox boundaries` that the head has and the
  base has not: the findings the pull request introduces;
- the parts `wingbox boundaries` could not check, said as such.

Run by hand it checks a working directory before a commit:

    python wingbox_pr.py --root DIR --base REF [--head REF] [--out DIR]

With `--base` and no head the change is the base against the files of DIR as they are on disk,
uncommitted and untracked ones included (git's ignore rules apply); no commit is made. The report
and the plan go to `--out`.

It writes the report to the job summary and, when asked, to one comment on the pull request that
is updated on each push (found by its HTML marker). Standard library only; the only network use
is the GitHub API for the comment (pip installs wingbox before this script runs). Wingbox writes
only into `.wingbox/` of the checkout; this script writes only into the runner's temporary folder
and the files GitHub gives it for the summary and the outputs.

This file is the I/O: git, the wingbox commands, the files of the runner, the GitHub API. The
facts are read by wingbox_facts, the report is built by wingbox_report and the diagram by
wingbox_diagram, all three pure.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))   # the modules next to this file, however it is loaded

import wingbox_changes  # noqa: E402
from wingbox_facts import (  # noqa: E402,F401
    BLOCKING_HEADS, FINDING_HEADS, MARKER, ChangedFile, ChangedItem, Dependent, Finding, Item, NotChecked, Report, Route, Site, User,
    checked_modules, dependents, findings, folder_of, introduced, items, module_of, parse_line, parse_route, selected, untouched,
    use_sites, users,
)
from wingbox_report import (  # noqa: E402,F401
    COMMENT_LIMIT, FACADE_FILES, FAIL_ON, FOLDER_LIMIT, LIST_LIMIT, MAP_ARTIFACT, SUMMARY_LIMIT, build_comment, build_plan,
    changed_tests, cut, exit_status, is_facade, is_parent, is_test, needs_comment, result, tests_to_run, untouched_count, uses,
)

# The head of a check of the working directory: the files as they are on disk, not a commit.
WORKING_TREE = "working-tree"

# Wingbox commands that ended abnormally in this run: the result is then UNKNOWN, never a pass.
TOOL_ERRORS: list[str] = []


def _run(argv: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _wingbox(cwd: str, *args: str) -> list[str]:
    done = _run([sys.executable, "-m", "wingbox", *args], cwd)
    # A query may end with 1 or 2 (boundaries found violations, a name is unknown); a build must end with 0:
    # without a graph every later answer is empty, and an empty answer reads as "nothing to check".
    if done.returncode not in ((0,) if args[:1] == ("build",) else (0, 1, 2)):
        print(f"::warning::wingbox {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-400:]}")
        TOOL_ERRORS.append(f"wingbox {args[0]} exited {done.returncode}")
    return done.stdout.splitlines()


def _git(cwd: str, *args: str) -> str:
    done = _run(["git", "-c", "core.quotepath=off", *args], cwd)
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _boundaries(root: str) -> list[str]:
    _wingbox(root, "build", root)
    return _wingbox(root, "boundaries")


ITEM_QUERIES = 60   # items whose users are asked for in one pull request; files beyond that are taken as a whole
REACH = 3           # steps from a changed item to the items of its file that call it


def changed_items(root: str, changed: ChangedFile, hunks: tuple[wingbox_changes.Hunk, ...], changed_modules: set[str],
                  budget: int) -> tuple[Optional[tuple[ChangedItem, ...]], bool, int]:
    """(the items of one changed file that hold a changed line of code, each with its users outside the pull
    request; whether code outside every item changed too; the queries left).

    A change reaches further than the item it is in: an item of the same file that calls a changed item (a
    call, not a mention of a type) is looked at too (and what calls that one, up to REACH steps), with the names it goes through. So a changed
    private helper is reported through the public items that call it.

    None for the items when the file cannot be looked at (it is not readable, or the budget of queries is
    spent): the report then speaks of the file as a whole. An item whose users Wingbox cannot tell (its name
    is ambiguous) counts as other code, so the file's users are still listed."""
    assert changed.module is not None
    try:
        source = (Path(root) / changed.path).read_text(encoding="utf-8-sig", errors="replace").split("\n")
    except OSError:
        return None, False, budget
    listed = items(_wingbox(root, "module", changed.module))
    touched, other = wingbox_changes.touched_items(listed, hunks, source, changed.path)
    waiting: list[tuple[Item, tuple[str, ...]]] = []
    seen: set[str] = set()
    for item in touched:                 # two definitions of one name (overloads) are one item
        if item.name not in seen:
            seen.add(item.name)
            waiting.append((item, ()))
    class_users: dict[str, Optional[set[str]]] = {}    # per class: the modules that use it (None: not known)
    found: list[ChangedItem] = []
    while waiting:
        if budget <= 0:
            return None, False, 0
        item, via = waiting.pop(0)
        budget -= 1
        target = f"{changed.module}::{item.name}"
        answer = _wingbox(root, "usages", target, "--sexp", "--all")
        sites = use_sites(answer, target)
        using = users(answer, target, changed_modules)
        if item.kind == "method":
            # A call of a method is often a call of that NAME (the receiver's type is not known): it counts
            # when the module also uses the class.
            owner = item.name.split(".", 1)[0]
            if owner not in class_users:
                if budget <= 0:
                    return None, False, 0
                budget -= 1
                owner_target = f"{changed.module}::{owner}"
                owner_sites = use_sites(_wingbox(root, "usages", owner_target, "--sexp", "--all"), owner_target)
                class_users[owner] = None if owner_sites is None else {site.module for site in owner_sites}
            known = class_users[owner]
            if sites is None or using is None or known is None:
                # Wingbox has no answer for the method: the change is reported as a change of its class.
                whole = next((listed_item for listed_item in listed if listed_item.name == owner), None)
                if whole is None:
                    other = True
                elif whole.name not in seen:
                    seen.add(whole.name)
                    waiting.append((whole, via))
                continue
            using = tuple(user for user in using if user.module in known)
        if sites is None or using is None:
            other = True
            continue
        found.append(ChangedItem(item.name, item.kind, item.line, using, via))
        if len(via) >= REACH:
            continue
        for site in sites:               # the items of this file that call it are touched by the change too
            if site.module != changed.module or not site.line or site.kind != "call":
                continue
            caller = wingbox_changes.holder(listed, source, changed.path, site.line)
            if caller is not None and caller.name not in seen:
                seen.add(caller.name)
                waiting.append((caller, (item.name, *via)))
    return tuple(sorted(found, key=lambda item: item.line)), other, budget


def collect(root: str, base: str, head: str, hops: int, patterns: list[str], version: str) -> Report:
    """Build the graphs of head (root) and base (a worktree) and gather the report's data.

    head is a commit, or WORKING_TREE: then the change is the merge base of base and HEAD against the files of
    root as they are on disk, uncommitted and untracked ones included, and nothing is committed."""
    working = head == WORKING_TREE
    merge_base = _git(root, "merge-base", base, "HEAD" if working else head).strip()
    if working:
        untracked = [line for line in _git(root, "ls-files", "--others", "--exclude-standard").splitlines() if line]
        names = list(dict.fromkeys([line for line in _git(root, "diff", "--name-only", merge_base).splitlines() if line] + untracked))
    else:
        untracked = []
        names = [line for line in _git(root, "diff", "--name-only", f"{base}...{head}").splitlines() if line]
    changed_paths, skipped = selected(names, patterns)
    head_lines = _boundaries(root)
    head_findings, not_checked = findings(head_lines)
    with tempfile.TemporaryDirectory() as tmp:
        worktree = os.path.join(tmp, "base")
        _git(root, "worktree", "add", "--detach", worktree, merge_base)
        try:
            base_findings, _ = findings(_boundaries(worktree))
        finally:
            _git(root, "worktree", "remove", "--force", worktree)
    new, resolved = introduced(base_findings, head_findings)
    changed_modules = {module_of(path) for path in changed_paths}
    changed: list[ChangedFile] = []
    for path in changed_paths:
        # --all: every dependent, not the first screen of them.
        module, found = dependents(_wingbox(root, "impact", path, str(hops), "--sexp", "--all"))
        if module is not None:
            changed_modules.add(module)
        changed.append(ChangedFile(path, module, ()) if module is None else ChangedFile(path, module, tuple(found)))
    hunks = wingbox_changes.hunks(_git(root, "diff", "-U0", "--no-color", "--no-ext-diff", merge_base if working else f"{base}...{head}"))
    for path in untracked:
        try:
            hunks[path] = wingbox_changes.added_file((Path(root) / path).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            pass
    bent = changed_tests({c.path: (c.module, c.untouched) for c in changed}, hunks)   # untouched still holds every dependent here
    hashes = []
    for c in changed:
        try:
            hashes.append((c.path, hashlib.sha256((Path(root) / c.path).read_bytes()).hexdigest()))
        except OSError:
            pass     # a deleted file has no content at the head
    budget = ITEM_QUERIES
    final_list: list[ChangedFile] = []
    for c in changed:
        if c.module is None:
            final_list.append(c)
            continue
        inside: Optional[tuple[ChangedItem, ...]] = None
        other = False
        if c.path in hunks:      # a file with no hunk (a rename, a mode change) is taken as a whole
            inside, other, budget = changed_items(root, c, hunks[c.path], changed_modules, budget)
        final_list.append(ChangedFile(c.path, c.module, untouched(list(c.untouched), changed_modules), inside, other))
    final = tuple(final_list)
    return Report(version, hops, base, head, final, tuple(new), resolved, tuple(not_checked), tuple(skipped),
                  tests_changed=bent, tool_errors=tuple(dict.fromkeys(TOOL_ERRORS)), hashes=tuple(hashes),
                  checked_modules=checked_modules(head_lines))


def copy_map(root: str, folder: str) -> str:
    """Copy the interactive map Wingbox built (`.wingbox/map.html`) into folder, for the upload step;
    returns the path of the copy, or "" when there is no map."""
    source = Path(root) / ".wingbox" / "map.html"
    if not source.is_file():
        return ""
    target = Path(folder) / "wingbox-map" / "map.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return str(target)


def post_comment(body: str, repository: str, number: int, token: str, api: str, create: bool = True) -> str:
    """Update the comment that carries the marker, or create one when create; returns what was done."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "wingbox-action"}

    def call(method: str, url: str, data: Optional[dict[str, str]] = None) -> tuple[Any, str]:
        request = urllib.request.Request(url, method=method, headers=headers,
                                         data=None if data is None else json.dumps(data).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null"), response.headers.get("Link", "") or ""

    url = f"{api}/repos/{repository}/issues/{number}/comments?per_page=100"
    while url:
        comments, link = call("GET", url)
        for comment in comments:
            if MARKER in (comment.get("body") or ""):
                call("PATCH", f"{api}/repos/{repository}/issues/comments/{comment['id']}", {"body": body})
                return f"updated comment {comment['id']}"
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = match.group(1) if match else ""
    if not create:
        return "no comment: no source module changed"
    created, _ = call("POST", f"{api}/repos/{repository}/issues/{number}/comments", {"body": body})
    return f"created comment {created.get('id')}"


def _append(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def settings(argv: list[str], env: Any, pull: dict[str, Any]) -> tuple[str, str, str, str]:
    """(repository folder, base, head, output folder): a key of the command line before the environment
    variable, before the pull request event, before the default. With `--base` given as a key and no head
    anywhere, the head is the working directory."""
    parser = argparse.ArgumentParser(prog="wingbox_pr.py", description="Check a pull request, or a working directory before a commit.")
    parser.add_argument("--root", help="the repository to check (default: WINGBOX_ROOT, else the current directory)")
    parser.add_argument("--base", help="the commit or branch to compare with")
    parser.add_argument("--head", help="the commit to check (default with --base: the working directory as it is on disk)")
    parser.add_argument("--out", help="the folder for wingbox-report.md and wingbox-plan.sexp")
    args = parser.parse_args(argv)
    root = args.root or env.get("WINGBOX_ROOT") or os.getcwd()
    base = args.base or env.get("WINGBOX_BASE") or pull.get("base", {}).get("sha", "")
    head = (args.head or env.get("WINGBOX_HEAD") or pull.get("head", {}).get("sha", "")
            or (WORKING_TREE if args.base else "HEAD"))
    out = args.out or env.get("RUNNER_TEMP") or tempfile.gettempdir()
    return root, base, head, out


def main(argv: Optional[list[str]] = None) -> int:
    env = os.environ
    fail_on = env.get("WINGBOX_FAIL_ON", "none")
    if fail_on not in FAIL_ON:
        print(f"::error::fail-on must be one of {', '.join(FAIL_ON)}, not {fail_on!r}")
        return 2
    event: dict[str, Any] = {}
    if env.get("GITHUB_EVENT_PATH") and os.path.isfile(env["GITHUB_EVENT_PATH"]):
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pull = event.get("pull_request") or {}
    root, base, head, temp = settings(sys.argv[1:] if argv is None else argv, env, pull)
    root = os.path.abspath(root)
    os.makedirs(temp, exist_ok=True)
    if not base:
        print("::error::no base commit: run the action on pull_request, or give the base input (by hand: --base REF)")
        return 2
    if _run(["git", "cat-file", "-e", f"{base}^{{commit}}"], root).returncode != 0:
        print("::error::the base commit is not in the checkout: use actions/checkout with fetch-depth: 0")
        return 2
    patterns = [p.strip() for p in env.get("WINGBOX_PATHS", "").splitlines() if p.strip()]
    upload_map = env.get("WINGBOX_UPLOAD_MAP", "false") == "true"
    report = collect(root, base, head, int(env.get("WINGBOX_HOPS", "2")), patterns, env.get("WINGBOX_VERSION", "?"))
    map_path = copy_map(root, temp) if upload_map else ""
    if upload_map and not map_path:
        print("::warning::no .wingbox/map.html to upload: wingbox build wrote no map")
    if map_path and env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        # The report names the artifact only when there is a map for the upload step.
        run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
        report = dataclasses.replace(report, map_run=run_url)
    summary = cut(build_comment(report, full=True), SUMMARY_LIMIT)
    comment = cut(build_comment(report), COMMENT_LIMIT)
    if env.get("GITHUB_STEP_SUMMARY"):
        _append(env["GITHUB_STEP_SUMMARY"], summary + "\n")
    report_path = os.path.join(temp, "wingbox-report.md")
    Path(report_path).write_text(summary, encoding="utf-8", newline="\n")
    plan_path = os.path.join(temp, "wingbox-plan.sexp")
    Path(plan_path).write_text(build_plan(report) + "\n", encoding="utf-8", newline="\n")
    outcome, execution, reasons = result(report)
    if env.get("GITHUB_OUTPUT"):
        _append(env["GITHUB_OUTPUT"], f"report={report_path}\nmap={map_path}\nuntouched-dependents={untouched_count(report)}\n"
                                      f"introduced-findings={len(report.introduced)}\nnot-checked-parts={len(report.not_checked)}\n"
                                      f"plan={plan_path}\nresult={outcome}\nexecution={execution}\nreasons={'; '.join(reasons)}\n"
                                      f"tests-to-run={len(tests_to_run(report))}\n")
    print(comment)
    print(f"Result of the boundary check: {outcome} (execution {execution})" + (f": {'; '.join(reasons)}" if reasons else ""))
    if env.get("WINGBOX_COMMENT", "true") == "true" and pull.get("number"):
        try:
            print(post_comment(comment, env["GITHUB_REPOSITORY"], int(pull["number"]), env.get("WINGBOX_TOKEN", ""),
                               env.get("GITHUB_API_URL", "https://api.github.com"), needs_comment(report)))
        except (urllib.error.URLError, KeyError, ValueError) as error:
            # A pull request from a fork gets a read-only token: the summary still holds the report.
            print(f"::warning::could not write the pull request comment ({error}); the report is in the job summary")
    return exit_status(report, fail_on)


if __name__ == "__main__":
    sys.exit(main())

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
import datetime
import glob
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
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))   # the modules next to this file, however it is loaded

import wingbox_changes  # noqa: E402
import wingbox_evidence  # noqa: E402
import wingbox_tests  # noqa: E402
from wingbox_facts import (  # noqa: E402,F401
    BLOCKING_HEADS, FINDING_HEADS, MARKER, WORKING_TREE, ChangedFile, ChangedItem, Dependent, Finding, Item, NotChecked, Report, Route, Site,
    Tree, User, checked_modules, dependents, findings, folder_of, go_functions, introduced, items, module_of, parse_line, parse_route, relation,
    selected, untouched, use_sites, users,
)
from wingbox_report import (  # noqa: E402,F401
    COMMENT_LIMIT, FACADE_FILES, FAIL_ON, FOLDER_LIMIT, LIST_LIMIT, MAP_ARTIFACT, SUMMARY_LIMIT, build_comment, build_plan,
    changed_tests, cut, evidence_counts, exit_status, holds_source, is_facade, is_parent, is_test, manifest_errors, needs_comment, outputs,
    required_evidence, result, test_evidence, tests_status, tests_summary, tests_to_run, untouched_count, uses,
)

# Wingbox commands that ended abnormally in this run: the result is then UNKNOWN, never a pass.
TOOL_ERRORS: list[str] = []
# Languages a build left out because their parser is not installed (name -> files): the result is then UNKNOWN.
SKIPPED_LANGUAGES: dict[str, int] = {}
_SKIPPED_LANGUAGE = re.compile(r"\(skipped-language (\S+) :files (\d+)")


def _run(argv: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _wingbox(cwd: str, *args: str) -> list[str]:
    # -P: the command runs in the checked repository, and `python -m` would put that folder first on the module
    # search path, so a folder `wingbox/` of the repository would run in place of the installed Wingbox.
    done = _run([sys.executable, "-P", "-m", "wingbox", *args], cwd)
    for name, files in _SKIPPED_LANGUAGE.findall(f"{done.stderr}\n{done.stdout}"):
        SKIPPED_LANGUAGES[name] = max(SKIPPED_LANGUAGES.get(name, 0), int(files))
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


def tree_state(root: str, head: str, ignore: tuple[str, ...]) -> Tree:
    """The commit of the working folder of root, how it relates to the head named, and the files that are not
    as in that commit: changed tracked files and untracked source files, git's ignore rules applied. `.wingbox/` and
    the folders of ignore (where this run writes) do not count. Asked before the first build."""
    checked = _git(root, "rev-parse", "HEAD").strip()
    named = _git(root, "rev-parse", f"{head}^{{commit}}").strip()
    parents = tuple(_git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:])
    excluded = [f":(exclude,top){folder}" for folder in (".wingbox", *ignore)]
    fields = _git(root, "status", "--porcelain", "--untracked-files=all", "-z", "--", ":(top)", *excluded).split("\0")
    differs: list[str] = []
    skip = False
    for entry in fields:
        if skip or len(entry) < 4:       # the second name of a rename or a copy: the path it came from
            skip = False
            continue
        skip = entry[0] in "RC"
        # An untracked file counts when Wingbox would read it as source; a test report or a build log does not
        # change the graph.
        if not entry.startswith("??") or wingbox_tests.SOURCE_FILE.search(entry[3:]):
            differs.append(entry[3:])
    return Tree(checked, relation(checked, named, parents), tuple(sorted(differs)))


def commit_hashes(root: str, commit: str, paths: list[str]) -> list[tuple[str, str]]:
    """(path, SHA-256 of its content in the commit), for the paths the commit has: what `git show
    <commit>:<path> | sha256sum` gives, so anyone with the commit can reproduce it."""
    found = []
    for path in paths:
        done = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=root, capture_output=True)
        if done.returncode == 0:
            found.append((path, hashlib.sha256(done.stdout).hexdigest()))
    return found


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


def collect(root: str, base: str, head: str, hops: int, patterns: list[str], version: str, ignore: tuple[str, ...] = ()) -> Report:
    """Build the graphs of head (root) and base (a worktree) and gather the report's data.

    head is a commit, or WORKING_TREE: then the change is the merge base of base and HEAD against the files of
    root as they are on disk, uncommitted and untracked ones included, and nothing is committed. ignore: the
    folders of root this run writes into, which do not count as a difference from the commit."""
    working = head == WORKING_TREE
    tree = Tree("", WORKING_TREE) if working else tree_state(root, head, ignore)
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
    hashes: list[tuple[str, str]] = []
    if working:
        for c in changed:
            try:
                hashes.append((c.path, hashlib.sha256((Path(root) / c.path).read_bytes()).hexdigest()))
            except OSError:
                pass     # a deleted file has no content
    else:
        hashes = commit_hashes(root, tree.commit, [c.path for c in changed])
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
                  checked_modules=checked_modules(head_lines), skipped_languages=tuple(sorted(SKIPPED_LANGUAGES.items())), tree=tree)


def utc(seconds: float) -> str:
    """A time in seconds since 1970 as the record writes it: UTC, to the second."""
    return datetime.datetime.fromtimestamp(int(seconds), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_started(env: Any) -> tuple[str, int]:
    """(the start of the run as the job knows it, UTC; the seconds a report may be older than that).

    GitLab dates the pipeline and the job (CI_PIPELINE_CREATED_AT, CI_JOB_STARTED_AT) by the clock of its
    server, so a report may be a few minutes older. GitHub gives a job no time: the start is the moment the
    runner wrote the event file of the job, by the clock that also dates the reports. By hand: not known."""
    for name in ("CI_PIPELINE_CREATED_AT", "CI_JOB_STARTED_AT"):
        try:
            if env.get(name):
                return utc(datetime.datetime.fromisoformat(env[name]).timestamp()), 300
        except ValueError:
            continue
    if env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_EVENT_PATH"):
        try:
            return utc(os.stat(env["GITHUB_EVENT_PATH"]).st_mtime), 0
        except OSError:
            pass
    return "", 0


def test_origin(env: Any, report: Report) -> wingbox_tests.Origin:
    """What the job knows of itself, to judge the test reports by: the commit checked, the start of the run,
    the run and the attempt (GitHub) or the pipeline (GitLab), the test command given (WINGBOX_TEST_COMMAND,
    verbatim) and whether a sidecar is required (WINGBOX_REQUIRE_TEST_PROVENANCE)."""
    started, tolerance = run_started(env)
    github = env.get("GITHUB_ACTIONS") == "true"
    offset = datetime.datetime.now().astimezone().utcoffset()
    return wingbox_tests.Origin(
        commit=report.tree.commit if report.tree is not None else "", started=started, tolerance=tolerance,
        run_id=env.get("GITHUB_RUN_ID", "") if github else env.get("CI_PIPELINE_ID", ""),
        attempt=env.get("GITHUB_RUN_ATTEMPT", "") if github else "",
        command=env.get("WINGBOX_TEST_COMMAND", ""), require_sidecar=env.get("WINGBOX_REQUIRE_TEST_PROVENANCE", "false") == "true",
        utc_offset=int(offset.total_seconds()) if offset is not None else 0)


test_origin.__test__ = False     # type: ignore[attr-defined]   # not a test case


def tracked_files(folder: str, names: list[str]) -> Optional[set[str]]:
    """Those of the files (paths from folder) that git tracks; None when folder is not in a git work tree."""
    tracked: set[str] = set()
    for start in range(0, len(names), 200):
        done = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files", "-z", "--", *names[start:start + 200]], cwd=folder, capture_output=True)
        if done.returncode != 0:
            return None
        tracked.update(name for name in done.stdout.decode("utf-8", errors="replace").split("\0") if name)
    return tracked


def read_sidecar(path: Path) -> tuple[Optional[wingbox_tests.Sidecar], str]:
    """(the sidecar beside a report, ""), (None, "") when there is none, or (None, why it could not be read)."""
    beside = Path(wingbox_tests.sidecar_name(str(path)))
    if not beside.is_file():
        return None, ""
    try:
        with open(beside, "rb") as stream:
            return wingbox_tests.parse_sidecar(stream.read(wingbox_tests.SIDECAR_LIMIT + 1))
    except OSError as error:
        return None, str(error)


def read_test_results(folder: str, patterns: list[str], origin: Optional[wingbox_tests.Origin] = None) -> tuple[
        Optional[tuple[wingbox_tests.Case, ...]], tuple[wingbox_tests.TestReport, ...], tuple[tuple[str, str], ...]]:
    """(the cases of the JUnit XML reports the patterns find under folder; the files read, each with the
    SHA-256 of its bytes; the files and patterns that gave nothing, with the reason). Without a pattern no
    test report was given: (None, (), ()).

    With an origin each report is judged by what the job knows (wingbox_tests.refusals): it then carries its
    modification time, whether git tracks it, the time and the commits it states and the state of its
    sidecar, and the cases of a refused report are left out."""
    if not patterns:
        return None, (), ()
    names: list[str] = []
    errors: list[tuple[str, str]] = []
    for pattern in patterns:
        found = sorted(name.replace(os.sep, "/") for name in glob.glob(pattern, root_dir=folder, recursive=True)
                       if os.path.isfile(os.path.join(folder, name)) and not name.endswith(wingbox_tests.SIDECAR_ENDING))
        if not found:
            errors.append((pattern, "no file matches this pattern"))
        names += [name for name in found if name not in names]
    tracked = tracked_files(folder, names) if origin is not None and names else None
    cases: list[wingbox_tests.Case] = []
    reports: list[wingbox_tests.TestReport] = []
    for name in names:
        path = Path(folder) / name
        try:
            data = path.read_bytes()
            read, stated = wingbox_tests.parse_report(data)
            modified = utc(path.stat().st_mtime)
        except (OSError, ValueError) as error:
            errors.append((name, str(error)))
            continue
        digest = hashlib.sha256(data).hexdigest()
        if origin is None:
            cases += read
            reports.append(wingbox_tests.TestReport(name, digest, len(read)))
            continue
        known = wingbox_tests.UNKNOWN if tracked is None else wingbox_tests.TRACKED if name in tracked else wingbox_tests.UNTRACKED
        sidecar, sidecar_error = read_sidecar(path)
        refused = wingbox_tests.refusals(stated, modified, known, digest, sidecar, sidecar_error, origin)
        if not refused:
            cases += read
        reports.append(wingbox_tests.TestReport(name, digest, len(read), refused, wingbox_tests.earliest(stated.timestamps, origin.utc_offset), modified, known,
                                                stated.commits, wingbox_tests.sidecar_state(digest, sidecar, sidecar_error, origin)[0],
                                                sidecar.command if sidecar is not None else ""))
    return tuple(cases), tuple(reports), tuple(errors)


def go_test_functions(root: str, tests: list[str], cases: list[wingbox_tests.Case]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(Go test to run, the functions Wingbox lists in it), for the tests a record of a Go package may belong
    to: such a record names a package and a function, never a file, so the file is found by its functions.
    Asked of the graph of root, one `wingbox module` each."""
    found = []
    for test in wingbox_tests.go_candidates(tests, cases):
        names = go_functions(_wingbox(root, "module", module_of(test) if test.endswith(".go") else test))
        if names is not None:
            found.append((test, names))
    return tuple(found)


def read_manifest(root: str, path: str, base: str) -> Optional[wingbox_tests.Manifest]:
    """The manifest of required tests: the file at path (from the root of the repository; "" for the default
    place) as the base commit holds it, so that a change cannot drop a requirement it is judged by; when the
    base has none, the file of the checkout. None when the default place holds none; a path that was given
    and is nowhere, or that leaves the repository, is an error of the manifest."""
    given = path.strip().replace("\\", "/")
    name = given or wingbox_tests.MANIFEST_PATH
    if os.path.isabs(name) or ".." in name.split("/"):
        return wingbox_tests.Manifest(name, "none", "", "", (), ("the path leaves the repository",))
    data: Optional[bytes] = None
    source = "none"
    if base:
        done = subprocess.run(["git", "cat-file", "blob", f"{base}:{name}"], cwd=root, capture_output=True)
        if done.returncode == 0:
            data, source = done.stdout, "base"
    if data is None:
        try:
            data, source = (Path(root) / name).read_bytes(), "head"
        except OSError:
            if not given:
                return None
            return wingbox_tests.Manifest(name, "none", "", "", (), ("the file is not in the base commit and not in the checkout",))
    try:
        groups, errors = wingbox_tests.parse_manifest(data.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        groups, errors = (), (f"not UTF-8 text: {error.reason}",)
    return wingbox_tests.Manifest(name, source, base if source == "base" else "", hashlib.sha256(data).hexdigest(), groups, errors)


def merge_base(root: str, report: Report) -> str:
    """The commit the change is compared with: the merge base of the base and the head; "" when git has none."""
    done = _run(["git", "merge-base", report.base, "HEAD" if report.head == WORKING_TREE else report.head], root)
    return done.stdout.strip() if done.returncode == 0 else ""


def step_outcome(env: Any) -> Optional[str]:
    """How the test step of the job ended (WINGBOX_TEST_STEP_OUTCOME: what `steps.<id>.outcome` gives); ""
    when it is not said; None when the value is none of wingbox_tests.STEP_OUTCOMES."""
    said = env.get("WINGBOX_TEST_STEP_OUTCOME", "").strip()
    return said if said in ("", *wingbox_tests.STEP_OUTCOMES) else None


OUTCOME_ERROR = f"test-step-outcome must be one of {', '.join(wingbox_tests.STEP_OUTCOMES)}"


def not_run_report(base: str, head: str, env: Any) -> Report:
    """The report of a check that did not run (its install failed, or it ended before it wrote a record):
    nothing was looked at. It carries what the job still knows: the two commits named, the outcome of the
    test step and the test command."""
    empty = Report(env.get("WINGBOX_VERSION", "?"), int(env.get("WINGBOX_HOPS", "2") or 2), base, head, (), (), 0, (), did_not_run=True,
                   test_step_outcome=step_outcome(env) or "")
    return dataclasses.replace(empty, test_origin=test_origin(env, empty))


def with_test_results(report: Report, env: Any, root: str) -> Report:
    """The report with the test reports the job hands over (the patterns of WINGBOX_TEST_RESULTS, one per
    line, looked for under WINGBOX_TEST_RESULTS_ROOT, else under the repository) and with the tests the
    manifest of the repository requires for this change (WINGBOX_REQUIRED_TESTS, else the default place).
    Each report is judged by what the job knows of itself (test_origin); a refused one gives no case."""
    patterns = [p.strip() for p in env.get("WINGBOX_TEST_RESULTS", "").splitlines() if p.strip()]
    origin = test_origin(env, report)
    cases, reports, errors = read_test_results(env.get("WINGBOX_TEST_RESULTS_ROOT") or root, patterns, origin)
    manifest = read_manifest(root, env.get("WINGBOX_REQUIRED_TESTS", ""), merge_base(root, report))
    required = (wingbox_tests.required_for(manifest.groups, [changed.path for changed in report.changed] + list(report.skipped))
                if manifest is not None else ())
    names = [test.shown for test in tests_to_run(report)] + [item.test for item in required]
    functions = go_test_functions(root, names, list(cases)) if cases else ()
    return dataclasses.replace(report, test_cases=cases, test_reports=reports, test_report_errors=errors, test_functions=functions,
                               tests_manifest=manifest, required_tests=required, test_origin=origin if patterns or origin.command else None,
                               test_step_outcome=step_outcome(env) or "")


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


def tests_failed(report: Report, env: Any, say: Callable[[str], None] = print) -> int:
    """1, with each reason said, when WINGBOX_REQUIRE_TESTS is true or strict and a test to run or a required
    test has no passing record, or the manifest of required tests could not be read; with strict also when no
    test was found for a change that holds source code. A change no test was found for is said in any case."""
    wanted = env.get("WINGBOX_REQUIRE_TESTS", "false")
    if wanted not in ("true", "strict"):
        return 0
    status = tests_status(report, True, wanted == "strict")
    found, needed = test_evidence(report), required_evidence(report)
    given = " (no test report was given)" if found is None else " in the test reports given"
    manifest = report.tests_manifest
    if report.test_step_outcome not in ("", "success"):
        say(f"Wingbox: require-tests: the test step of the job ended with {report.test_step_outcome}")
    if manifest is not None and manifest.errors:
        say(f"Wingbox: require-tests: the manifest of required tests {manifest.path} could not be read: {'; '.join(manifest.errors)}")
    missing = len(tests_to_run(report)) if found is None else sum(1 for item in found if item.status != wingbox_tests.PASSED)
    if missing:
        say(f"Wingbox: require-tests: {missing} test(s) that use what changed have no passing record{given}")
    lacking = len(report.required_tests) if needed is None else sum(1 for item in needed if item.status != wingbox_tests.PASSED)
    if lacking and manifest is not None:
        say(f"Wingbox: require-tests: {lacking} required test(s) of {manifest.path} have no passing record{given}")
    if not tests_to_run(report) and not report.required_tests and not manifest_errors(report) and report.test_step_outcome in ("", "success"):
        say("Wingbox: require-tests: no test that uses what changed was found"
            + (", and the change holds source code (require-tests is strict)" if status else ""))
    return status


def now() -> str:
    """The UTC time, as the evidence record writes it."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def github_run(env: Any, change: str) -> wingbox_evidence.Run:
    """The run as GitHub Actions names it; outside it a local run."""
    if env.get("GITHUB_ACTIONS") != "true":
        return wingbox_evidence.Run("local", "", "", "", "", "", now())
    repository, run_id = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "")
    url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/actions/runs/{run_id}" if repository and run_id else ""
    return wingbox_evidence.Run("github", repository, change, run_id, url, env.get("GITHUB_ACTION_REF", ""), now(),
                                env.get("GITHUB_RUN_ATTEMPT", ""), env.get("GITHUB_JOB", ""))


def write_evidence(report: Report, run: wingbox_evidence.Run, folder: str, documents: dict[str, str]) -> str:
    """Write the evidence record of the run into folder; returns its path."""
    path = os.path.join(folder, wingbox_evidence.FILE_NAME)
    record = wingbox_evidence.build_evidence(report, run, documents)
    Path(path).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


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


NOT_RUN_KEY = "--did-not-run"


def did_not_run(args: list[str], env: Any, pull: dict[str, Any]) -> int:
    """Write the record of a check that did not run, and its outputs; 0. The last step of the action calls
    this when the check step left no record, so that a job that hands records over always has one."""
    _root, base, head, temp = settings(args, env, pull)
    os.makedirs(temp, exist_ok=True)
    report = not_run_report(base, "" if head == "HEAD" and not env.get("WINGBOX_HEAD") else head, env)
    path = write_evidence(report, github_run(env, str(pull.get("number") or "")), temp, {})
    if env.get("GITHUB_OUTPUT"):
        _append(env["GITHUB_OUTPUT"], f"evidence={path}\n" + "".join(f"{name}={value}\n" for name, value in outputs(report).items()))
    print(f"Wingbox: the check did not run; the record that says so is {path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    env = os.environ
    args = sys.argv[1:] if argv is None else argv
    event: dict[str, Any] = {}
    if env.get("GITHUB_EVENT_PATH") and os.path.isfile(env["GITHUB_EVENT_PATH"]):
        try:
            event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        except ValueError:
            if NOT_RUN_KEY not in args:
                raise
    pull = event.get("pull_request") or {}
    if NOT_RUN_KEY in args:
        return did_not_run([arg for arg in args if arg != NOT_RUN_KEY], env, pull)
    fail_on = env.get("WINGBOX_FAIL_ON", "none")
    if fail_on not in FAIL_ON:
        print(f"::error::fail-on must be one of {', '.join(FAIL_ON)}, not {fail_on!r}")
        return 2
    if step_outcome(env) is None:
        print(f"::error::{OUTCOME_ERROR}, not {env.get('WINGBOX_TEST_STEP_OUTCOME')!r}")
        return 2
    root, base, head, temp = settings(args, env, pull)
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
    report = with_test_results(report, env, root)
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
    plan = build_plan(report) + "\n"
    Path(plan_path).write_text(plan, encoding="utf-8", newline="\n")
    evidence_path = write_evidence(report, github_run(env, str(pull.get("number") or "")), temp,
                                   {"wingbox-report.md": summary, "wingbox-plan.sexp": plan})
    values = outputs(report)
    outcome, execution, reasons = values["result"], values["execution"], values["reasons"]
    if env.get("GITHUB_OUTPUT"):
        _append(env["GITHUB_OUTPUT"], f"report={report_path}\nmap={map_path}\nplan={plan_path}\nevidence={evidence_path}\n"
                                      + "".join(f"{name}={value}\n" for name, value in values.items()))
    print(comment)
    print(f"Result of the boundary check: {outcome} (execution {execution})" + (f": {reasons}" if reasons else ""))
    if env.get("WINGBOX_COMMENT", "true") == "true" and pull.get("number"):
        try:
            print(post_comment(comment, env["GITHUB_REPOSITORY"], int(pull["number"]), env.get("WINGBOX_TOKEN", ""),
                               env.get("GITHUB_API_URL", "https://api.github.com"), needs_comment(report)))
        except (urllib.error.URLError, KeyError, ValueError) as error:
            # A pull request from a fork gets a read-only token: the summary still holds the report.
            print(f"::warning::could not write the pull request comment ({error}); the report is in the job summary")
    return max(exit_status(report, fail_on), tests_failed(report, env))


if __name__ == "__main__":
    sys.exit(main())

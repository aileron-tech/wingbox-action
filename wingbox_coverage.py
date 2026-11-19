"""What a run analysed and what it left out: the `coverage` object of the evidence record.

A run can be partial in several ways: a language whose parser is missing, a part whose rules could not be
checked, changed files left out by the `paths` input, a checkout that is not the commit named, a graph that
is stale. Each is read here from a fact Wingbox prints or git reports, into one account with
`complete: true|false`. A line an older Wingbox does not print leaves its value UNKNOWN: never a guess, and
never complete. Pure functions only.
"""
from __future__ import annotations

import re
from typing import Optional

from wingbox_facts import IN_NO_PART, Graph, Part, Report, parse_line, tree_reasons

FRESH, STALE, UNKNOWN = "FRESH", "STALE", "UNKNOWN"
# The folders Wingbox leaves out by their name, wherever they lie (its own list, as of Wingbox 0.14).
LEFT_OUT_FOLDERS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist", ".next", ".turbo", "build", "obj", "coverage", ".tox", "site-packages",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor", ".worktrees",
})
# A file Wingbox 0.14 reads as source, by its ending: only for the counts of what was left out.
SOURCE = re.compile(r"\.(?:py|ts|tsx|mts|cts|js|jsx|mjs|cjs|rs|cs|java|go|rb|rake|php|pas|pp|lpr|inc|dart)$")
# What a graph built from the source text does not see. Stated once; only the last is counted, where Wingbox counts it.
NOT_SEEN = ("reflection", "imports built from strings", "calls through an untyped receiver")
FOLDERS_LIMIT = 20   # ignored folders named in the record
_NOTE = re.compile(r"\(wingbox-graph-(stale|unconfirmed) :reason ([^\s()]+)(?: :changed-files (\d+))?")


def parts(lines: list[str]) -> Optional[tuple[Part, ...]]:
    """The parts of the repository from `wingbox boundaries --strict`: each checked part with its style and
    modules, each part that was not checked with the reason. None when the output has no `boundaries-result`
    line: a Wingbox before 0.13, which does not say which parts it checked."""
    found: list[Part] = []
    strict = False
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        head, _args, keys = parsed
        if head == "boundaries-checked":
            found.append(Part(keys.get("part", "."), keys.get("style", ""), _number(keys.get("modules", "")), True))
        elif head == "boundaries-not-checked":
            found.append(Part(keys.get("part", "."), "", _number(keys.get("files-in-part", "")), False, keys.get("reason", "unknown")))
        elif head == "boundaries-result":
            strict = True
    return tuple(sorted(found, key=lambda part: part.part)) if strict else None


def _number(text: str) -> int:
    return int(text) if text.isdigit() else 0


def languages(graph: list[str]) -> tuple[tuple[str, int], ...]:
    """(language, files in the graph) from the `node` facts of the graph, by language."""
    counts: dict[str, int] = {}
    for line in graph:
        if line.startswith("(node "):
            fields = line.split(None, 3)
            if len(fields) >= 3:
                counts[fields[2].rstrip(")")] = counts.get(fields[2].rstrip(")"), 0) + 1
    return tuple(sorted(counts.items()))


def notes(text: str) -> tuple[tuple[str, str, int], ...]:
    """(stale or unconfirmed, reason, changed files) of each note a Wingbox command printed about the graph it
    read: `(wingbox-graph-stale :reason R :changed-files N)` or `(wingbox-graph-unconfirmed :reason R ...)`."""
    return tuple((kind, reason, int(changed or 0)) for kind, reason, changed in _NOTE.findall(text))


def graph_state(graph: list[str], said: tuple[tuple[str, str, int], ...], commit: str, build_failed: bool) -> Graph:
    """The state of the graph the report was read from.

    graph: the lines of the graph; its `built-from` fact names the digest of its source files, the commit (when
    the working folder had no change at build time) and the Wingbox version. said: the notes of the commands
    that read it. commit: the commit of the working folder, "" for a working directory. FRESH: built by this
    run, no command found a changed file. STALE: a command says it misses changed files, or it was built from
    another commit. UNKNOWN: the build failed, a command could not confirm it, or it has no `built-from` fact
    (Wingbox before 0.13)."""
    keys: dict[str, str] = {}
    for line in graph:
        parsed = parse_line(line) if line.startswith("(built-from ") else None
        if parsed is not None:
            keys = parsed[2]
    tree, built_from, version = keys.get("tree", ""), keys.get("commit", ""), keys.get("wingbox", "")
    stale = [(reason, changed) for kind, reason, changed in said if kind == "stale"]
    unconfirmed = [reason for kind, reason, _changed in said if kind == "unconfirmed"]
    if build_failed:
        return Graph(UNKNOWN, "build-failed", tree, built_from, version)
    if stale:
        return Graph(STALE, stale[0][0], tree, built_from, version, max(changed for _reason, changed in stale))
    if unconfirmed:
        return Graph(UNKNOWN, unconfirmed[0], tree, built_from, version)
    if not keys:
        return Graph(UNKNOWN, "no-built-from")
    if commit and built_from and built_from != commit:
        return Graph(STALE, "built-from-another-commit", tree, built_from, version)
    return Graph(FRESH, "", tree, built_from, version)


def unresolved_calls(uses: list[str]) -> Optional[int]:
    """How many method calls the graph could not give to a type (`(unresolved <module> :method-calls N)` of its
    use file); None when the use file records no positions, so nothing was counted."""
    counted = any(line.startswith("(code-graph-uses ") and ":positions yes" in line for line in uses)
    if not counted:
        return None
    total = 0
    for line in uses:
        parsed = parse_line(line) if line.startswith("(unresolved ") else None
        if parsed is not None:
            total += _number(parsed[2].get("method-calls", ""))
    return total


def left_out(tracked: list[str]) -> tuple[tuple[str, int], ...]:
    """(folder name, source files) for the tracked source files that lie in a folder Wingbox leaves out by
    its name (a vendored or generated folder): the first such folder of each path."""
    counts: dict[str, int] = {}
    for path in tracked:
        if not SOURCE.search(path):
            continue
        folder = next((name for name in path.split("/")[:-1] if name in LEFT_OUT_FOLDERS), None)
        if folder is not None:
            counts[folder] = counts.get(folder, 0) + 1
    return tuple(sorted(counts.items()))


def ignored(listed: list[str]) -> tuple[int, tuple[str, ...]]:
    """(source files, folders) among what git ignores on disk, as `git ls-files --others --ignored
    --exclude-standard --directory` lists it: a folder ignored as a whole ends with a slash. `.wingbox/`,
    which Wingbox writes, is not counted."""
    kept = [path for path in listed if path and not path.startswith(".wingbox/")]
    return sum(1 for path in kept if not path.endswith("/") and SOURCE.search(path)), tuple(sorted(path for path in kept if path.endswith("/")))


def _parts(report: Report) -> tuple[Part, ...]:
    """The parts the record lists: those of the strict account, else the parts the plain output names as not checked."""
    scan = report.scan
    if scan is not None and scan.parts is not None:
        return scan.parts
    return tuple(Part(part.part, "", 0, False, part.reason) for part in report.not_checked)


def reasons(report: Report) -> tuple[str, ...]:
    """Why the run is not complete; empty when it is. The same words as the reasons of the result where both
    say the same thing."""
    scan = report.scan
    found = [f"tool-error: {error}" for error in report.tool_errors]
    found += [f"skipped-language: {name} ({files} files)" for name, files in report.skipped_languages]
    found += tree_reasons(report)
    found += [f"not-checked: {part.part} ({part.reason})" for part in _parts(report) if not part.checked]
    if report.skipped:
        found.append(f"paths-filter: {len(report.skipped)} changed file(s) left out")
    if scan is None:
        found.append(f"graph: {UNKNOWN} (not-read)")
    else:
        if scan.graph.state != FRESH:
            missed = f", {scan.graph.stale_files} changed file(s)" if scan.graph.stale_files else ""
            found.append(f"graph: {scan.graph.state} ({scan.graph.reason}{missed})")
        if scan.parts is None:
            found.append(f"parts: {UNKNOWN} (wingbox-names-no-checked-part)")
    return tuple(dict.fromkeys(found))


def complete(report: Report) -> bool:
    """Whether the result speaks for the whole repository at the commit named."""
    return not reasons(report)


def in_no_part(report: Report) -> int:
    """How many files lie outside every part of the repository: they have no rules to check."""
    return sum(part.modules for part in _parts(report) if part.reason == IN_NO_PART)


def record(report: Report, repository: str) -> dict[str, object]:
    """The `coverage` object of the evidence record."""
    scan, tree = report.scan, report.tree
    graph = scan.graph if scan is not None else Graph(UNKNOWN, "not-read")
    analysed = dict(scan.languages) if scan is not None else {}
    skipped = dict(report.skipped_languages)
    unresolved = scan.unresolved_calls if scan is not None else None
    return {
        "complete": complete(report),
        "reasons": list(reasons(report)),
        "repository": repository,
        "commit": tree.commit if tree is not None else "",
        "tree": tree.relation if tree is not None else "unknown",
        "languages": [{"language": name, "analysed": analysed.get(name, 0), "skipped": skipped.get(name, 0)}
                      for name in sorted({*analysed, *skipped})],
        "parts": [{"part": part.part, "style": part.style, "modules": part.modules, "checked": part.checked, "reason": part.reason}
                  for part in _parts(report)],
        "excluded": {
            "paths": {"patterns": list(scan.patterns) if scan is not None else [], "changed-files": list(report.skipped)},
            "gitignore": {"files": scan.ignored_files if scan is not None else 0,
                          "folders": list(scan.ignored_folders[:FOLDERS_LIMIT]) if scan is not None else []},
            "folders": [{"folder": folder, "files": files} for folder, files in (scan.folders if scan is not None else ())],
        },
        "graph": {"state": graph.state, "reason": graph.reason, "tree": graph.tree, "commit": graph.commit, "wingbox": graph.wingbox,
                  "stale-files": graph.stale_files},
        "not-seen": [{"what": what, "counted": what == NOT_SEEN[-1] and unresolved is not None,
                      "sites": unresolved if what == NOT_SEEN[-1] and unresolved is not None else 0} for what in NOT_SEEN],
    }

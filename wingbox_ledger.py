"""Evidence over a period: a ledger of the records of a repository, and the report of a period.

One record says what one run found. A reviewer who has to judge a period needs every run of it: the failed
and the unknown ones, each change of the rules, the accepted breaks that came and went, and the stretches in
which nothing was checked. The ledger is a text file with one line per run, added at its end and never
changed: the key of the run, its time, the commits, the verdicts, the policy files the change edited, the
accepted breaks, and the SHA-256 of the record and of the manifest of its package. Each line holds the
SHA-256 of the line before it, so a line that was removed or changed breaks the chain where it happened.
The last line has no line after it: its SHA-256 (the head of the ledger) is what to keep elsewhere.

`period` reads a ledger and counts. It states what was recorded; it does not conclude that a control
operated effectively.

Pure: bytes and values in, values out. No file, no clock, no git: the time of a line is the time of its
record, and the history of the default branch is given by the caller. wingbox_evidence_cli.py is the I/O.
"""
from __future__ import annotations

import calendar
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import wingbox_package as package
from wingbox_package import Problem

SCHEMA = "wingbox-ledger/1"
PERIOD_SCHEMA = "wingbox-period/1"
STATEMENT = ("A count of what the ledger recorded in the period, for a reviewer. "
             "It does not conclude that a control operated effectively.")
NO_PACKAGE = {"manifest-sha256": "", "bundle": "", "state": "none"}
VERDICTS = ("boundaries", "existing", "baseline", "tests", "coverage", "execution")
# The words of a verdict that say a defect was found; a run that is not eligible without one of them is unknown.
FAILED_WORDS = {"boundaries": "FAIL", "existing": "PRESENT", "baseline": "EXPIRED", "tests": "failed"}
_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
DAY = 24 * 3600


@dataclass(frozen=True)
class Ledger:
    """A ledger as it was read."""
    lines: tuple[Mapping[str, Any], ...]     # the lines that are ledger lines, in the order of the file
    problems: tuple[Problem, ...]            # where the chain is broken or a line is no ledger line
    head: str                                # the SHA-256 of the last line as it is; "" for an empty ledger


@dataclass(frozen=True)
class Commit:
    """A commit of the default branch, on its first-parent line."""
    sha: str
    parents: tuple[str, ...]
    time: int        # of the commit, seconds since 1970


def moment(text: str) -> Optional[int]:
    """A UTC time written as 2026-01-02T03:04:05Z, in seconds since 1970; None when it is no such time."""
    if not _MOMENT.match(text):
        return None
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def boundary(text: str, end: bool = False) -> Optional[int]:
    """A boundary of a period: a UTC time, or a day (2026-01-02). A day as the start is its first second; as
    the end it is the first second of the next day, so the day itself belongs to the period."""
    if _DAY.match(text):
        start = moment(text + "T00:00:00Z")
        return None if start is None else start + (DAY if end else 0)
    return moment(text)


def stamp(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def encode(line: Mapping[str, Any]) -> bytes:
    """A line as its bytes, without the line end: JSON with the keys in order, no space, ASCII."""
    return json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def line(record: Mapping[str, Any], record_sha256: str, sequence: int, previous: str,
         packed: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """The ledger line of a record of version 2. previous: the SHA-256 of the line before it, "" for the first.
    packed: the SHA-256 of the manifest, the bundle id and the state of the package of the run, when it has one."""
    run, subject, baseline = record["run"], record["subject"], record["baseline"]

    def breaks(entries: Sequence[Mapping[str, Any]], state: str) -> list[dict[str, str]]:
        return [{"finding": item["finding"], "text": item["text"], "owner": item["owner"], "expires": item["expires"], "state": state}
                for item in entries]
    return {
        "schema": SCHEMA,
        "sequence": sequence,
        "previous": previous,
        "key": "/".join((run["provider"], run["repository"], run["id"], run.get("attempt", ""), run.get("job", ""))),
        "time": record["generated"],
        "repository": run["repository"],
        "change": run["change"],
        "base": subject["base"],
        "head": subject["head"],
        "commit": subject["checked"],
        "verdicts": {"boundaries": record["boundaries"]["introduced"]["verdict"], "existing": record["boundaries"]["existing"]["verdict"],
                     "baseline": baseline["verdict"], "tests": record["tests"]["evidence"],
                     "coverage": "complete" if record["coverage"]["complete"] else "incomplete", "execution": record["result"]["execution"]},
        "eligible": record["eligible"],
        "reasons": list(record["eligible-reasons"]),
        "policy": [{"path": change["path"], "kind": change["kind"], "change": change["change"]} for change in record["policy"]["changed"]],
        "accepted": breaks(baseline["entries"], "accepted") + breaks(baseline["past"], "expired"),
        "accepted-count": baseline["accepted"],
        "expired-count": baseline["expired"],
        "record-sha256": record_sha256,
        "package": dict(packed) if packed else dict(NO_PACKAGE),
    }


def read(data: bytes, schema: Mapping[str, Any]) -> Ledger:
    """Read a ledger and check its chain. Each line must be a ledger line, say it is the line after the one
    before it, and hold the SHA-256 of that line as it is (without its line end; a carriage return before the
    line end does not count). A line that fails is named by its place in the file; the lines after it are
    checked against what is there, so one removed or changed line is reported once, where the chain breaks."""
    pieces = data.split(b"\n")
    problems: list[Problem] = []
    if pieces[-1] == b"":
        pieces.pop()
    elif data:
        problems.append(Problem("ledger", f"line {len(pieces)}", "the last line has no line end: it was cut, or the file was not written by `append`"))
    lines: list[Mapping[str, Any]] = []
    before, expected = "", 1
    for place, raw in enumerate(pieces, start=1):
        text = raw[:-1] if raw.endswith(b"\r") else raw
        said = package.parse(text)
        wrong = package.schema_problems(said, schema) if isinstance(said, dict) else ["not a JSON object"]
        if not wrong and moment(said["time"]) is None:
            wrong = ["its time is no time"]
        if wrong:
            problems.append(Problem("ledger", f"line {place}", "not a ledger line: " + "; ".join(wrong[:3])))
            before, expected = package.sha256(text), 0
            continue
        if said["previous"] != before or (expected and said["sequence"] != expected):
            problems.append(Problem("ledger", f"line {place}", (
                f"says it is line {said['sequence']} and does not name the line before it as it is: " + (
                    "the first lines were removed" if place == 1 else f"line {place - 1} was changed, or a line was removed or added before this one"))))
        lines.append(said)
        before, expected = package.sha256(text), said["sequence"] + 1
    return Ledger(tuple(lines), tuple(problems), before)


def next_line(ledger: Ledger, record: Mapping[str, Any], record_sha256: str, packed: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """The line to add at the end of a ledger whose chain holds."""
    last = ledger.lines[-1]["sequence"] if ledger.lines else 0
    return line(record, record_sha256, last + 1, ledger.head, packed)


def recorded(ledger: Ledger, record_sha256: str) -> int:
    """The number of the line that holds this record; 0 when no line does."""
    return next((said["sequence"] for said in ledger.lines if said["record-sha256"] == record_sha256), 0)


def outcome(said: Mapping[str, Any]) -> str:
    """One word for a run: `eligible` when its record is fit to count as evidence; `failed` when a verdict
    found a defect (a boundary finding introduced, a violation on the head, a baseline entry past its end
    date, a failed test); `unknown` when it is not eligible and no verdict found one."""
    if said["eligible"]:
        return "eligible"
    return "failed" if any(said["verdicts"][name] == word for name, word in FAILED_WORDS.items()) else "unknown"


def _same_change(one: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Whether two runs are on the same change: the same pull or merge request of the same repository, or,
    outside one, the same head commit."""
    if one["repository"] != other["repository"]:
        return False
    return one["change"] == other["change"] if one["change"] else (not other["change"] and one["head"] == other["head"] and bool(one["head"]))


def _named(said: Mapping[str, Any]) -> dict[str, str]:
    return {"key": said["key"], "time": said["time"], "commit": said["commit"] or said["head"]}


def period(ledger: Ledger, start: int, end: int, commits: Optional[Sequence[Commit]] = None, branch: str = "",
           max_gap: Optional[int] = None, expected_head: str = "") -> dict[str, Any]:
    """The report of the runs whose time is in [start, end). commits: the first-parent line of the default
    branch, to list the commits of the period that no line names; None when the ledger is not compared with
    a repository. max_gap: the longest stretch without a run that is not listed, in seconds; None when
    stretches are not looked for. expected_head: the SHA-256 the last line must have, when it was kept
    elsewhere."""
    problems = [{"where": problem.member, "detail": problem.detail} for problem in ledger.problems]
    if expected_head and ledger.head != expected_head.lower():
        problems.append({"where": "last line", "detail": f"its SHA-256 is {ledger.head or 'none (the ledger is empty)'}, not the one given: "
                                                         "lines were removed from the end or added, or the last line was changed"})
    inside = [(index, said) for index, said in enumerate(ledger.lines) if start <= (moment(said["time"]) or 0) < end]
    runs = [said for _index, said in inside]
    by_verdict = {name: {word: sum(1 for said in runs if said["verdicts"][name] == word)
                         for word in sorted({said["verdicts"][name] for said in runs})} for name in VERDICTS}
    packages = {state: sum(1 for said in runs if said["package"]["state"] == state) for state in ("complete", "incomplete", "none")}
    not_eligible = []
    for index, said in inside:
        if said["eligible"]:
            continue
        later = next((other for other in ledger.lines[index + 1:] if other["eligible"] and _same_change(said, other)), None)
        not_eligible.append({**_named(said), "change": said["change"], "outcome": outcome(said), "reasons": list(said["reasons"]),
                             "followed-by": _named(later) if later else {"key": "", "time": "", "commit": ""}})
    changes = [{**_named(said), "change": said["change"], "files": list(said["policy"])} for said in runs if said["policy"]]
    breaks: dict[tuple[str, str], dict[str, Any]] = {}
    for said in runs:
        for item in said["accepted"]:
            seen = breaks.setdefault((item["finding"], item["text"]), {"finding": item["finding"], "text": item["text"], "owner": "", "expires": "",
                                                                       "runs": 0, "expired-in": 0, "first": said["time"], "last": said["time"]})
            seen.update(owner=item["owner"], expires=item["expires"], runs=seen["runs"] + 1, last=said["time"],
                        **{"expired-in": seen["expired-in"] + (1 if item["state"] == "expired" else 0)})
    not_listed = max((said["accepted-count"] + said["expired-count"] - len(said["accepted"]) for said in runs), default=0)
    named = {value for said in ledger.lines for value in (said["commit"], said["head"]) if value}
    in_period = sorted((commit for commit in commits or () if start <= commit.time < end), key=lambda commit: (commit.time, commit.sha))
    direct = [commit for commit in in_period if commit.sha in named]
    merged = [commit for commit in in_period if commit.sha not in named and any(parent in named for parent in commit.parents[1:])]
    without = [commit for commit in in_period if commit not in direct and commit not in merged]
    times = sorted(moment(said["time"]) or 0 for said in runs)
    marks = [start, *times, end]
    longer = [{"from": stamp(one), "to": stamp(other), "hours": (other - one) // 3600}
              for one, other in zip(marks, marks[1:]) if max_gap is not None and other - one > max_gap]
    defects = len(problems) + len(without) + len(longer)
    return {
        "schema": PERIOD_SCHEMA,
        "statement": STATEMENT,
        "period": {"from": stamp(start), "to": stamp(end)},
        "ledger": {"lines": len(ledger.lines), "intact": not problems, "head": ledger.head, "problems": problems},
        "runs": {"total": len(runs), "eligible": sum(1 for said in runs if said["eligible"]),
                 "failed": sum(1 for said in runs if outcome(said) == "failed"), "unknown": sum(1 for said in runs if outcome(said) == "unknown"),
                 "by-verdict": by_verdict, "packages": packages},
        "not-eligible": not_eligible,
        "policy-changes": changes,
        "accepted-breaks": {"breaks": [breaks[key] for key in sorted(breaks)], "not-listed": max(not_listed, 0)},
        "gaps": {
            "commits": {"compared": commits is not None, "branch": branch if commits is not None else "", "in-period": len(in_period),
                        "with-record": len(direct), "through-merged-head": [{"commit": commit.sha, "time": stamp(commit.time)} for commit in merged],
                        "without-record": [{"commit": commit.sha, "time": stamp(commit.time)} for commit in without]},
            "stretches": {"checked": max_gap is not None, "limit-hours": (max_gap or 0) // 3600, "longer": longer},
        },
        "defects": defects,
        "ok": not defects,
    }


def _counts(found: Mapping[str, int]) -> str:
    return ", ".join(f"{word} {count}" for word, count in found.items()) or "none"


def _run(item: Mapping[str, str]) -> str:
    return f"{item['time']} {item['key']} commit {item['commit'] or 'unknown'}"


def text(said: Mapping[str, Any]) -> str:
    """The report for a reader: what it is, the counts, the runs that are not eligible with what followed
    each, the policy changes, the accepted breaks, the gaps; the last line says whether a defect (a broken ledger, a gap) was found."""
    ledger, runs, gaps = said["ledger"], said["runs"], said["gaps"]
    commits, stretches = gaps["commits"], gaps["stretches"]
    out = ["# Evidence over a period", "", said["statement"], "",
           f"Period: {said['period']['from']} to {said['period']['to']} (the end is not included)",
           f"Ledger: {ledger['lines']} line{'' if ledger['lines'] == 1 else 's'}, chain {'intact' if ledger['intact'] else 'BROKEN'}, "
           f"last line SHA-256 {ledger['head'] or 'none'}"]
    out += [f"- ledger problem, {problem['where']}: {problem['detail']}" for problem in ledger["problems"]]
    out += ["", "## Runs", "",
            f"Runs in the period: {runs['total']}; eligible as evidence: {runs['eligible']}; failed: {runs['failed']}; unknown: {runs['unknown']}"]
    out += [f"- {name}: {_counts(runs['by-verdict'][name])}" for name in VERDICTS]
    out += [f"- packages: {_counts(runs['packages'])}"]
    out += ["", "## Runs that are not eligible", ""]
    for item in said["not-eligible"]:
        after = item["followed-by"]
        out.append(f"- {_run(item)}{' change ' + item['change'] if item['change'] else ''}: {item['outcome']} ({'; '.join(item['reasons']) or 'no reason given'}); "
                   + (f"followed by an eligible run on the same change: {_run(after)}" if after["key"] else "followed by no eligible run on the same change"))
    out += [] if said["not-eligible"] else ["None."]
    out += ["", "## Policy changes", ""]
    for item in said["policy-changes"]:
        files = ", ".join(f"{change['path']} ({change['kind']}, {change['change']})" for change in item["files"])
        out.append(f"- {_run(item)}{' change ' + item['change'] if item['change'] else ''}: {files}")
    out += [] if said["policy-changes"] else ["None."]
    out += ["", "## Accepted breaks", ""]
    for item in said["accepted-breaks"]["breaks"]:
        past = f", past its end date in {item['expired-in']}" if item["expired-in"] else ""
        out.append(f"- {item['finding']}: {item['text']}: owner {item['owner'] or 'not stated'}, end date {item['expires'] or 'not stated'}; "
                   f"in {item['runs']} run{'' if item['runs'] == 1 else 's'}{past}, first {item['first']}, last {item['last']}")
    out += [] if said["accepted-breaks"]["breaks"] else ["None."]
    if said["accepted-breaks"]["not-listed"]:
        out.append(f"A run holds {said['accepted-breaks']['not-listed']} more than its record lists.")
    out += ["", "## Gaps", ""]
    if commits["compared"]:
        out.append(f"Commits of {commits['branch']} in the period: {commits['in-period']}; named by a line: {commits['with-record']}; "
                   f"only their merged head named: {len(commits['through-merged-head'])}; without a record: {len(commits['without-record'])}")
        out += [f"- only the merged head has a record: {item['commit']} {item['time']}" for item in commits["through-merged-head"]]
        out += [f"- without a record: {item['commit']} {item['time']}" for item in commits["without-record"]]
    else:
        out.append("Commits of the default branch: not compared (no --repo).")
    if stretches["checked"]:
        out.append(f"Stretches without a run longer than {stretches['limit-hours']} hours: {len(stretches['longer'])}")
        out += [f"- {item['from']} to {item['to']}: {item['hours']} hours" for item in stretches["longer"]]
    else:
        out.append("Stretches without a run: not looked for (no --max-gap-hours).")
    out += ["", "period: ok" if said["ok"] else f"period: failed ({said['defects']} defect{'' if said['defects'] == 1 else 's'})"]
    return "\n".join(out) + "\n"

#!/usr/bin/env python3
"""wingbox-evidence: build, check and review the evidence package of a run, keep the runs of a period, and
evaluate a control mapping against a run.

    python wingbox_evidence_cli.py pack --run-dir DIR --out FILE [--repo DIR] [--reports-root DIR] [--job-log FILE]
                                        [--policy PATH]... [--workflow PATH]... [--stated NAME=VALUE]... [--retention TEXT]
    python wingbox_evidence_cli.py verify FILE [--repo DIR] [--manifest-sha256 HEX] [--json]
    python wingbox_evidence_cli.py review FILE --reviewer NAME --decision accepted|rejected --grounds TEXT [--time UTC]
    python wingbox_evidence_cli.py append --ledger FILE (--record FILE | --package FILE)
    python wingbox_evidence_cli.py period --ledger FILE --from DATE --to DATE [--repo DIR [--branch NAME]]
                                          [--max-gap-hours N] [--head-sha256 HEX] [--json]
    python wingbox_evidence_cli.py controls (--record FILE | --package FILE) [--mapping FILE] [--date DAY] [--json]

`pack` gathers what a run left in DIR (wingbox-evidence.json and the report and plan it names), the test
reports the record names as they are on disk, the job log when one is given, the policy files as the commit
checked holds them (read from git, never from the working folder) and the schemas, and writes one archive
with `manifest.json`. `verify` checks an archive against itself, and with --repo against the repository.
`review` adds the decision of a person as a new member; no earlier byte changes. `append` adds the line of
a run at the end of a ledger; no earlier byte changes. `period` reads a ledger and counts what it recorded
between two dates, with the gaps: with --repo the commits of a branch that no line names, with
--max-gap-hours the stretches without a run. `controls` evaluates a control mapping (wingbox_controls) against
the record of a run, or against the record in a package, and prints each control with its state, its reasons
and the evidence it names, then the controls without evidence and the evidence no control names.

Exit status: 0 the command did its work and the result is good (a complete package, a package that
verifies, a review recorded, a line recorded, a period without a gap, a mapping every evaluated control of
which is supported); 1 the command did its work and found a defect (an incomplete package, a member that does
not verify, a package or a ledger that does not verify, a gap in the period, a mapping that is refused or a
control that is not-supported or incomplete); 2 wrong usage (an option, a file that is not there, a file that would be written
over). The last line of the output says which of the first two it is.

This file is the I/O: files, git, the clock for a review without --time. The rules are in
wingbox_package, wingbox_ledger and wingbox_controls, pure. Nothing of a package or of a repository is run: git is asked for
file contents (`git cat-file`) and for the commits of a branch (`git rev-list`), and an archive is read in
memory, never unpacked. Standard library only, no network.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))   # the modules next to this file, however it is loaded

import wingbox_controls as controls  # noqa: E402
import wingbox_ledger as ledger  # noqa: E402
import wingbox_package as package  # noqa: E402

HERE = Path(__file__).resolve().parent
PROGRAM = "wingbox-evidence"
PACKAGE_FILE = "wingbox-package.zip"
LEDGER_SCHEMA_FILE = "wingbox-ledger-1.schema.json"
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_DIGEST = re.compile(r"^[0-9a-fA-F]{64}\Z")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


class Usage(Exception):
    """The command was asked for something it cannot do as asked: exit status 2."""


def schema_bytes(name: str) -> bytes:
    """A schema of this tool as it is packed: with line feeds, whatever the checkout of the tool made of them."""
    return (HERE / "schema" / name).read_bytes().replace(b"\r\n", b"\n")


def schemas() -> package.Schemas:
    """The schemas of this tool, by which a package is checked."""
    record, manifest, review = (json.loads(schema_bytes(name)) for name in package.SCHEMA_FILES)
    return package.Schemas(manifest, review, record)


def _optional(path: Optional[Path], limit: int) -> Optional[bytes]:
    """The bytes of a file; None when it is not a file, cannot be read or is larger than the limit."""
    try:
        if path is None or not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _given_file(path: str, limit: int, what: str) -> bytes:
    """The bytes of a file the command line names; Usage when it is not there or too large."""
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise Usage(f"{what} {path}: {error.strerror or error}") from None
    if not os.path.isfile(path):
        raise Usage(f"{what} {path} is not a file")
    if size > limit:
        raise Usage(f"{what} {path} is {size} bytes, more than the limit of {limit} (--max-bytes)")
    return Path(path).read_bytes()


def _inside(root: Path, relative: str) -> Optional[Path]:
    """The file at a path of the record under root, links followed; None when it is not there or lies outside
    root: a record is data, and a path of it never reaches a file elsewhere."""
    try:
        target = (root / relative).resolve(strict=True)
        target.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return target


def _git(repo: str, *args: str) -> Optional[bytes]:
    """What a git command that only reads prints; None when it fails, git is not there or repo is no folder."""
    try:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def has_commit(repo: str, commit: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is not None


def blob(repo: str, commit: str, path: str) -> Optional[bytes]:
    """The content of a file in a commit, what `git show <commit>:<path>` gives; None when the commit has none."""
    return _git(repo, "cat-file", "blob", f"{commit}:{path}")


def pack_run(run_dir: str, out: str, repo: str = ".", reports_root: str = "", job_log: str = "", named: Sequence[tuple[str, str]] = (),
             stated: Sequence[tuple[str, str]] = (), retention: str = "", limit: int = package.MAX_BYTES) -> tuple[int, list[str]]:
    """Write the package of the run in run_dir to out; (0 for a complete package and 1 for an incomplete one,
    the lines that say so). named: (path, kind) of policy files to ask the commit for besides the known ones."""
    if os.path.lexists(out):
        raise Usage(f"{out} exists: a package is not written over")
    if not os.path.isdir(run_dir):
        raise Usage(f"the run folder {run_dir} is not a folder")
    own = {name: schema_bytes(name) for name in package.SCHEMA_FILES}
    record_schema = json.loads(own[package.SCHEMA_FILES[0]])
    members: dict[str, bytes] = {package.schema_path(name): data for name, data in own.items()}
    record = _optional(Path(run_dir) / package.RECORD_FILE, limit)
    if record is not None:
        members[package.RECORD] = record
    want = package.wanted(record, record_schema, named)
    for name in want.documents:
        data = _optional(Path(run_dir) / name, limit)
        if data is not None:
            members[package.document_path(name)] = data
    root = Path(reports_root or repo)
    for path in want.reports:
        for name in (path, path + package.SIDECAR_ENDING):
            data = _optional(_inside(root, name), limit)
            if data is not None:
                members[package.report_path(name)] = data
    read = bool(want.commit) and has_commit(repo, want.commit)
    if read:
        for path, _kind in want.policy:
            data = blob(repo, want.commit, path)
            if data is not None:
                members[package.policy_path(path)] = data
    log = os.path.basename(job_log) if job_log else ""
    if job_log:
        members[package.job_log_path(log)] = _given_file(job_log, limit, "the job log")
    given = package.Given(retention, tuple(sorted(stated)), read, want.policy, log)
    data, manifest = package.pack(members, given, record_schema)
    if len(data) > min(limit, package.HARD_LIMIT):
        raise Usage(f"the package would be {len(data)} bytes, more than the limit of {limit} (--max-bytes)")
    try:
        with open(out, "xb") as stream:
            stream.write(data)
    except OSError as error:
        raise Usage(f"{out}: {error.strerror or error}") from None
    absent = [need for need in manifest["required"] if need["state"] != package.PRESENT]
    lines = [f"package: {out}", f"bundle: {manifest['bundle']}", f"manifest-sha256: {package.sha256(package.encode(manifest))}",
             f"state: {manifest['state']}", f"members: {len(manifest['members'])}",
             *(f"absent: {need['id']} ({need['state']}: {need['reason']})" for need in absent),
             "pack: complete" if not absent else f"pack: incomplete ({len(absent)} required member{'' if len(absent) == 1 else 's'} absent)"]
    return (1 if absent else 0), lines


def verify_file(path: str, repo: Optional[str] = None, expected: str = "", limit: int = package.MAX_BYTES) -> dict[str, object]:
    """The check of the archive at path as one value (wingbox_package.result); with repo also against the
    commits of that repository."""
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise Usage(f"the package {path}: {error.strerror or error}") from None
    if size > limit:
        checked = package.Checked((package.Problem("too-large", "", f"{size} bytes, more than the limit of {limit} (--max-bytes)"),))
    else:
        checked = package.verify(_given_file(path, limit, "the package"), schemas(), expected, limit)
    compared = None
    if repo is not None:
        if checked.stated is None:
            compared = [package.Problem("not-recomputable", "", "the manifest could not be read")]
        else:
            asked = package.git_wanted(checked)
            commits = {commit: has_commit(repo, commit) for commit in {commit for commit, _path in asked} | {checked.stated["policy"]["commit"]}
                       if commit}
            digests = {}
            for commit, name in asked:
                data = blob(repo, commit, name) if commits.get(commit) else None
                digests[(commit, name)] = None if data is None else package.sha256(data)
            compared = package.git_problems(checked, commits, digests)
    return {"package": path, **package.result(checked, compared)}


def review_file(path: str, reviewer: str, decision: str, grounds: str, time: str, limit: int = package.MAX_BYTES) -> tuple[int, list[str]]:
    """Add a review to the archive at path; (0, what was recorded), or (1, why not) when the archive does not
    verify. The new archive is written beside the old one and put in its place in one step."""
    for what, text in (("--reviewer", reviewer), ("--grounds", grounds)):
        if not text.strip() or any(ord(letter) < 32 for letter in text):
            raise Usage(f"{what} must be one line of text")
    if not _TIME.match(time):
        raise Usage("--time must be a UTC time as 2026-01-02T03:04:05Z")
    data = _given_file(path, limit, "the package")
    new, checked = package.with_review(data, schemas(), reviewer, decision, grounds, time, limit)
    if new is None:
        said = package.result(checked)
        return 1, [f"package: {path}", *package.lines(said)[:-1], "review: not recorded (the package does not verify)"]
    if len(new) > min(limit, package.HARD_LIMIT):
        raise Usage(f"the package would be {len(new)} bytes, more than the limit of {limit} (--max-bytes)")
    beside = f"{path}.new"
    try:
        with open(beside, "wb") as stream:
            stream.write(new)
        os.replace(beside, path)
    except OSError as error:
        raise Usage(f"{path}: {error.strerror or error}") from None
    number = len(checked.reviews) + 1
    state = checked.stated["state"] if checked.stated else "unknown"
    return 0, [f"package: {path}", f"manifest-sha256: {checked.manifest_sha256}", f"state: {state}",
               f"member: {package.review_name(number)}", f"decision: {decision} by {reviewer} at {time}", "review: recorded"]


def ledger_schema() -> dict[str, object]:
    return json.loads(schema_bytes(LEDGER_SCHEMA_FILE))


def _problems(found: Sequence[package.Problem]) -> list[str]:
    return [f"problem: {problem.code}{' ' + problem.member if problem.member else ''}: {problem.detail}" for problem in found]


def append_run(path: str, record_file: str = "", package_file: str = "", limit: int = package.MAX_BYTES) -> tuple[int, list[str], str]:
    """Add the line of a run at the end of the ledger at path, from its package or from its record alone;
    (the exit status, the lines that say what was done, the SHA-256 of the last line of the ledger). The file
    is opened to add to: no earlier byte is written. 1 and nothing added when the ledger or the package does
    not verify or the record is no record of version 2; 0 and nothing added when the record is in the ledger."""
    data = _given_file(path, limit, "the ledger") if os.path.lexists(path) else b""
    book = ledger.read(data, ledger_schema())
    if book.problems:
        return 1, [f"ledger: {path}", *_problems(book.problems), "append: not recorded (the ledger does not verify)"], ""
    own = schemas()
    packed = None
    if package_file:
        archive = _given_file(package_file, limit, "the package")
        checked = package.verify(archive, own, limit=limit)
        if not checked.intact or checked.stated is None:
            return 1, [f"ledger: {path}", *_problems(checked.problems), "append: not recorded (the package does not verify)"], book.head
        record_bytes = dict(package.read(archive)[0]).get(package.RECORD)
        packed = {"manifest-sha256": checked.manifest_sha256, "bundle": checked.stated["bundle"], "state": checked.stated["state"]}
    else:
        record_bytes = _given_file(record_file, limit, "the record")
    record = package.read_record(record_bytes, own.record)
    if record is None or record_bytes is None:
        return 1, [f"ledger: {path}", f"append: not recorded (not a {package.RECORD_SCHEMA} record)"], book.head
    digest = package.sha256(record_bytes)
    already = ledger.recorded(book, digest)
    if already:
        return 0, [f"ledger: {path}", f"line: {already}", f"head-sha256: {book.head}", "append: already recorded"], book.head
    said = ledger.next_line(book, record, digest, packed)
    new = ledger.encode(said)
    try:
        with open(path, "ab") as stream:
            stream.write(new + b"\n")
    except OSError as error:
        raise Usage(f"the ledger {path}: {error.strerror or error}") from None
    head = package.sha256(new)
    return 0, [f"ledger: {path}", f"line: {said['sequence']}", f"key: {said['key']}", f"time: {said['time']}", f"package: {said['package']['state']}",
               f"head-sha256: {head}", "append: recorded"], head


def branch_commits(repo: str, branch: str) -> list[ledger.Commit]:
    """The commits of a branch on its first-parent line, each with its parents and its time, as git lists them."""
    if branch.startswith("-"):
        raise Usage(f"--branch {branch} is no branch name")
    listed = _git(repo, "rev-list", "--first-parent", "--parents", "--timestamp", branch, "--")
    if listed is None:
        raise Usage(f"the repository {repo} has no commit named {branch}")
    found = []
    for words in (line.split() for line in listed.decode("ascii", errors="replace").splitlines()):
        if len(words) >= 2 and words[0].isdigit():
            found.append(ledger.Commit(words[1], tuple(words[2:]), int(words[0])))
    return found


def period_run(path: str, start: str, end: str, repo: Optional[str] = None, branch: str = "HEAD", max_gap_hours: Optional[int] = None,
               expected_head: str = "", limit: int = package.MAX_BYTES) -> dict[str, object]:
    """The report of the ledger at path for the period from start to end (wingbox_ledger.period); with repo
    the ledger is compared with the commits of that branch of the repository."""
    first, last = ledger.boundary(start), ledger.boundary(end, end=True)
    if first is None or last is None:
        raise Usage("--from and --to must be a day as 2026-01-02 or a UTC time as 2026-01-02T03:04:05Z")
    if first >= last:
        raise Usage("--from must be before --to")
    if max_gap_hours is not None and max_gap_hours < 1:
        raise Usage("--max-gap-hours must be a positive number of hours")
    book = ledger.read(_given_file(path, limit, "the ledger"), ledger_schema())
    commits = branch_commits(repo, branch) if repo is not None else None
    return ledger.period(book, first, last, commits, branch, None if max_gap_hours is None else max_gap_hours * 3600, expected_head)


def controls_run(record_file: str = "", package_file: str = "", mapping_file: str = "", date: str = "",
                 limit: int = package.MAX_BYTES) -> tuple[int, dict[str, object], list[str]]:
    """Evaluate a control mapping against the record of a run, by hand: (0 when the mapping was read and no
    control is not-supported or incomplete, else 1; the evaluation as one value; lines to print before it).

    The record is the file given, or the record of the package given, which must verify. The mapping is the
    file given; for a package without one, the member that holds the mapping as the commit checked has it. It
    is evaluated as a mapping given by hand: whether the base commit of the run held it is a question for
    the record of that run, which a line answers by comparing the SHA-256 the record names. date: the day
    validity is judged at; "" for the day of the record."""
    own = schemas()
    before: list[str] = []
    members: dict[str, bytes] = {}
    if package_file:
        archive = _given_file(package_file, limit, "the package")
        checked = package.verify(archive, own, limit=limit)
        if not checked.intact:
            return 1, {}, [f"package: {package_file}", *_problems(checked.problems), "controls: not evaluated (the package does not verify)"]
        members = dict(package.read(archive)[0])
        record_bytes = members.get(package.RECORD)
        before.append(f"package: {package_file}")
    else:
        record_bytes = _given_file(record_file, limit, "the record")
        before.append(f"record: {record_file}")
    record = package.read_record(record_bytes, own.record)
    if record is None:
        return 1, {}, [*before, f"controls: not evaluated (not a {package.RECORD_SCHEMA} record)"]
    ran = record.get("controls", {}).get("mapping", {})
    if mapping_file:
        data, name = _given_file(mapping_file, limit, "the mapping"), mapping_file.replace("\\", "/")
    else:
        name = ran.get("path") or controls.CONTROLS_PATH
        found = members.get(package.policy_path(name))
        if found is None:
            raise Usage(f"no control mapping: name one with --mapping (the {'package holds' if package_file else 'record names'} none"
                        f"{' at ' + name if package_file else ''})")
        data = found
    day = date or record["generated"][:10]
    if not _DAY.match(day):
        raise Usage("--date must be a day as 2026-01-31")
    try:
        said = controls.evaluate(controls.read(data, name, "given"), record, day)
    except ValueError:
        raise Usage("--date must be a day as 2026-01-31") from None
    digest = said["mapping"]["sha256"]
    before.append("run-mapping: the record evaluated no mapping" if not ran.get("sha256") else
                  f"run-mapping: the same bytes as the run evaluated ({ran.get('source')})" if ran.get("sha256") == digest else
                  f"run-mapping: other bytes than the run evaluated ({ran.get('source')}, SHA-256 {ran.get('sha256')})")
    return (0 if controls.ok(said) else 1), said, before


def now() -> str:
    """The UTC time, as a review writes it."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pair(text: str) -> tuple[str, str]:
    name, mark, value = text.partition("=")
    if not mark or not name.strip():
        raise argparse.ArgumentTypeError("write NAME=VALUE")
    return name.strip(), value


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog=PROGRAM, description="Build, check and review the evidence package of a run, keep the runs of a period, "
                                                            "and evaluate a control mapping against a run.",
                                  epilog="Exit status: 0 done and good; 1 done, and a defect was found; 2 wrong usage.")
    commands = top.add_subparsers(dest="command", required=True, metavar="pack|verify|review|append|period|controls")
    limit = argparse.ArgumentParser(add_help=False)
    limit.add_argument("--max-bytes", type=int, default=package.MAX_BYTES, metavar="N",
                       help=f"the largest archive and the largest member read (default {package.MAX_BYTES})")
    pack = commands.add_parser("pack", parents=[limit], help="gather a run into one archive with a manifest")
    pack.add_argument("--run-dir", required=True, metavar="DIR", help="the folder the run wrote wingbox-evidence.json, the report and the plan into")
    pack.add_argument("--out", required=True, metavar="FILE", help="the archive to write; it must not exist")
    pack.add_argument("--repo", default=".", metavar="DIR", help="the repository whose commit holds the policy files (default: the current folder)")
    pack.add_argument("--reports-root", default="", metavar="DIR", help="the folder the test report paths of the record start from (default: --repo)")
    pack.add_argument("--job-log", default="", metavar="FILE", help="the log of the job, packed as it is")
    pack.add_argument("--policy", action="append", default=[], metavar="PATH", help="one more policy file to take from the commit checked")
    pack.add_argument("--workflow", action="append", default=[], metavar="PATH", help="the CI configuration that ran the job, taken from the commit checked")
    pack.add_argument("--stated", action="append", default=[], type=_pair, metavar="NAME=VALUE",
                      help="a version the job states: build, model, prompt, tool-schema, test-data")
    pack.add_argument("--retention", default="", metavar="TEXT", help="how long the job keeps the package, in its own words")
    verify = commands.add_parser("verify", parents=[limit], help="check an archive against itself, and with --repo against the repository")
    verify.add_argument("package", metavar="FILE")
    verify.add_argument("--repo", default=None, metavar="DIR", help="also compare the policy files and the changed files with their commit in this repository")
    verify.add_argument("--manifest-sha256", default="", metavar="HEX", help="the SHA-256 the manifest must have, when it was kept elsewhere")
    verify.add_argument("--json", action="store_true", help="print the check as one JSON value")
    review = commands.add_parser("review", parents=[limit], help="add the decision of a person as a new member")
    review.add_argument("package", metavar="FILE")
    review.add_argument("--reviewer", required=True, metavar="NAME")
    review.add_argument("--decision", required=True, choices=package.DECISIONS)
    review.add_argument("--grounds", required=True, metavar="TEXT")
    review.add_argument("--time", default="", metavar="UTC", help="the time of the decision as 2026-01-02T03:04:05Z (default: now)")
    append = commands.add_parser("append", parents=[limit], help="add the line of a run at the end of a ledger")
    append.add_argument("--ledger", required=True, metavar="FILE", help="the ledger; it is made when it is not there")
    source = append.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", default="", metavar="FILE", help="the package of the run: the line names its manifest")
    source.add_argument("--record", default="", metavar="FILE", help="wingbox-evidence.json of a run without a package")
    period = commands.add_parser("period", parents=[limit], help="count what a ledger recorded between two dates, with the gaps")
    period.add_argument("--ledger", required=True, metavar="FILE")
    period.add_argument("--from", dest="start", required=True, metavar="DATE", help="the first day (2026-01-02) or moment (2026-01-02T03:04:05Z, UTC)")
    period.add_argument("--to", dest="end", required=True, metavar="DATE", help="the last day, which belongs to the period, or the moment it ends")
    period.add_argument("--repo", default=None, metavar="DIR", help="compare with the commits of a branch of this repository")
    period.add_argument("--branch", default="HEAD", metavar="NAME", help="the default branch as the repository names it (default: HEAD)")
    period.add_argument("--max-gap-hours", type=int, default=None, metavar="N", help="list every stretch without a run longer than this")
    period.add_argument("--head-sha256", default="", metavar="HEX", help="the SHA-256 the last line must have, when it was kept elsewhere")
    period.add_argument("--json", action="store_true", help="print the report as one JSON value")
    mapped = commands.add_parser("controls", parents=[limit], help="evaluate a control mapping against the record of a run")
    subject = mapped.add_mutually_exclusive_group(required=True)
    subject.add_argument("--record", default="", metavar="FILE", help="wingbox-evidence.json of a run")
    subject.add_argument("--package", default="", metavar="FILE", help="the package of a run: its record is evaluated")
    mapped.add_argument("--mapping", default="", metavar="FILE", help="the control mapping (default with --package: the one the package holds)")
    mapped.add_argument("--date", default="", metavar="DAY", help="the day validity is judged at, as 2026-01-31 (default: the day of the record)")
    mapped.add_argument("--json", action="store_true", help="print the evaluation as one JSON value")
    return top


def run(args: argparse.Namespace) -> tuple[int, list[str]]:
    """(the exit status, the lines to print) of a parsed command."""
    if args.max_bytes < 1:
        raise Usage("--max-bytes must be a positive number")
    if args.command == "pack":
        named = [(path, "workflow") for path in args.workflow] + [(path, "named") for path in args.policy]
        return pack_run(args.run_dir, args.out, args.repo, args.reports_root, args.job_log, named, args.stated, args.retention, args.max_bytes)
    if args.command == "verify":
        if args.manifest_sha256 and not _DIGEST.match(args.manifest_sha256):
            raise Usage("--manifest-sha256 must be 64 hexadecimal digits")
        said = verify_file(args.package, args.repo, args.manifest_sha256, args.max_bytes)
        text = [json.dumps(said, indent=2, ensure_ascii=True)] if args.json else [f"package: {said['package']}", *package.lines(said)]
        return (0 if said["ok"] else 1), text
    if args.command == "review":
        return review_file(args.package, args.reviewer, args.decision, args.grounds, args.time or now(), args.max_bytes)
    if args.command == "append":
        status, lines, _head = append_run(args.ledger, args.record, args.package, args.max_bytes)
        return status, lines
    if args.command == "controls":
        status, said, before = controls_run(args.record, args.package, args.mapping, args.date, args.max_bytes)
        if not said:
            return status, before
        return status, [json.dumps(said, indent=2, ensure_ascii=True)] if args.json else [*before, *controls.lines(said)]
    if args.head_sha256 and not _DIGEST.match(args.head_sha256):
        raise Usage("--head-sha256 must be 64 hexadecimal digits")
    report = period_run(args.ledger, args.start, args.end, args.repo, args.branch, args.max_gap_hours, args.head_sha256, args.max_bytes)
    return (0 if report["ok"] else 1), [json.dumps(report, indent=2, ensure_ascii=True) if args.json else ledger.text(report).rstrip("\n")]


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)     # wrong usage ends here with status 2
    try:
        status, lines = run(args)
    except Usage as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return status


if __name__ == "__main__":
    sys.exit(main())

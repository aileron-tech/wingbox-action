"""Evidence over a period: the ledger of the records of a repository, and the report of a period.

What is expected here is written by hand and computed apart from the modules under test: a ledger is written
by `ledger_bytes` (JSON with sorted keys and no space, each line holding the hashlib SHA-256 of the line
before), the reports are literals, the schema check is the validator of test_evidence_record.
"""
import ast
import calendar
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_ledger as wl  # noqa: E402
import wingbox_pr as pr  # noqa: E402
from test_evidence_record import problems as schema_problems  # noqa: E402
from test_package import as_json, members_of, run_cli, run_folder, sha  # noqa: E402
from test_wingbox_pr import report  # noqa: E402

LEDGER_SCHEMA = json.loads((ROOT / "schema" / "wingbox-ledger-1.schema.json").read_text(encoding="utf-8"))
PERIOD_SCHEMA = json.loads((ROOT / "schema" / "wingbox-period-1.schema.json").read_text(encoding="utf-8"))
STATEMENT = "A count of what the ledger recorded in the period, for a reviewer. It does not conclude that a control operated effectively."
PASSING = {"boundaries": "PASS", "existing": "NONE", "baseline": "NONE", "tests": "complete", "coverage": "complete", "execution": "ok"}
BREAK_A = {"finding": "violation-known", "text": "src/a -> src/b", "owner": "team-a", "expires": "2026-03-01"}
BREAK_B = {"finding": "capability-known", "text": "src/c uses db-client", "owner": "", "expires": ""}
NO_PACKAGE = {"manifest-sha256": "", "bundle": "", "state": "none"}


def seconds(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return calendar.timegm((year, month, day, hour, minute, 0))


def line(run: str, time: str, change: str = "", head: str = "", eligible: bool = True, reasons: tuple = (), policy: tuple = (),
         accepted: tuple = (), package: dict = NO_PACKAGE, base: str = "a" * 40, **verdicts: str) -> dict:
    """A ledger line without its place in the chain, written out."""
    return {"schema": "wingbox-ledger/1", "key": f"github/owner/repo/{run}/1/check", "time": time, "repository": "owner/repo", "change": change,
            "base": base, "head": head, "commit": head, "verdicts": {**PASSING, **verdicts}, "eligible": eligible, "reasons": list(reasons),
            "policy": list(policy), "accepted": [dict(item) for item in accepted], "accepted-count": sum(1 for item in accepted if item["state"] == "accepted"),
            "expired-count": sum(1 for item in accepted if item["state"] == "expired"), "record-sha256": sha(f"record of run {run}".encode()),
            "package": dict(package)}


def encoded(said: dict) -> bytes:
    return json.dumps(said, sort_keys=True, separators=(",", ":")).encode("ascii")


def ledger_bytes(lines: list, numbers: tuple = ()) -> bytes:
    """A ledger of the lines, written apart from the module under test: each line gets its number (1, 2, ...
    unless other numbers are given) and the SHA-256 of the bytes of the line before, and ends with a line feed."""
    out, before = b"", ""
    for place, said in enumerate(lines, start=1):
        raw = encoded({**said, "sequence": numbers[place - 1] if numbers else place, "previous": before})
        out += raw + b"\n"
        before = sha(raw)
    return out


def git(repo: Path, *args: str, at: int = 0) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{at} +0000", "GIT_COMMITTER_DATE": f"@{at} +0000"} if at else None
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "-c", "core.autocrlf=false",
                           *args], check=True, capture_output=True, text=True, env=env)
    return done.stdout.strip()


def commit(repo: Path, name: str, at: int) -> str:
    (repo / name).write_bytes(name.encode() + b"\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name, at=at)
    return git(repo, "rev-parse", "HEAD")


def named(said: dict) -> dict:
    return {"key": said["key"], "time": said["time"], "commit": said["head"]}


WORK: Path
REPO: Path
LEDGER: Path
LINES: list
C0: str
H1: str
H2: str
MERGE: str
M1: str
X: str
M2: str
_folder: tempfile.TemporaryDirectory


def setUpModule() -> None:
    """A repository whose branch `main` has five commits on its first-parent line (one before January 2026,
    a merge of a branch with two commits, two in January, one in February), and a ledger of seven runs on
    them. Read by every test of this file, changed by none."""
    global WORK, REPO, LEDGER, LINES, C0, H1, H2, MERGE, M1, X, M2, _folder
    _folder = tempfile.TemporaryDirectory()
    WORK = Path(_folder.name).resolve()
    REPO = WORK / "repo"
    REPO.mkdir()
    subprocess.run(["git", "init", "-q", str(REPO)], check=True)
    git(REPO, "symbolic-ref", "HEAD", "refs/heads/main")
    C0 = commit(REPO, "c0", seconds(2025, 12, 20))
    git(REPO, "checkout", "-q", "-b", "feature")
    H1 = commit(REPO, "h1", seconds(2026, 1, 5, 9))
    H2 = commit(REPO, "h2", seconds(2026, 1, 5, 11))
    git(REPO, "checkout", "-q", "main")
    git(REPO, "merge", "-q", "--no-ff", "feature", "-m", "merge feature", at=seconds(2026, 1, 6))
    MERGE = git(REPO, "rev-parse", "HEAD")
    M1 = commit(REPO, "m1", seconds(2026, 1, 20, 8))
    X = commit(REPO, "x", seconds(2026, 1, 25))
    M2 = commit(REPO, "m2", seconds(2026, 2, 1, 0, 30))
    accepted_a, accepted_b = {**BREAK_A, "state": "accepted"}, {**BREAK_B, "state": "accepted"}
    LINES = [
        line("101", "2026-01-05T10:00:00Z", "12", H1, False, ("boundaries-introduced: FAIL",), accepted=(accepted_a,), boundaries="FAIL", baseline="ACCEPTED"),
        line("102", "2026-01-05T12:00:00Z", "12", H2, policy=({"path": ".wingbox/baseline.sexp", "kind": "baseline", "change": "changed"},),
             accepted=(accepted_a, accepted_b), baseline="ACCEPTED"),
        line("103", "2026-01-10T09:00:00Z", "13", "d" * 40, False, ("tests: none",), tests="none"),
        line("104", "2026-01-20T09:00:00Z", "", M1, package={"manifest-sha256": "1" * 64, "bundle": "2" * 64, "state": "complete"}),
        line("105", "2026-02-01T00:00:00Z", "", M2, False, ("boundaries-introduced: UNKNOWN", "boundaries-existing: UNKNOWN"), boundaries="UNKNOWN",
             existing="UNKNOWN", execution="error", package={"manifest-sha256": "3" * 64, "bundle": "4" * 64, "state": "incomplete"}),
        line("106", "2026-02-01T01:00:00Z", "", M2, base=X),      # X is the base of a run: that is no record of X
        line("107", "2026-02-20T00:00:00Z", "14", "e" * 40, False, ("baseline: 1 past the end date", "tests: failed"),
             accepted=({**BREAK_A, "state": "expired"},), baseline="EXPIRED", tests="failed"),
    ]
    LEDGER = WORK / "ledger.jsonl"
    LEDGER.write_bytes(ledger_bytes(LINES))


def tearDownModule() -> None:
    _folder.cleanup()


def head_of(lines: list) -> str:
    return sha(ledger_bytes(lines).splitlines()[-1])


def january() -> dict:
    """The report of January 2026 for LEDGER, compared with `main` of REPO and with a limit of 200 hours, written out."""
    one, two, three, four = LINES[:4]
    return {
        "schema": "wingbox-period/1",
        "statement": STATEMENT,
        "period": {"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"},
        "ledger": {"lines": 7, "intact": True, "head": head_of(LINES), "problems": []},
        "runs": {"total": 4, "eligible": 2, "failed": 1, "unknown": 1,
                 "by-verdict": {"boundaries": {"FAIL": 1, "PASS": 3}, "existing": {"NONE": 4}, "baseline": {"ACCEPTED": 2, "NONE": 2},
                                "tests": {"complete": 3, "none": 1}, "coverage": {"complete": 4}, "execution": {"ok": 4}},
                 "packages": {"complete": 1, "incomplete": 0, "none": 3}},
        "not-eligible": [
            {**named(one), "change": "12", "outcome": "failed", "reasons": ["boundaries-introduced: FAIL"], "followed-by": named(two)},
            {**named(three), "change": "13", "outcome": "unknown", "reasons": ["tests: none"], "followed-by": {"key": "", "time": "", "commit": ""}},
        ],
        "policy-changes": [{**named(two), "change": "12", "files": [{"path": ".wingbox/baseline.sexp", "kind": "baseline", "change": "changed"}]}],
        "accepted-breaks": {"breaks": [
            {**BREAK_B, "runs": 1, "expired-in": 0, "first": "2026-01-05T12:00:00Z", "last": "2026-01-05T12:00:00Z"},
            {**BREAK_A, "runs": 2, "expired-in": 0, "first": "2026-01-05T10:00:00Z", "last": "2026-01-05T12:00:00Z"},
        ], "not-listed": 0},
        "gaps": {
            "commits": {"compared": True, "branch": "main", "in-period": 3, "with-record": 1,
                        "through-merged-head": [{"commit": MERGE, "time": "2026-01-06T00:00:00Z"}],
                        "without-record": [{"commit": X, "time": "2026-01-25T00:00:00Z"}]},
            "stretches": {"checked": True, "limit-hours": 200, "longer": [
                {"from": "2026-01-10T09:00:00Z", "to": "2026-01-20T09:00:00Z", "hours": 240},
                {"from": "2026-01-20T09:00:00Z", "to": "2026-02-01T00:00:00Z", "hours": 279}]},
        },
        "defects": 3,
        "ok": False,
    }


def period(*args: object) -> tuple:
    """(exit status, the report as a value) of `period --json` with the arguments."""
    status, out, _err = run_cli("period", *args, "--json")
    said = json.loads(out)
    assert (status == 0) == said["ok"] == (said["defects"] == 0), (status, said)
    return status, said


def a_record(time: str, run: str, head: str = "b" * 40) -> dict:
    """A record as the action writes it, of a run with a time, a run id and a head of its own."""
    record = we.build_evidence(report(head=head, tree=pr.Tree(head, "head")), we.Run("github", "owner/repo", "12", run, "", "v1", time, "1", "check"), {})
    return record


class LineTest(unittest.TestCase):
    def test_a_run_is_one_line_added_at_the_end(self) -> None:
        """/* REQ-ACTION-190: `append --record` writes the line of a record at the end of a ledger: the key of the run (provider, repository, run id, attempt, job), the time of the record, the repository, the change, the base, the head and the commit checked, the verdicts, whether the record is eligible and why not, the policy files the change edits, the accepted breaks, the SHA-256 of the record, no package, its number and the SHA-256 of the line before (empty for the first); the line is JSON with its keys in order, no space, one line feed; it meets schema/wingbox-ledger-1.schema.json; a second record gives line 2, which holds the SHA-256 of the bytes of line 1, and the bytes of line 1 are as they were; the command prints the line number and the SHA-256 of the last line and ends with status 0 */"""
        folder = WORK / "line"
        folder.mkdir()
        first, second = a_record("2026-03-01T10:00:00Z", "201"), a_record("2026-03-02T10:00:00Z", "202")
        first["policy"]["changed"] = [{"path": ".wingbox/config.yaml", "kind": "rules", "change": "added"}]
        first["baseline"].update(verdict="EXPIRED", accepted=1, expired=1,
                                 entries=[{**BREAK_A, "file": ".wingbox/baseline.sexp", "reason": "legacy"}],
                                 past=[{**BREAK_B, "file": ".wingbox/baseline.sexp", "reason": ""}])
        for name, record in (("first.json", first), ("second.json", second)):
            (folder / name).write_bytes(as_json(record))
        target = folder / "ledger.jsonl"
        status, out, err = run_cli("append", "--ledger", target, "--record", folder / "first.json")
        expected = {
            "schema": "wingbox-ledger/1", "sequence": 1, "previous": "", "key": "github/owner/repo/201/1/check", "time": "2026-03-01T10:00:00Z",
            "repository": "owner/repo", "change": "12", "base": "a" * 40, "head": "b" * 40, "commit": "b" * 40,
            "verdicts": {"boundaries": "FAIL", "existing": "UNKNOWN", "baseline": "EXPIRED", "tests": "none", "coverage": "incomplete", "execution": "ok"},
            "eligible": False, "reasons": first["eligible-reasons"],
            "policy": [{"path": ".wingbox/config.yaml", "kind": "rules", "change": "added"}],
            "accepted": [{**BREAK_A, "state": "accepted"}, {**BREAK_B, "state": "expired"}], "accepted-count": 1, "expired-count": 1,
            "record-sha256": sha(as_json(first)), "package": NO_PACKAGE,
        }
        self.assertEqual(("FAIL", "UNKNOWN", "none", False), (first["result"]["boundaries"], first["boundaries"]["existing"]["verdict"],
                                                              first["tests"]["evidence"], first["coverage"]["complete"]))
        self.assertEqual(encoded(expected) + b"\n", target.read_bytes())
        self.assertEqual([], schema_problems(expected, LEDGER_SCHEMA))
        self.assertEqual((0, ""), (status, err))
        self.assertEqual([f"ledger: {target}", "line: 1", "key: github/owner/repo/201/1/check", "time: 2026-03-01T10:00:00Z", "package: none",
                          f"head-sha256: {sha(encoded(expected))}", "append: recorded"], out.splitlines())
        status, out, _err = run_cli("append", "--ledger", target, "--record", folder / "second.json")
        self.assertEqual(0, status)
        one, two = target.read_bytes().split(b"\n")[:2]
        self.assertEqual(encoded(expected), one)
        self.assertEqual((2, sha(one), "2026-03-02T10:00:00Z", sha(as_json(second))),
                         tuple(json.loads(two)[key] for key in ("sequence", "previous", "time", "record-sha256")))
        self.assertTrue(target.read_bytes().endswith(two + b"\n"))
        self.assertIn(f"line: 2\nkey: github/owner/repo/202/1/check\ntime: 2026-03-02T10:00:00Z\npackage: none\nhead-sha256: {sha(two)}\nappend: recorded\n", out)
        # The same records added to another file give the same bytes: no clock and no place is in a line.
        again = folder / "elsewhere" / "ledger.jsonl"
        again.parent.mkdir()
        for name in ("first.json", "second.json"):
            run_cli("append", "--ledger", again, "--record", folder / name)
        self.assertEqual(target.read_bytes(), again.read_bytes())

    def test_a_line_names_the_package_of_the_run(self) -> None:
        """/* REQ-ACTION-191: `append --package` reads the record from the package and writes the SHA-256 of its manifest (what a review names, and what stays when reviews are added), its bundle id and its state into the line; an incomplete package that is what its manifest says gives a line with the state `incomplete`; a package that does not verify gives no line: status 1 and the ledger as it was; a package with a review added gives the same manifest digest */"""
        folder = WORK / "packed"
        repo = folder / "repo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        head = commit(repo, "app.py", seconds(2026, 3, 1))
        record = as_json(a_record("2026-03-01T10:00:00Z", "301", head))
        self.assertEqual(0, run_cli("pack", "--run-dir", run_folder(folder / "run", record, ("wingbox-report.md", "wingbox-plan.sexp")), "--out",
                                    folder / "package.zip", "--repo", repo)[0])
        manifest = dict(members_of((folder / "package.zip").read_bytes()))["manifest.json"]
        target = folder / "ledger.jsonl"
        status, out, _err = run_cli("append", "--ledger", target, "--package", folder / "package.zip")
        self.assertEqual(0, status)
        self.assertIn("package: complete\n", out)
        said = json.loads(target.read_bytes())
        self.assertEqual(({"manifest-sha256": sha(manifest), "bundle": json.loads(manifest)["bundle"], "state": "complete"}, sha(record), head),
                         (said["package"], said["record-sha256"], said["commit"]))
        self.assertEqual(0, run_cli("review", folder / "package.zip", "--reviewer", "R", "--decision", "accepted", "--grounds", "read", "--time",
                                    "2026-03-02T00:00:00Z")[0])
        self.assertEqual(sha(manifest), sha(dict(members_of((folder / "package.zip").read_bytes()))["manifest.json"]))
        other = as_json(a_record("2026-03-03T10:00:00Z", "302", "f" * 40))
        self.assertEqual(1, run_cli("pack", "--run-dir", run_folder(folder / "other", other, ("wingbox-report.md", "wingbox-plan.sexp")), "--out",
                                    folder / "incomplete.zip", "--repo", repo)[0])
        self.assertEqual(0, run_cli("append", "--ledger", target, "--package", folder / "incomplete.zip")[0])
        self.assertEqual(("incomplete", 2), tuple(json.loads(target.read_bytes().splitlines()[1])[key] if key == "sequence" else
                                                  json.loads(target.read_bytes().splitlines()[1])["package"]["state"] for key in ("state", "sequence")))
        kept = target.read_bytes()
        broken = folder / "broken.zip"
        broken.write_bytes((folder / "package.zip").read_bytes()[:-1] + b"x")
        status, out, _err = run_cli("append", "--ledger", target, "--package", broken)
        self.assertEqual(1, status)
        self.assertTrue(out.endswith("append: not recorded (the package does not verify)\n"), out)
        self.assertEqual(kept, target.read_bytes())

    def test_a_record_is_recorded_once_and_only_a_record_is(self) -> None:
        """/* REQ-ACTION-192: a record that is in the ledger is not added again: status 0, `append: already recorded` with the number of its line, the ledger as it was; a file that is no record of version 2 gives no line: status 1 and `append: not recorded`; neither changes a byte of the ledger */"""
        folder = WORK / "once"
        folder.mkdir()
        (folder / "record.json").write_bytes(as_json(a_record("2026-03-01T10:00:00Z", "401")))
        (folder / "other.json").write_bytes(as_json(a_record("2026-03-02T10:00:00Z", "402")))
        (folder / "small.json").write_bytes(b'{"result": {"boundaries": "UNKNOWN", "execution": "error", "reasons": ["check-did-not-run"]}}\n')
        target = folder / "ledger.jsonl"
        for name in ("record.json", "other.json"):
            self.assertEqual(0, run_cli("append", "--ledger", target, "--record", folder / name)[0])
        kept = target.read_bytes()
        status, out, _err = run_cli("append", "--ledger", target, "--record", folder / "record.json")
        self.assertEqual(0, status)
        self.assertEqual([f"ledger: {target}", "line: 1", f"head-sha256: {sha(kept.splitlines()[1])}", "append: already recorded"], out.splitlines())
        status, out, _err = run_cli("append", "--ledger", target, "--record", folder / "small.json")
        self.assertEqual(1, status)
        self.assertEqual([f"ledger: {target}", "append: not recorded (not a wingbox-evidence/2 record)"], out.splitlines())
        self.assertEqual(kept, target.read_bytes())


class ChainTest(unittest.TestCase):
    def check(self, data: bytes) -> list:
        """(where, the first words of the detail) of each problem `period` reports for a ledger of these bytes; the status is 1 when there is one."""
        path = WORK / "chain.jsonl"
        path.write_bytes(data)
        status, said = period("--ledger", path, "--from", "2026-01-01", "--to", "2026-12-31")
        self.assertEqual((0 if said["ledger"]["intact"] else 1, said["ledger"]["intact"]), (status, not said["ledger"]["problems"]))
        return [problem["where"] for problem in said["ledger"]["problems"]]

    def test_a_removed_and_a_changed_line_are_each_found_where_the_chain_breaks(self) -> None:
        """/* REQ-ACTION-193: `period` checks the chain of the ledger it reads and ends with status 1 when it is broken: a line removed from the middle is reported at the place of the line after it, which says it is a later line and names a line that is not before it; a line whose content was changed is reported at the next line, which does not name it as it is; removed first lines are reported at line 1; a line that is not a ledger line, a line with another number than its place and a last line without its line end are reported as themselves; a removed and a changed line are each reported once, two lines in each other's place at each broken link; a ledger with carriage returns before its line ends is intact; the report says `chain BROKEN` and names the line */"""
        whole = ledger_bytes(LINES)
        rows = whole.split(b"\n")[:-1]
        self.assertEqual([], self.check(whole))
        self.assertEqual([], self.check(b""))
        self.assertEqual([], self.check(b"\r\n".join(rows) + b"\r\n"))
        self.assertEqual(["line 3"], self.check(b"\n".join(rows[:2] + rows[3:]) + b"\n"))
        edited = json.loads(rows[2])
        edited["eligible"], edited["reasons"] = True, []
        self.assertEqual(["line 4"], self.check(b"\n".join(rows[:2] + [encoded(edited)] + rows[3:]) + b"\n"))
        self.assertEqual(["line 1"], self.check(b"\n".join(rows[2:]) + b"\n"))
        self.assertEqual(["line 2", "line 3"], self.check(b"\n".join(rows[:1] + [b"not a line"] + rows[2:]) + b"\n"))
        self.assertEqual(["line 7"], self.check(whole[:-1]))
        # Lines that name each other rightly but are numbered 1, 2, 4: a number was changed, or lines were renumbered.
        self.assertEqual(["line 3"], self.check(ledger_bytes(LINES[:3], (1, 2, 4))))
        self.assertEqual(["line 1"], self.check(ledger_bytes(LINES[:3], (2, 3, 4))))
        # Two lines in each other's place: neither follows the line before it, and the line after them does not either.
        self.assertEqual(["line 3", "line 4", "line 5"], self.check(b"\n".join(rows[:2] + [rows[3], rows[2]] + rows[4:]) + b"\n"))
        self.assertEqual(["line 3"], self.check(b"\n".join(rows[:2] + [b""] + rows[2:]) + b"\n")[:1])
        path = WORK / "chain.jsonl"
        path.write_bytes(b"\n".join(rows[:2] + rows[3:]) + b"\n")
        status, out, _err = run_cli("period", "--ledger", path, "--from", "2026-01-01", "--to", "2026-12-31")
        self.assertEqual(1, status)
        self.assertIn(f"Ledger: 6 lines, chain BROKEN, last line SHA-256 {sha(rows[-1])}\n- ledger problem, line 3: says it is line 4 and does not name "
                      "the line before it as it is: line 2 was changed, or a line was removed or added before this one\n", out)
        self.assertTrue(out.endswith("\nperiod: failed (1 defect)\n"), out)
        self.assertEqual(6, len(wl.read(path.read_bytes(), LEDGER_SCHEMA).lines))

    def test_a_line_cut_from_the_end_is_found_with_the_head_kept_elsewhere(self) -> None:
        """/* REQ-ACTION-194: the last line of a ledger has no line after it: a ledger without its last lines verifies by itself; with --head-sha256, the SHA-256 of the last line as `append` and `period` printed it, a ledger whose last line is another one is reported and the status is 1; `append` adds nothing to a ledger that does not verify: status 1, the file as it was */"""
        path = WORK / "cut.jsonl"
        path.write_bytes(b"\n".join(ledger_bytes(LINES).split(b"\n")[:5]) + b"\n")
        status, said = period("--ledger", path, "--from", "2026-01-01", "--to", "2026-12-31")
        self.assertEqual((0, True, 5), (status, said["ledger"]["intact"], said["ledger"]["lines"]))
        status, said = period("--ledger", path, "--from", "2026-01-01", "--to", "2026-12-31", "--head-sha256", head_of(LINES))
        self.assertEqual((1, False, ["last line"]), (status, said["ledger"]["intact"], [problem["where"] for problem in said["ledger"]["problems"]]))
        self.assertEqual(0, period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-12-31", "--head-sha256", head_of(LINES).upper())[0])
        rows = ledger_bytes(LINES).split(b"\n")[:-1]
        path.write_bytes(b"\n".join(rows[:2] + rows[3:]) + b"\n")
        kept = path.read_bytes()
        (WORK / "cut-record.json").write_bytes(as_json(a_record("2026-03-01T10:00:00Z", "501")))
        status, out, _err = run_cli("append", "--ledger", path, "--record", WORK / "cut-record.json")
        self.assertEqual(1, status)
        self.assertIn("problem: ledger line 3: says it is line 4 and does not name the line before it as it is", out)
        self.assertTrue(out.endswith("append: not recorded (the ledger does not verify)\n"), out)
        self.assertEqual(kept, path.read_bytes())

    def test_any_one_line_removed_or_changed_is_detected(self) -> None:
        """/* REQ-ACTION-208: for every line of a ledger but the last, the ledger without that line and the ledger with that line changed (a verdict, the time, the record digest) each have a broken chain; with the SHA-256 of the last line kept elsewhere the same holds for the last line; two lines in each other's place break it too */"""
        whole = ledger_bytes(LINES)
        rows = whole.split(b"\n")[:-1]
        noted = sha(rows[-1])

        def broken(changed: list, head: str = "") -> bool:
            book = wl.read(b"\n".join(changed) + b"\n", LEDGER_SCHEMA)
            return bool(book.problems) or bool(head and book.head != head)
        self.assertFalse(broken(rows, noted))
        for place in range(len(rows)):
            last = place == len(rows) - 1
            self.assertEqual(not last, broken(rows[:place] + rows[place + 1:]), place)
            self.assertTrue(broken(rows[:place] + rows[place + 1:], noted), place)
            for key, value in (("eligible", not json.loads(rows[place])["eligible"]), ("time", "2026-06-06T06:06:06Z"), ("record-sha256", "9" * 64)):
                changed = rows[:place] + [encoded({**json.loads(rows[place]), key: value})] + rows[place + 1:]
                self.assertEqual(not last, broken(changed), (place, key))
                self.assertTrue(broken(changed, noted), (place, key))
        for place in range(len(rows) - 1):
            self.assertTrue(broken(rows[:place] + [rows[place + 1], rows[place]] + rows[place + 2:]), place)


class PeriodTest(unittest.TestCase):
    def test_the_report_of_a_period(self) -> None:
        """/* REQ-ACTION-195: `period --from DAY --to DAY` reports the runs whose time is in the period, the last day included and a run at the first second after it left out: the number of runs, of eligible, failed and unknown ones, the runs by the word of each verdict (boundaries, existing, baseline, tests, coverage, execution) and by the state of their package; the whole report with --repo, --branch and --max-gap-hours is the expected value written out, meets schema/wingbox-period-1.schema.json, and the status is 1 because it holds gaps; a moment as a boundary is taken as written, its end left out */"""
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--repo", REPO, "--branch", "main", "--max-gap-hours", "200")
        self.assertEqual(january(), said)
        self.assertEqual(1, status)
        self.assertEqual([], schema_problems(said, PERIOD_SCHEMA))
        status, plain = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual((0, {**january(), "gaps": {"commits": {"compared": False, "branch": "", "in-period": 0, "with-record": 0, "through-merged-head": [],
                                                                "without-record": []},
                                                    "stretches": {"checked": False, "limit-hours": 0, "longer": []}}, "defects": 0, "ok": True}), (status, plain))
        _status, moments = period("--ledger", LEDGER, "--from", "2026-01-05T10:00:00Z", "--to", "2026-01-05T12:00:00Z")
        self.assertEqual((1, {"from": "2026-01-05T10:00:00Z", "to": "2026-01-05T12:00:00Z"}), (moments["runs"]["total"], moments["period"]))
        _status, february = period("--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28")
        self.assertEqual((3, 1, 1, 1, {"complete": 0, "incomplete": 1, "none": 2}, {"error": 1, "ok": 2}),
                         (february["runs"]["total"], february["runs"]["eligible"], february["runs"]["failed"], february["runs"]["unknown"],
                          february["runs"]["packages"], february["runs"]["by-verdict"]["execution"]))
        _status, nothing = period("--ledger", LEDGER, "--from", "2025-01-01", "--to", "2025-12-31")
        self.assertEqual((0, [], {name: {} for name in ("boundaries", "existing", "baseline", "tests", "coverage", "execution")}),
                         (nothing["runs"]["total"], nothing["not-eligible"], nothing["runs"]["by-verdict"]))

    def test_a_failed_run_is_listed_with_what_followed_it(self) -> None:
        """/* REQ-ACTION-196: every run of the period whose record is not eligible is listed with its key, time, commit, change, `failed` (a verdict found a defect: a boundary finding, a violation on the head, a baseline entry past its end date, a failed test) or `unknown`, its reasons, and the first later eligible run on the same change: the same pull or merge request, or outside one the same head; a failed run that was fixed names the run that fixed it, also when that run is after the period; an eligible run on another change does not count, and a run nothing followed says so */"""
        one, two, three, _four, five, six, seven = LINES
        _status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual([("101", "failed", named(two)), ("103", "unknown", {"key": "", "time": "", "commit": ""})],
                         [(item["key"].split("/")[3], item["outcome"], item["followed-by"]) for item in said["not-eligible"]])
        _status, said = period("--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28")
        self.assertEqual([{**named(five), "change": "", "outcome": "unknown", "reasons": ["boundaries-introduced: UNKNOWN", "boundaries-existing: UNKNOWN"],
                           "followed-by": named(six)},
                          {**named(seven), "change": "14", "outcome": "failed", "reasons": ["baseline: 1 past the end date", "tests: failed"],
                           "followed-by": {"key": "", "time": "", "commit": ""}}], said["not-eligible"])
        _status, said = period("--ledger", LEDGER, "--from", "2026-01-05T10:00:00Z", "--to", "2026-01-05T11:00:00Z")
        self.assertEqual([named(two)], [item["followed-by"] for item in said["not-eligible"]])
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual(0, status)
        self.assertIn(f"## Runs that are not eligible\n\n- 2026-01-05T10:00:00Z github/owner/repo/101/1/check commit {H1} change 12: failed "
                      f"(boundaries-introduced: FAIL); followed by an eligible run on the same change: 2026-01-05T12:00:00Z github/owner/repo/102/1/check "
                      f"commit {H2}\n- 2026-01-10T09:00:00Z github/owner/repo/103/1/check commit {'d' * 40} change 13: unknown (tests: none); "
                      "followed by no eligible run on the same change\n", out)
        for words, expected in (({"boundaries": "FAIL"}, "failed"), ({"existing": "PRESENT"}, "failed"), ({"baseline": "EXPIRED"}, "failed"),
                                ({"tests": "failed"}, "failed"), ({"tests": "incomplete"}, "unknown"), ({"coverage": "incomplete"}, "unknown"),
                                ({"boundaries": "UNKNOWN", "execution": "error"}, "unknown")):
            self.assertEqual(expected, wl.outcome(line("1", "2026-01-01T00:00:00Z", eligible=False, **words)), words)

    def test_policy_changes_and_accepted_breaks_of_the_period(self) -> None:
        """/* REQ-ACTION-197: every change of a policy file is listed with the run that showed it (key, time, commit, change) and each file with its kind and what happened to it; a period without one says `None.` */"""
        _status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual(january()["policy-changes"], said["policy-changes"])
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertIn(f"## Policy changes\n\n- 2026-01-05T12:00:00Z github/owner/repo/102/1/check commit {H2} change 12: .wingbox/baseline.sexp (baseline, changed)\n", out)
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28")
        self.assertIn("## Policy changes\n\nNone.\n", out)

    def test_accepted_breaks_open_in_the_period(self) -> None:
        """/* REQ-ACTION-198: the breaks a baseline accepted in a run of the period are listed once each with the owner and the end date, the number of runs that held it, in how many of them it was past its end date, and the first and the last time it was seen; a break without an owner or an end date says `not stated`; a break that later expired is listed with that in the period it happened */"""
        _status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual(january()["accepted-breaks"], said["accepted-breaks"])
        _status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertIn("## Accepted breaks\n\n- capability-known: src/c uses db-client: owner not stated, end date not stated; in 1 run, first "
                      "2026-01-05T12:00:00Z, last 2026-01-05T12:00:00Z\n- violation-known: src/a -> src/b: owner team-a, end date 2026-03-01; in 2 runs, "
                      "first 2026-01-05T10:00:00Z, last 2026-01-05T12:00:00Z\n", out)
        _status, said = period("--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28")
        self.assertEqual({"breaks": [{**BREAK_A, "runs": 1, "expired-in": 1, "first": "2026-02-20T00:00:00Z", "last": "2026-02-20T00:00:00Z"}], "not-listed": 0},
                         said["accepted-breaks"])
        _status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28")
        self.assertIn("- violation-known: src/a -> src/b: owner team-a, end date 2026-03-01; in 1 run, past its end date in 1, first 2026-02-20T00:00:00Z", out)
        many = WORK / "many.jsonl"
        many.write_bytes(ledger_bytes([{**line("601", "2026-01-02T00:00:00Z", accepted=({**BREAK_A, "state": "accepted"},)), "accepted-count": 140}]))
        self.assertEqual(139, period("--ledger", many, "--from", "2026-01-01", "--to", "2026-01-31")[1]["accepted-breaks"]["not-listed"])

    def test_a_commit_of_the_default_branch_without_a_record_is_listed(self) -> None:
        """/* REQ-ACTION-199: with --repo and --branch, `period` compares the ledger with the first-parent commits of the branch whose time is in the period: a commit a line names as its head or as the commit checked has a record, one that is only the base of a run has none; a merge commit that no line names, whose merged head a line names, is listed apart; every other commit is listed as without a record, never skipped, and the status is 1; commits outside the period are not counted; without --repo the report says the commits were not compared; a branch the repository does not have is wrong usage */"""
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--repo", REPO, "--branch", "main")
        self.assertEqual((1, january()["gaps"]["commits"], 1), (status, said["gaps"]["commits"], said["defects"]))
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--repo", REPO, "--branch", "main")
        self.assertEqual(1, status)
        self.assertIn(f"## Gaps\n\nCommits of main in the period: 3; named by a line: 1; only their merged head named: 1; without a record: 1\n"
                      f"- only the merged head has a record: {MERGE} 2026-01-06T00:00:00Z\n- without a record: {X} 2026-01-25T00:00:00Z\n"
                      "Stretches without a run: not looked for (no --max-gap-hours).\n\nperiod: failed (1 defect)\n", out)
        status, said = period("--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-02-28", "--repo", REPO, "--branch", "main")
        self.assertEqual((0, 1, 1, []), (status, said["gaps"]["commits"]["in-period"], said["gaps"]["commits"]["with-record"],
                                         said["gaps"]["commits"]["without-record"]))
        status, said = period("--ledger", LEDGER, "--from", "2025-12-01", "--to", "2025-12-31", "--repo", REPO, "--branch", "main")
        self.assertEqual((1, [{"commit": C0, "time": "2025-12-20T00:00:00Z"}]), (status, said["gaps"]["commits"]["without-record"]))
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--repo", REPO, "--branch", "feature")
        self.assertEqual((0, 2, 2), (status, said["gaps"]["commits"]["in-period"], said["gaps"]["commits"]["with-record"]))
        _status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertIn("Commits of the default branch: not compared (no --repo).\n", out)
        for wrong in (("--repo", REPO, "--branch", "no-such-branch"), ("--repo", REPO, "--branch", "--all"), ("--repo", REPO, "--branch=--all"),
                      ("--repo", WORK / "no-such-folder")):
            status, out, err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", *wrong)
            self.assertEqual((2, ""), (status, out), wrong)
            self.assertNotEqual("", err)

    def test_a_stretch_without_a_run_longer_than_the_limit_is_listed(self) -> None:
        """/* REQ-ACTION-200: with --max-gap-hours N, every stretch of the period without a run that is longer than N hours is listed with its start, its end and its whole hours, the stretch from the start of the period to the first run and from the last run to its end included, and the status is 1; a limit no stretch exceeds lists none and the status is 0; a period without any run is one stretch; without the option the report says stretches were not looked for */"""
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "200")
        self.assertEqual((1, january()["gaps"]["stretches"], 2), (status, said["gaps"]["stretches"], said["defects"]))
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "279")
        self.assertEqual((0, {"checked": True, "limit-hours": 279, "longer": []}), (status, said["gaps"]["stretches"]))
        status, said = period("--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "105")
        self.assertEqual([("2026-01-01T00:00:00Z", "2026-01-05T10:00:00Z", 106), ("2026-01-05T12:00:00Z", "2026-01-10T09:00:00Z", 117),
                          ("2026-01-10T09:00:00Z", "2026-01-20T09:00:00Z", 240), ("2026-01-20T09:00:00Z", "2026-02-01T00:00:00Z", 279)],
                         [(item["from"], item["to"], item["hours"]) for item in said["gaps"]["stretches"]["longer"]])
        status, said = period("--ledger", LEDGER, "--from", "2025-06-01", "--to", "2025-06-30", "--max-gap-hours", "24")
        self.assertEqual((1, [{"from": "2025-06-01T00:00:00Z", "to": "2025-07-01T00:00:00Z", "hours": 720}]), (status, said["gaps"]["stretches"]["longer"]))
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "200")
        self.assertEqual(1, status)
        self.assertIn("Stretches without a run longer than 200 hours: 2\n- 2026-01-10T09:00:00Z to 2026-01-20T09:00:00Z: 240 hours\n"
                      "- 2026-01-20T09:00:00Z to 2026-02-01T00:00:00Z: 279 hours\n\nperiod: failed (2 defects)\n", out)

    def test_the_report_states_counts_and_concludes_nothing(self) -> None:
        """/* REQ-ACTION-201: the report says what it is in its first lines: a count of what the ledger recorded, for a reviewer, which does not conclude that a control operated effectively; that sentence is the only place with the words `effectively`, `control` or `conclude`, and the report holds none of `conform`, `compliant`, `assur`, `satisf` or `passed the`; it names the period with its end left out, the number of lines of the ledger and the SHA-256 of its last line; its last line is `period: ok` or `period: failed (N defects)`; the same ledger and options give the same bytes */"""
        options = ("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-02-28", "--repo", REPO, "--branch", "main", "--max-gap-hours", "100")
        status, out, err = run_cli(*options)
        self.assertEqual((1, ""), (status, err))
        self.assertEqual(["# Evidence over a period", "", STATEMENT, "", "Period: 2026-01-01T00:00:00Z to 2026-03-01T00:00:00Z (the end is not included)",
                          f"Ledger: 7 lines, chain intact, last line SHA-256 {head_of(LINES)}", "", "## Runs", "",
                          "Runs in the period: 7; eligible as evidence: 3; failed: 2; unknown: 2", "- boundaries: FAIL 1, PASS 5, UNKNOWN 1",
                          "- existing: NONE 6, UNKNOWN 1", "- baseline: ACCEPTED 2, EXPIRED 1, NONE 4", "- tests: complete 5, failed 1, none 1",
                          "- coverage: complete 7", "- execution: error 1, ok 6", "- packages: complete 1, incomplete 1, none 5"], out.splitlines()[:17])
        rest = out.replace(STATEMENT, "").lower()
        for word in ("effectively", "control", "conclude", "conform", "compliant", "assur", "satisf", "passed the"):
            self.assertNotIn(word, rest, word)
        self.assertEqual(out, run_cli(*options)[1])
        # Six stretches over 100 hours (106, 117, 240, 279, 455 and 216 hours) and one commit without a record.
        self.assertTrue(out.endswith("\nperiod: failed (7 defects)\n"), out[-200:])
        status, out, _err = run_cli("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31")
        self.assertEqual((0, "period: ok"), (status, out.splitlines()[-1]))
        self.assertEqual(STATEMENT, wl.STATEMENT)
        self.assertEqual(STATEMENT, json.loads(run_cli(*options, "--json")[1])["statement"])


class CommandTest(unittest.TestCase):
    def test_wrong_usage_of_the_ledger_commands_is_status_2(self) -> None:
        """/* REQ-ACTION-202: `append` and `period` end with status 2, print nothing on the standard output and say why on the error stream for: a missing --ledger, neither or both of --record and --package, a record or a package that is not there, a ledger that is not there (period), a date in another form, a start that is not before the end, a limit of no hours, a digest in another form, a ledger in a folder that is not there (append); a defect (a broken ledger, a gap) is status 1 and a period without one status 0 */"""
        folder = WORK / "usage"
        folder.mkdir()
        (folder / "record.json").write_bytes(as_json(a_record("2026-03-01T10:00:00Z", "701")))
        wrong = (
            ("append", "--record", folder / "record.json"), ("append", "--ledger", folder / "l.jsonl"),
            ("append", "--ledger", folder / "l.jsonl", "--record", folder / "record.json", "--package", folder / "p.zip"),
            ("append", "--ledger", folder / "l.jsonl", "--record", folder / "no-such.json"), ("append", "--ledger", folder / "l.jsonl", "--package", folder / "no.zip"),
            ("append", "--ledger", folder / "no-such-folder" / "l.jsonl", "--record", folder / "record.json"),
            ("period", "--from", "2026-01-01", "--to", "2026-01-31"), ("period", "--ledger", folder / "no-such.jsonl", "--from", "2026-01-01", "--to", "2026-01-31"),
            ("period", "--ledger", LEDGER, "--from", "January", "--to", "2026-01-31"), ("period", "--ledger", LEDGER, "--from", "2026-13-01", "--to", "2026-01-31"),
            ("period", "--ledger", LEDGER, "--from", "2026-02-01", "--to", "2026-01-31"), ("period", "--ledger", LEDGER, "--from", "2026-01-01"),
            ("period", "--ledger", LEDGER, "--from", "2026-01-01T00:00:00Z", "--to", "2026-01-01T00:00:00Z"),
            ("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "0"),
            ("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--max-gap-hours", "soon"),
            ("period", "--ledger", LEDGER, "--from", "2026-01-01", "--to", "2026-01-31", "--head-sha256", "abc"),
        )
        for args in wrong:
            status, out, err = run_cli(*args)
            self.assertEqual((2, ""), (status, out), args)
            self.assertNotEqual("", err, args)
        self.assertEqual(["record.json"], [path.name for path in folder.iterdir()])
        script = [sys.executable, "-P", str(ROOT / "wingbox_evidence_cli.py"), "period", "--ledger", str(LEDGER), "--from", "2026-01-01", "--to", "2026-01-31"]
        good = subprocess.run(script, capture_output=True, text=True, cwd=folder)
        self.assertEqual((0, "period: ok"), (good.returncode, good.stdout.splitlines()[-1]))
        defect = subprocess.run([*script, "--max-gap-hours", "200"], capture_output=True, text=True, cwd=folder)
        self.assertEqual((1, "period: failed (2 defects)"), (defect.returncode, defect.stdout.splitlines()[-1]))

    def test_the_ledger_module_reads_no_clock_and_starts_nothing(self) -> None:
        """/* REQ-ACTION-205: wingbox_ledger.py imports only calendar, json, re, time, dataclasses, typing and wingbox_package, and calls no clock (`time.time`, `time.localtime`, `datetime`): the time of a line is the time of its record; the command script starts no program but git, with the words `cat-file` and `rev-list`, and a branch name that starts with a hyphen is refused before git is started */"""
        tree = ast.parse((ROOT / "wingbox_ledger.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual({"__future__", "calendar", "json", "re", "time", "dataclasses", "typing", "wingbox_package"}, names)
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "time"}
        self.assertEqual({"strptime", "strftime", "gmtime"}, used)
        gmtime = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "gmtime"]
        self.assertEqual([1], [len(node.args) for node in gmtime])
        text = (ROOT / "wingbox_evidence_cli.py").read_text(encoding="utf-8")
        self.assertEqual((2, 1, 4), (text.count('_git(repo, "cat-file", '), text.count('_git(repo, "rev-list", '), text.count("_git(")))
        self.assertLess(text.index('if branch.startswith("-"):'), text.index('_git(repo, "rev-list", '))


class ActionTest(unittest.TestCase):
    def test_the_action_and_gitlab_add_a_line_when_asked(self) -> None:
        """/* REQ-ACTION-204: action.yml has the input `ledger`, empty by default, handed to both steps that write a record as WINGBOX_LEDGER, and the output `ledger-head`; with WINGBOX_LEDGER naming a file the scripts add the line of the run at its end, from the package when one was written and else from the record, make the folder of the file, and give the SHA-256 of the last line; a path that is not absolute starts at the workspace of the job; a ledger that does not verify gives no line and status 1; without the variable nothing is written or said; in GitLab the same variable, the SHA-256 as WINGBOX_LEDGER_HEAD in wingbox.env, and the shell-runner template hands the variable to the container */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  ledger:\n    description: [^\n]+\n    default: ""\n')
        self.assertEqual(2, action.count("WINGBOX_LEDGER: ${{ inputs.ledger }}"))
        self.assertRegex(action, r"\n  ledger-head:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.ledger-head \|\| steps\.did-not-run\.outputs\.ledger-head \}\}\n")
        self.assertRegex((ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8"), r"--env WINGBOX_LEDGER\s")
        folder = WORK / "action"
        repo = folder / "repo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        head = commit(repo, "app.py", seconds(2026, 3, 1))
        out = run_folder(folder / "out", as_json(a_record("2026-03-01T10:00:00Z", "801", head)), ("wingbox-report.md", "wingbox-plan.sexp"))
        said: list = []
        self.assertEqual(("", 0), pr.write_ledger({}, str(out), "", str(repo), said.append))
        self.assertEqual(([], ["wingbox-evidence.json"]), (said, [path.name for path in out.iterdir()]))
        env = {"WINGBOX_LEDGER": "kept/ledger.jsonl", "GITHUB_WORKSPACE": str(folder / "workspace")}
        digest, status = pr.write_ledger(env, str(out), "", str(repo), said.append)
        target = folder / "workspace" / "kept" / "ledger.jsonl"
        self.assertEqual((sha(target.read_bytes().rstrip(b"\n")), 0, "append: recorded"), (digest, status, said[-1]))
        self.assertEqual(("none", "github/owner/repo/801/1/check"), (json.loads(target.read_bytes())["package"]["state"], json.loads(target.read_bytes())["key"]))
        (folder / "out2").mkdir()
        shutil.copyfile(out / "wingbox-evidence.json", folder / "out2" / "wingbox-evidence.json")
        record = json.loads((out / "wingbox-evidence.json").read_bytes())
        record["generated"] = "2026-03-01T11:00:00Z"
        (folder / "out2" / "wingbox-evidence.json").write_bytes(as_json(record))
        path, state, _status = pr.write_package({"WINGBOX_PACKAGE": "true"}, str(folder / "out2"), str(repo), {}, said.append)
        digest, status = pr.write_ledger({"WINGBOX_LEDGER": str(target)}, str(folder / "out2"), path, str(repo), said.append)
        second = json.loads(target.read_bytes().splitlines()[1])
        self.assertEqual(("complete", 0, sha(target.read_bytes().splitlines()[1]), 2, sha(dict(members_of(Path(path).read_bytes()))["manifest.json"])),
                         (state, status, digest, second["sequence"], second["package"]["manifest-sha256"]))
        target.write_bytes(target.read_bytes().splitlines()[1] + b"\n")
        kept = target.read_bytes()
        self.assertEqual(("", 1), pr.write_ledger({"WINGBOX_LEDGER": str(target)}, str(out), "", str(repo), said.append)[:2])
        self.assertEqual((kept, "append: not recorded (the ledger does not verify)"), (target.read_bytes(), said[-1]))
        import wingbox_gitlab
        self.assertEqual("WINGBOX_RESULT=PASS\nWINGBOX_PACKAGE_STATE=complete\nWINGBOX_LEDGER_HEAD=abc\n",
                         wingbox_gitlab.dotenv({"result": "PASS", **wingbox_gitlab.package_values("complete", "abc")}))
        self.assertEqual({"ledger-head": "abc"}, wingbox_gitlab.package_values("", "abc"))

    def test_the_documents_describe_the_ledger_and_the_period(self) -> None:
        """/* REQ-ACTION-206: README.md has the section "Evidence over a period" with the two commands as `python wingbox_evidence_cli.py`, the fields of a line and its schema, how the chain is checked by hand and what it cannot show without the head kept elsewhere, the parts of the report, the gaps and their options, the sentence that the report does not conclude that a control operated effectively, the input `ledger` with its output and the GitLab variable, and that one writer at a time adds to a ledger; CHANGELOG.md names the commands and the input; the two schemas are in schema/ and each names itself */"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("\n## Evidence over a period\n", 1)[1].split("\n## ", 1)[0]
        for needed in ("python wingbox_evidence_cli.py append --ledger", "python wingbox_evidence_cli.py period --ledger", "schema/wingbox-ledger-1.schema.json",
                       "schema/wingbox-period-1.schema.json", "sha256sum", "--head-sha256", "--max-gap-hours", "--repo", "--branch",
                       "does not conclude that a control operated effectively", "`failed`", "`unknown`", "only their merged head", "WINGBOX_LEDGER",
                       "ledger-head", "`concurrency`", "period: ok", "append: recorded"):
            self.assertIn(needed, section, needed)
        for needed in ("| `ledger` |", "| `ledger-head` |", "WINGBOX_LEDGER_HEAD"):
            self.assertIn(needed, readme, needed)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for needed in ("python wingbox_evidence_cli.py append", "python wingbox_evidence_cli.py period", "`ledger`", "schema/wingbox-ledger-1.schema.json"):
            self.assertIn(needed, changelog, needed)
        for name, word in (("wingbox-ledger-1.schema.json", "wingbox-ledger/1"), ("wingbox-period-1.schema.json", "wingbox-period/1")):
            schema = json.loads((ROOT / "schema" / name).read_text(encoding="utf-8"))
            self.assertEqual(([word], f"https://github.com/aileron-tech/wingbox-action/blob/main/schema/{name}"),
                             (schema["properties"]["schema"]["enum"], schema["$id"]))
        self.assertEqual(("wingbox-ledger/1", "wingbox-period/1"), (wl.SCHEMA, wl.PERIOD_SCHEMA))


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class RunTest(unittest.TestCase):
    def test_a_run_with_a_ledger_adds_its_line_and_the_period_names_the_commit_without_a_record(self) -> None:
        """/* REQ-ACTION-207: run as on a runner on the fixture repository with WINGBOX_PACKAGE true and WINGBOX_LEDGER naming a file, the script adds one line to the ledger that names the head of the run and the SHA-256 of the manifest of its package, gives the output `ledger-head`, and ends with status 0; `period --repo` on that ledger for the day of the run counts one run and lists the base commit of the fixture, which no run checked, as without a record, with status 1; the fixture checkout stays clean */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, temp = Path(tmp) / "repo", Path(tmp) / "runner"
            temp.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            target = Path(tmp) / "kept" / "ledger.jsonl"
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_"))}
            env.update(WINGBOX_BASE=base, WINGBOX_HEAD=head, WINGBOX_COMMENT="false", WINGBOX_PACKAGE="true", WINGBOX_LEDGER=str(target),
                       RUNNER_TEMP=str(temp), GITHUB_OUTPUT=str(temp / "output.txt"))
            done = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_pr.py")], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            outputs = dict(line.split("=", 1) for line in (temp / "output.txt").read_text(encoding="utf-8").splitlines())
            rows = target.read_bytes().splitlines()
            self.assertEqual((1, sha(rows[0])), (len(rows), outputs["ledger-head"]))
            said = json.loads(rows[0])
            manifest = dict(members_of((temp / "wingbox-package.zip").read_bytes()))["manifest.json"]
            self.assertEqual((1, "", head, head, sha(manifest), "complete", sha((temp / "wingbox-evidence.json").read_bytes())),
                             (said["sequence"], said["previous"], said["head"], said["commit"], said["package"]["manifest-sha256"],
                              said["package"]["state"], said["record-sha256"]))
            self.assertEqual([], schema_problems(said, LEDGER_SCHEMA))
            day = said["time"][:10]
            status, found = period("--ledger", target, "--from", day, "--to", day, "--repo", repo)
            commits = found["gaps"]["commits"]
            self.assertEqual((1, 1, 2, 1, [base]), (status, found["runs"]["total"], commits["in-period"], commits["with-record"],
                                                    [item["commit"] for item in commits["without-record"]]))
            self.assertEqual("", subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout)


if __name__ == "__main__":
    unittest.main()

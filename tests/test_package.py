"""The evidence package: one archive per run with a manifest, built, checked and reviewed by a command.

What is expected here is written by hand and computed apart from the modules under test: digests with
hashlib, the archive with struct (`zip_bytes`, a second writer of the form the README describes), the
manifest as a literal, the schema check with the validator of test_evidence_record.
"""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_evidence_cli as cli  # noqa: E402
import wingbox_package as pkg  # noqa: E402
import wingbox_policy  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_evidence_record import problems as schema_problems  # noqa: E402
from test_test_evidence import with_tests  # noqa: E402

RUN = we.Run("github", "owner/repo", "12", "7", "https://github.com/owner/repo/actions/runs/7", "v1", "2026-01-02T03:04:05Z", "2", "check")
BASELINE = b'(baseline . src/a src/b :owner "team" :reason "legacy" :expires "2027-01-01")\n'
WORKFLOW = b"name: check\non: pull_request\njobs: {}\n"
APP = b"print('app')\n"
JUNIT = b'<?xml version="1.0"?>\r\n<testsuite name="tests/order.test.ts"><testcase classname="x" name="a"/></testsuite>\xff\r\n'
SIDECAR = b'{"commit": "0000000", "run-id": "7", "attempt": "2", "command": "pytest", "sha256": "00"}\n'
REPORT, PLAN, LOG = b"report\n", b"plan\n", b"line one\r\nline two\n"
REQUIRED = [
    {"group": "billing", "test": "tests/test_billing.py::test_total", "status": "passed", "cases": 1,
     "records": [{"where": "tests.test_billing", "name": "test_total", "status": "passed"}]},
    {"group": "billing", "test": "tests/test_refund.py", "status": "partial", "cases": 2,
     "records": [{"where": "tests.test_refund", "name": "test_all", "status": "passed"}, {"where": "tests.test_refund", "name": "test_part", "status": "skipped"}]},
    {"group": "billing", "test": "pkg/billing/billing_test.go", "status": "partial", "cases": 1,
     "records": [{"where": "pkg/billing", "name": "TestBill", "status": "passed"}, {"where": "pkg/billing", "name": "TestRefund", "status": "no-record"}]},
    {"group": "orders", "test": "tests/test_orders.py", "status": "no-record", "cases": 0, "records": []},
]
TO_RUN = [
    {"test": "tests/invoice.test", "status": "failed", "cases": 1, "records": [{"where": "x", "name": "a", "status": "failed"}]},
    {"test": "tests/order.test", "status": "skipped", "cases": 1, "records": [{"where": "x", "name": "b", "status": "skipped"}]},
    {"test": "tests/none.test", "status": "none", "cases": 0, "records": []},
]
# The same tests as a reviewer reads them: one outcome each, one per case.
CHECKS = [
    {"id": "tests/test_billing.py::test_total", "kind": "required-test", "group": "billing", "outcome": "PASS",
     "cases": [{"id": "tests.test_billing::test_total", "outcome": "PASS"}]},
    {"id": "tests/test_refund.py", "kind": "required-test", "group": "billing", "outcome": "SKIP",
     "cases": [{"id": "tests.test_refund::test_all", "outcome": "PASS"}, {"id": "tests.test_refund::test_part", "outcome": "SKIP"}]},
    {"id": "pkg/billing/billing_test.go", "kind": "required-test", "group": "billing", "outcome": "NOT RUN",
     "cases": [{"id": "pkg/billing::TestBill", "outcome": "PASS"}, {"id": "pkg/billing::TestRefund", "outcome": "NOT RUN"}]},
    {"id": "tests/test_orders.py", "kind": "required-test", "group": "orders", "outcome": "NOT RUN", "cases": []},
    {"id": "tests/invoice.test", "kind": "uses-what-changed", "group": "", "outcome": "FAIL", "cases": [{"id": "x::a", "outcome": "FAIL"}]},
    {"id": "tests/order.test", "kind": "uses-what-changed", "group": "", "outcome": "SKIP", "cases": [{"id": "x::b", "outcome": "SKIP"}]},
    {"id": "tests/none.test", "kind": "uses-what-changed", "group": "", "outcome": "UNKNOWN", "cases": []},
]
STATEMENTS = {
    "check": "The record states which checks ran and how each ended.",
    "acceptance": "A review record states that a person accepted or rejected this evidence; the tool makes none.",
    "conformity": "Nothing in this package states that a control conforms.",
}
SCHEMA_NAMES = ("wingbox-evidence-2.schema.json", "wingbox-package-1.schema.json", "wingbox-review-1.schema.json")
PACKAGE_SCHEMA = json.loads((ROOT / "schema" / "wingbox-package-1.schema.json").read_text(encoding="utf-8"))
REVIEW_SCHEMA = json.loads((ROOT / "schema" / "wingbox-review-1.schema.json").read_text(encoding="utf-8"))
RECORD_SCHEMA = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def schema_file(name: str) -> bytes:
    """A schema as a package holds it: the file of this repository with line feeds."""
    return (ROOT / "schema" / name).read_bytes().replace(b"\r\n", b"\n")


def zip_bytes(members: list, comment: bytes = b"", method: int = 0, modes: tuple = ()) -> bytes:
    """A zip archive in the form the README describes, written apart from the module under test: stored
    members in the order given, dated 1980-01-01, no extra field, the mode of a regular file."""
    body, directory = b"", b""
    for name, data in members:
        raw = name.encode("latin-1")
        checksum = zlib.crc32(data)
        directory += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 0x0314, 20, 0, method, 0, 0x21, checksum, len(data), len(data), len(raw),
                                 0, 0, 0, 0, dict(modes).get(name, 0o100644) << 16, len(body)) + raw
        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, method, 0, 0x21, checksum, len(data), len(data), len(raw), 0) + raw + data
    return body + directory + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(members), len(members), len(directory), len(body), len(comment)) + comment


def members_of(data: bytes) -> list:
    """(name, bytes) of every member, in the order of the archive, read by the zipfile module."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def as_json(value: object) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("ascii")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "-c", "core.autocrlf=false",
                           *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(repo: Path, files: dict) -> str:
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "files")
    return git(repo, "rev-parse", "HEAD")


def record_of(checked: str, reports: tuple = (("test-results/junit.xml", JUNIT),), files: tuple = (("app.py", APP),), **changes: object) -> dict:
    """The record of a run on the commit, built by the module that writes records, with its tests set by hand."""
    full = with_tests(head=checked, tree=pr.Tree(checked, "head"), hashes=tuple((path, sha(data)) for path, data in files), test_cases=(),
                      test_reports=tuple(wt.TestReport(path, sha(data), 1) for path, data in reports))
    record = we.build_evidence(full, RUN, {"wingbox-report.md": REPORT.decode(), "wingbox-plan.sexp": PLAN.decode()})
    record["tests"]["required"], record["tests"]["to-run"] = copy.deepcopy(REQUIRED), copy.deepcopy(TO_RUN)
    record.update(changes)
    return record


def run_folder(folder: Path, record: object, leave_out: tuple = ()) -> Path:
    folder.mkdir(parents=True)
    for name, data in (("wingbox-evidence.json", record if isinstance(record, bytes) else as_json(record)), ("wingbox-report.md", REPORT),
                       ("wingbox-plan.sexp", PLAN)):
        if name not in leave_out:
            (folder / name).write_bytes(data)
    return folder


def run_cli(*args: object) -> tuple:
    """(exit status, what the command printed, what it said on the error stream), run in this process."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = cli.main([str(arg) for arg in args])
        except SystemExit as stop:
            status = stop.code
    return status, out.getvalue(), err.getvalue()


def found(path: Path, *more: object) -> list:
    """(code, member) of every problem `verify --json` reports for the archive."""
    status, out, _err = run_cli("verify", path, "--json", *more)
    said = json.loads(out)
    assert (status == 0) == said["ok"] == (not said["problems"]), (status, said)
    return [(problem["code"], problem["member"]) for problem in said["problems"]]


WORK: Path
REPO: Path
COMMIT: str
RECORD: bytes
RUN_DIR: Path
LOG_FILE: Path
COMPLETE: Path        # a complete package of the run, with a job log, the workflow and stated versions
PLAIN: Path           # a complete package with nothing stated
_folder: tempfile.TemporaryDirectory


def setUpModule() -> None:
    """A repository with one commit whose working folder was edited afterwards, the folder of a run on that
    commit, and two packages of it. Read by every test of this file, changed by none."""
    global WORK, REPO, COMMIT, RECORD, RUN_DIR, LOG_FILE, COMPLETE, PLAIN, _folder
    _folder = tempfile.TemporaryDirectory()
    WORK = Path(_folder.name).resolve()
    REPO = WORK / "repo"
    REPO.mkdir()
    subprocess.run(["git", "init", "-q", str(REPO)], check=True)
    COMMIT = commit(REPO, {".wingbox/baseline.sexp": BASELINE, ".github/workflows/check.yml": WORKFLOW, "app.py": APP})
    # After the commit: the working folder has another baseline and a rule file no commit holds.
    (REPO / ".wingbox" / "baseline.sexp").write_bytes(b"(baseline . src/x src/y)\n")
    (REPO / ".wingbox" / "config.yaml").write_bytes(b"layers: []\n")
    (REPO / "test-results").mkdir()
    (REPO / "test-results" / "junit.xml").write_bytes(JUNIT)
    (REPO / "test-results" / "junit.xml.run.json").write_bytes(SIDECAR)
    RECORD = as_json(record_of(COMMIT))
    RUN_DIR = run_folder(WORK / "run", RECORD)
    LOG_FILE = WORK / "job.log"
    LOG_FILE.write_bytes(LOG)
    COMPLETE, PLAIN = WORK / "complete.zip", WORK / "plain.zip"
    assert run_cli("pack", "--run-dir", RUN_DIR, "--out", COMPLETE, "--repo", REPO, "--job-log", LOG_FILE, "--workflow",
                   ".github/workflows/check.yml", "--stated", "model=m-1", "--stated", "build=41", "--retention", "90 days")[0] == 0
    assert run_cli("pack", "--run-dir", RUN_DIR, "--out", PLAIN, "--repo", REPO)[0] == 0


def tearDownModule() -> None:
    _folder.cleanup()


def entry(name: str, path: str, role: str, media: str, data: bytes, required: bool, source: str, checked: str = "") -> dict:
    return {"id": name, "path": path, "role": role, "media-type": media, "length": len(data), "sha256": sha(data), "required": required,
            "source": source, "commit": checked}


def expected_manifest() -> dict:
    """The manifest of COMPLETE, written out."""
    members = [
        entry("policy:.github/workflows/check.yml", "policy/.github/workflows/check.yml", "policy", "application/yaml", WORKFLOW, False,
              ".github/workflows/check.yml", COMMIT),
        entry("policy:.wingbox/baseline.sexp", "policy/.wingbox/baseline.sexp", "policy", "text/plain", BASELINE, False, ".wingbox/baseline.sexp", COMMIT),
        entry("record", "record/wingbox-evidence.json", "record", "application/json", RECORD, True, "wingbox-evidence.json"),
        entry("document:wingbox-plan.sexp", "record/wingbox-plan.sexp", "document", "text/plain", PLAN, True, "wingbox-plan.sexp"),
        entry("document:wingbox-report.md", "record/wingbox-report.md", "document", "text/markdown", REPORT, True, "wingbox-report.md"),
        *(entry(f"schema:{name}", f"schema/{name}", "schema", "application/json", schema_file(name), True, "") for name in SCHEMA_NAMES),
        entry("job-log", "sources/job-log/job.log", "job-log", "text/plain", LOG, False, "job.log"),
        entry("test-report:test-results/junit.xml", "sources/test-reports/test-results/junit.xml", "test-report", "application/xml", JUNIT, True,
              "test-results/junit.xml"),
        entry("sidecar:test-results/junit.xml", "sources/test-reports/test-results/junit.xml.run.json", "test-report-sidecar", "application/json",
              SIDECAR, False, "test-results/junit.xml.run.json"),
    ]

    def present(name: str, path: str) -> dict:
        return {"id": name, "path": path, "state": "present", "reason": ""}
    return {
        "schema": "wingbox-package/1",
        "bundle": sha("".join(f"{member['sha256']}  {member['path']}\n" for member in members).encode("ascii")),
        "state": "complete",
        "created": "2026-01-02T03:04:05Z",
        "tool": {"name": "wingbox-action", "ref": "v1", "wingbox": "0.7.2"},
        "subject": {"repository": "owner/repo", "base": "a" * 40, "head": COMMIT, "checked": COMMIT, "tree": "head"},
        "run": {"provider": "github", "id": "7", "attempt": "2", "job": "check", "change": "12", "url": "https://github.com/owner/repo/actions/runs/7"},
        "retention": "90 days",
        "control": {"mapping": "none", "id": "", "edition": "", "scope": "", "owner": "", "applies": "", "risk": ""},
        "stated": [{"name": "build", "value": "41"}, {"name": "model", "value": "m-1"}],
        "policy": {"commit": COMMIT, "read": True, "files": [
            {"path": ".github/workflows/check.yml", "kind": "workflow", "state": "present", "member": "policy:.github/workflows/check.yml"},
            {"path": ".wingbox/baseline.sexp", "kind": "baseline", "state": "present", "member": "policy:.wingbox/baseline.sexp"},
            {"path": ".wingbox/config.yaml", "kind": "rules", "state": "not-in-commit", "member": ""},
            {"path": ".wingbox/learned.sexp", "kind": "rules", "state": "not-in-commit", "member": ""},
            {"path": ".wingbox/required-tests.yaml", "kind": "required-tests", "state": "not-in-commit", "member": ""},
        ]},
        "checks": CHECKS,
        "members": members,
        "required": [
            present("record", "record/wingbox-evidence.json"),
            present("document:wingbox-plan.sexp", "record/wingbox-plan.sexp"),
            present("document:wingbox-report.md", "record/wingbox-report.md"),
            present("test-report:test-results/junit.xml", "sources/test-reports/test-results/junit.xml"),
            present("policy-files", ""),
            *(present(f"schema:{name}", f"schema/{name}") for name in SCHEMA_NAMES),
        ],
        "optional": [{"id": "job-log", "state": "present"}],
        "absent": [],
        "statements": STATEMENTS,
        "attestation": {"member": "attestation/manifest.json.attestation", "over": "manifest.json"},
    }


def rewritten(source: Path, target: Path, change) -> Path:
    """A copy of an archive with its members changed by `change` (a list of (name, bytes) in, one out), written
    by the writer of this file."""
    target.write_bytes(zip_bytes(change(members_of(source.read_bytes()))))
    return target


def with_manifest(members: list, edit) -> list:
    """The members with the manifest edited as a value and written as JSON with two spaces."""
    said = json.loads(dict(members)["manifest.json"])
    edit(said)
    return [(name, as_json(said) if name == "manifest.json" else data) for name, data in members]


class NamesTest(unittest.TestCase):
    def test_a_member_name_is_a_safe_relative_path(self) -> None:
        """/* REQ-ACTION-170: a member name is safe when it is a relative path of segments made of A-Z a-z 0-9 and `. _ % + = @ , -`; an empty name, an absolute path, a segment `.` or `..`, an empty segment, a backslash, a colon, a space, a control or non-ASCII character, a segment that ends with a dot, a device name of Windows and a name over 240 characters are each refused with a reason; a path of a repository is turned into a safe name with %XX, and two paths give two names */"""
        for name in ("manifest.json", "record/wingbox-evidence.json", "sources/test-reports/a%20b/junit.xml", "policy/.wingbox/baseline.sexp",
                     "reviews/0001.json", "a/b+c=d@e,f-g_h.i"):
            self.assertEqual("", pkg.unsafe(name), name)
        for name in ("", "/etc/passwd", "../x", "a/../b", "a/./b", "a//b", "a/", "a\\b", "C:/x", "a b", "a\x00b", "caf\u00e9", "NUL", "aux.txt",
                     "a/CON/b", "lpt1.log", "dot.", "x" * 241):
            self.assertNotEqual("", pkg.unsafe(name), name)
        turned = {"test-results/junit.xml": "test-results/junit.xml", "a b/c.xml": "a%20b/c.xml", "../up/x.xml": "%2E%2E/up/x.xml",
                  "/abs/x.xml": "%2F/abs/x.xml", "C:\\r\\x.xml": "C%3A/r/x.xml", "100%.xml": "100%25.xml", "caf\u00e9.xml": "caf%C3%A9.xml",
                  "nul.xml": "%6Eul.xml", "dir./x": "dir%2E/x", "./x": "%2E/x", "a%20b/c.xml": "a%2520b/c.xml"}
        for path, name in turned.items():
            self.assertEqual(name, pkg.safe_name(path), path)
            self.assertEqual("", pkg.unsafe("sources/test-reports/" + name), path)
        self.assertEqual(len(turned), len(set(turned.values())))
        self.assertEqual("sources/test-reports/test%20results/junit.xml", pkg.report_path("test results/junit.xml"))
        with self.assertRaises(ValueError):
            pkg.archive([("../x", b"")])
        with self.assertRaises(ValueError):
            pkg.archive([("a", b""), ("a", b"")])


class ArchiveTest(unittest.TestCase):
    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        """/* REQ-ACTION-171: `pack` is deterministic: two packages of the same run are the same bytes, also when the run folder is a copy elsewhere with other file dates; the archive is a zip file that the zipfile module reads, with the manifest first and the other members by path, every member stored without compression, dated 1980-01-01 00:00:00, with no extra field and no comment and the mode of a regular file; a second writer of that form, written apart, gives the same bytes */"""
        again = WORK / "again.zip"
        elsewhere = WORK / "elsewhere"
        shutil.copytree(RUN_DIR, elsewhere / "copy of run")
        for path in (elsewhere / "copy of run").iterdir():
            os.utime(path, (1_000_000_000, 1_000_000_000))
        other_log = elsewhere / "job.log"
        other_log.write_bytes(LOG)
        options = ("--repo", REPO, "--workflow", ".github/workflows/check.yml", "--stated", "build=41", "--stated", "model=m-1", "--retention", "90 days")
        self.assertEqual(0, run_cli("pack", "--run-dir", elsewhere / "copy of run", "--out", again, "--job-log", other_log, *options)[0])
        data = COMPLETE.read_bytes()
        self.assertEqual(data, again.read_bytes())
        members = members_of(data)
        names = [name for name, _data in members]
        self.assertEqual("manifest.json", names[0])
        self.assertEqual(sorted(names[1:]), names[1:])
        self.assertEqual(data, zip_bytes(members))
        self.assertEqual(data, pkg.archive(members))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(b"", archive.comment)
            self.assertIsNone(archive.testzip())
            for info in archive.infolist():
                self.assertEqual(((1980, 1, 1, 0, 0, 0), zipfile.ZIP_STORED, b"", b"", 0o100644, 3, info.file_size),
                                 (info.date_time, info.compress_type, info.extra, info.comment, info.external_attr >> 16, info.create_system,
                                  info.compress_size), info.filename)

    def test_an_archive_that_is_unsafe_to_handle_is_refused(self) -> None:
        """/* REQ-ACTION-179: `verify` refuses, with exit status 1 and the member named, an archive with a member name that leaves the folder, is absolute, holds a backslash or a drive letter; a member that is a symbolic link; a compressed member; an archive with a comment, with bytes after its end or before its first member; a file that is no archive; an archive the zipfile module wrote in its own form; and an archive larger than --max-bytes. It reads the archive in memory: nothing is unpacked and the folder it runs in stays empty */"""
        members = members_of(PLAIN.read_bytes())
        folder = WORK / "unsafe"
        folder.mkdir()
        cases = {}
        for index, name in enumerate(("../evil.txt", "/abs.txt", "a\\b.txt", "C:/x.txt", "record/../../up.txt")):
            (folder / f"name{index}.zip").write_bytes(zip_bytes(members + [(name, b"x")]))
            cases[f"name{index}.zip"] = [("unsafe-member-name", name.replace("\\", "\\\\"))]
        (folder / "link.zip").write_bytes(zip_bytes(members + [("record/link", b"/etc/passwd")], modes=(("record/link", 0o120777),)))
        cases["link.zip"] = [("link-member", "record/link")]
        (folder / "packed.zip").write_bytes(zip_bytes(members, method=8))
        cases["packed.zip"] = [("archive", "manifest.json")]
        (folder / "comment.zip").write_bytes(zip_bytes(members, comment=b"hello"))
        (folder / "after.zip").write_bytes(zip_bytes(members) + b"more")
        (folder / "before.zip").write_bytes(b"#!/bin/sh\n" + zip_bytes(members))
        (folder / "text.zip").write_bytes(b"not an archive\n")
        with zipfile.ZipFile(folder / "module.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                archive.writestr(name, data)
        for name in ("comment.zip", "after.zip", "before.zip", "text.zip"):
            cases[name] = [("archive", "")]
        empty = folder / "cwd"
        empty.mkdir()
        before = os.getcwd()
        os.chdir(empty)
        try:
            for name, expected in cases.items():
                self.assertEqual(expected, found(folder / name), name)
            self.assertEqual(["archive"], [code for code, _member in found(folder / "module.zip")])
            self.assertEqual([("too-large", "")], found(PLAIN, "--max-bytes", "100"))
            status, out, _err = run_cli("verify", folder / "link.zip")
            self.assertEqual(1, status)
            self.assertIn("integrity: refused\n", out)
            self.assertIn("problem: link-member record/link: a symbolic link\n", out)
            self.assertTrue(out.endswith("verify: failed (1 problem)\n"), out)
        finally:
            os.chdir(before)
        self.assertEqual([], list(empty.iterdir()))
        self.assertEqual(sorted([*cases, "module.zip", "cwd"]), sorted(path.name for path in folder.iterdir()))


class ManifestTest(unittest.TestCase):
    def test_the_manifest_of_a_complete_package(self) -> None:
        """/* REQ-ACTION-172: `pack` writes manifest.json as the first member: the schema `wingbox-package/1`, the bundle id (the SHA-256 of the lines `<sha256>  <path>` of the members by path), `complete`, the time of the record, the tool, the subject, the run with its address, the retention and the versions stated, the control marked as not mapped, the policy files with the commit they come from, the checks, every member with id, path, role, media type, length, SHA-256, whether it is required, its source and commit, the required members each `present`, the job log as optional, no absent member, the three statements and the place of an attestation; it meets schema/wingbox-package-1.schema.json; the command ends with status 0 and its last line is `pack: complete` */"""
        members = dict(members_of(COMPLETE.read_bytes()))
        said = json.loads(members["manifest.json"])
        expected = expected_manifest()
        self.assertEqual(expected, said)
        self.assertEqual(as_json(expected), members["manifest.json"])
        self.assertEqual([], schema_problems(said, PACKAGE_SCHEMA))
        self.assertEqual(sorted(["manifest.json", *(member["path"] for member in expected["members"])]), sorted(members))
        out = WORK / "said.zip"
        status, text, err = run_cli("pack", "--run-dir", RUN_DIR, "--out", out, "--repo", REPO)
        self.assertEqual((0, ""), (status, err))
        self.assertEqual([f"package: {out}", f"bundle: {json.loads(dict(members_of(out.read_bytes()))['manifest.json'])['bundle']}",
                          f"manifest-sha256: {sha(dict(members_of(out.read_bytes()))['manifest.json'])}", "state: complete", "members: 9",
                          "pack: complete"], text.splitlines())
        plain = json.loads(dict(members_of(out.read_bytes()))["manifest.json"])
        self.assertEqual(("", [], [{"id": "job-log", "state": "absent"}]), (plain["retention"], plain["stated"], plain["optional"]))
        self.assertNotIn(".github/workflows/check.yml", [item["path"] for item in plain["policy"]["files"]])

    def test_the_checks_have_one_outcome_each(self) -> None:
        """/* REQ-ACTION-183: the manifest lists every required test and every test that uses what changed with its exact identifier and one outcome of PASS, FAIL, SKIP, NOT RUN, UNKNOWN, and each recorded case with one outcome: passed is PASS, failed FAIL, skipped SKIP, a test or case without a record NOT RUN, a test with no report given UNKNOWN; a test with a passed case and a skipped one is SKIP, with a case without a record NOT RUN */"""
        self.assertEqual(CHECKS, pkg.checks(json.loads(RECORD)))
        self.assertEqual([], pkg.checks(None))
        self.assertEqual({"PASS", "FAIL", "SKIP", "NOT RUN", "UNKNOWN"}, {check["outcome"] for check in CHECKS})
        self.assertEqual(PACKAGE_SCHEMA["properties"]["checks"]["items"]["properties"]["outcome"]["enum"], ["PASS", "FAIL", "SKIP", "NOT RUN", "UNKNOWN"])

    def test_the_statements_stay_apart_and_the_attestation_has_its_place(self) -> None:
        """/* REQ-ACTION-185: a package keeps three statements apart: the record states that a check ran, a review states that a person accepted the evidence, and nothing states that a control conforms; the manifest and `verify` say each on its own, and no output of the command holds the word `conforms` in another sentence; the manifest names the member a detached attestation takes (attestation/manifest.json.attestation, over manifest.json) and holds neither a digest of itself nor a signature; `pack` writes no attestation, and one put at that place is accepted by `verify`, reported as present and not checked */"""
        said = json.loads(dict(members_of(COMPLETE.read_bytes()))["manifest.json"])
        self.assertEqual(STATEMENTS, said["statements"])
        self.assertEqual({"member": "attestation/manifest.json.attestation", "over": "manifest.json"}, said["attestation"])
        self.assertNotIn(sha(dict(members_of(COMPLETE.read_bytes()))["manifest.json"]), json.dumps(said))
        self.assertEqual([], [key for key in said if "signature" in key or "sha256" in key])
        self.assertNotIn("attestation/manifest.json.attestation", dict(members_of(COMPLETE.read_bytes())))
        status, out, _err = run_cli("verify", COMPLETE)
        self.assertEqual(0, status)
        self.assertEqual([f"statement: {name}: {text}" for name, text in STATEMENTS.items()], [line for line in out.splitlines() if line.startswith("statement: ")])
        self.assertEqual(1, out.count("conforms"))
        self.assertIn("attestation: none\n", out)
        attested = rewritten(COMPLETE, WORK / "attested.zip", lambda members: members + [("attestation/manifest.json.attestation", b"signed elsewhere")])
        status, out, _err = run_cli("verify", attested)
        self.assertEqual(0, status)
        self.assertIn("attestation: present-not-checked\n", out)
        elsewhere = rewritten(COMPLETE, WORK / "attested-elsewhere.zip", lambda members: members + [("attestation/other.sig", b"x")])
        self.assertEqual([("unexpected-member", "attestation/other.sig")], found(elsewhere))


class SourcesTest(unittest.TestCase):
    def test_originals_are_packed_byte_for_byte(self) -> None:
        """/* REQ-ACTION-173: the test reports the record names, the sidecar beside one and the job log are members with the bytes they have on disk, line endings and bytes that are no text included; a report in a folder with a space gets a member name with %20; a report path of the record that leads out of the folder the reports are read from is not read: the member is absent and the package incomplete */"""
        members = dict(members_of(COMPLETE.read_bytes()))
        self.assertEqual((JUNIT, SIDECAR, LOG), (members["sources/test-reports/test-results/junit.xml"],
                                                members["sources/test-reports/test-results/junit.xml.run.json"], members["sources/job-log/job.log"]))
        self.assertIn(b"\r\n", members["sources/job-log/job.log"])
        folder = WORK / "sources"
        reports = folder / "reports"
        (reports / "odd dir").mkdir(parents=True)
        (reports / "odd dir" / "re port.xml").write_bytes(JUNIT)
        (folder / "outside.xml").write_bytes(JUNIT)
        run_folder(folder / "odd", record_of(COMMIT, reports=(("odd dir/re port.xml", JUNIT),)))
        self.assertEqual(0, run_cli("pack", "--run-dir", folder / "odd", "--out", folder / "odd.zip", "--repo", REPO, "--reports-root", reports)[0])
        self.assertEqual(JUNIT, dict(members_of((folder / "odd.zip").read_bytes()))["sources/test-reports/odd%20dir/re%20port.xml"])
        run_folder(folder / "out", record_of(COMMIT, reports=(("../outside.xml", JUNIT),)))
        status, text, _err = run_cli("pack", "--run-dir", folder / "out", "--out", folder / "out.zip", "--repo", REPO, "--reports-root", reports)
        self.assertEqual(1, status)
        self.assertIn("absent: test-report:../outside.xml (absent: not found by `pack`, or larger than its limit)\n", text)
        self.assertEqual([], [name for name, _data in members_of((folder / "out.zip").read_bytes()) if name.startswith("sources/")])

    def test_policy_files_come_from_the_commit_checked(self) -> None:
        """/* REQ-ACTION-174: `pack` takes each policy file from the commit the record says was checked, never from the working folder: a baseline edited after the commit is packed as the commit has it, with the commit named; a rule file that is only in the working folder is `not-in-commit` and no member; a file named with --policy is asked for too; when the commit cannot be read (no repository, or a commit the repository does not have) every policy file is `not-read`, the required item `policy-files` is absent, the package is incomplete and the status is 1 */"""
        self.assertNotEqual(BASELINE, (REPO / ".wingbox" / "baseline.sexp").read_bytes())
        self.assertTrue((REPO / ".wingbox" / "config.yaml").is_file())
        members = dict(members_of(PLAIN.read_bytes()))
        self.assertEqual(BASELINE, members["policy/.wingbox/baseline.sexp"])
        self.assertEqual(subprocess.run(["git", "-C", str(REPO), "show", f"{COMMIT}:.wingbox/baseline.sexp"], check=True, capture_output=True).stdout,
                         members["policy/.wingbox/baseline.sexp"])
        self.assertNotIn("policy/.wingbox/config.yaml", members)
        said = json.loads(members["manifest.json"])
        self.assertEqual({"commit": COMMIT, "read": True, "files": [
            {"path": ".wingbox/baseline.sexp", "kind": "baseline", "state": "present", "member": "policy:.wingbox/baseline.sexp"},
            {"path": ".wingbox/config.yaml", "kind": "rules", "state": "not-in-commit", "member": ""},
            {"path": ".wingbox/learned.sexp", "kind": "rules", "state": "not-in-commit", "member": ""},
            {"path": ".wingbox/required-tests.yaml", "kind": "required-tests", "state": "not-in-commit", "member": ""}]}, said["policy"])
        folder = WORK / "policy"
        folder.mkdir()
        self.assertEqual(0, run_cli("pack", "--run-dir", RUN_DIR, "--out", folder / "named.zip", "--repo", REPO, "--policy", "./app.py", "--policy",
                                    "controls/map.yaml")[0])
        named = json.loads(dict(members_of((folder / "named.zip").read_bytes()))["manifest.json"])
        self.assertEqual([{"path": "app.py", "kind": "named", "state": "present", "member": "policy:app.py"},
                          {"path": "controls/map.yaml", "kind": "named", "state": "not-in-commit", "member": ""}],
                         [item for item in named["policy"]["files"] if item["kind"] == "named"])
        self.assertEqual(APP, dict(members_of((folder / "named.zip").read_bytes()))["policy/app.py"])
        (folder / "no-repository").mkdir()
        run_folder(folder / "other-run", record_of("f" * 40))
        for name, run_dir, repo in (("no-repository.zip", RUN_DIR, folder / "no-repository"), ("other-commit.zip", folder / "other-run", REPO)):
            status, text, _err = run_cli("pack", "--run-dir", run_dir, "--out", folder / name, "--repo", repo, "--reports-root", REPO)
            self.assertEqual(1, status, name)
            self.assertTrue(text.endswith("absent: policy-files (absent: the commit checked could not be read)\npack: incomplete (1 required member absent)\n"), text)
            unread = json.loads(dict(members_of((folder / name).read_bytes()))["manifest.json"])
            self.assertEqual(("incomplete", ["policy-files"], False), (unread["state"], unread["absent"], unread["policy"]["read"]))
            self.assertEqual({"not-read"}, {item["state"] for item in unread["policy"]["files"]})
            self.assertEqual([], [member for member in unread["members"] if member["role"] == "policy"])
        self.assertEqual(wingbox_policy.FIXED, pkg.POLICY_FILES)


class IncompleteTest(unittest.TestCase):
    def test_an_absent_required_member_makes_the_package_incomplete(self) -> None:
        """/* REQ-ACTION-175: a run folder without the plan the record names gives a package that says `incomplete`, lists the plan under `absent` and as `absent` among the required members, and `pack` ends with status 1 and the line `pack: incomplete (1 required member absent)`; a report whose bytes are not the ones the record names is `differs`; a run folder without a record has the record absent; `verify` of such a package ends with status 1, says the integrity is verified, the state incomplete and names what is absent */"""
        folder = WORK / "incomplete"
        folder.mkdir()
        status, text, _err = run_cli("pack", "--run-dir", run_folder(folder / "no-plan", RECORD, ("wingbox-plan.sexp",)), "--out", folder / "no-plan.zip",
                                     "--repo", REPO)
        self.assertEqual(1, status)
        self.assertEqual(["state: incomplete", "members: 8", "absent: document:wingbox-plan.sexp (absent: not found by `pack`, or larger than its limit)",
                          "pack: incomplete (1 required member absent)"], text.splitlines()[3:])
        said = json.loads(dict(members_of((folder / "no-plan.zip").read_bytes()))["manifest.json"])
        self.assertEqual(("incomplete", ["document:wingbox-plan.sexp"]), (said["state"], said["absent"]))
        self.assertIn({"id": "document:wingbox-plan.sexp", "path": "record/wingbox-plan.sexp", "state": "absent",
                       "reason": "not found by `pack`, or larger than its limit"}, said["required"])
        self.assertEqual([], schema_problems(said, PACKAGE_SCHEMA))
        status, out, _err = run_cli("verify", folder / "no-plan.zip")
        self.assertEqual(1, status)
        for line in ("integrity: verified", "state: incomplete",
                     "problem: incomplete document:wingbox-plan.sexp: absent: not found by `pack`, or larger than its limit", "verify: failed (1 problem)"):
            self.assertIn(line + "\n", out)
        other = folder / "other-report"
        (other / "test-results").mkdir(parents=True)
        (other / "test-results" / "junit.xml").write_bytes(JUNIT + b"<!-- edited -->")
        status, text, _err = run_cli("pack", "--run-dir", RUN_DIR, "--out", folder / "differs.zip", "--repo", REPO, "--reports-root", other)
        self.assertEqual(1, status)
        self.assertIn("absent: test-report:test-results/junit.xml (differs: its SHA-256 is not the one the record names)\n", text)
        self.assertEqual([("incomplete", "test-report:test-results/junit.xml")], found(folder / "differs.zip"))
        (folder / "empty").mkdir()
        status, text, _err = run_cli("pack", "--run-dir", folder / "empty", "--out", folder / "empty.zip", "--repo", REPO)
        self.assertEqual(1, status)
        self.assertIn("absent: record (absent: no wingbox-evidence.json in the run folder)\n", text)
        self.assertEqual({("incomplete", "record"), ("incomplete", "policy-files")}, set(found(folder / "empty.zip")))

    def test_a_record_of_a_check_that_did_not_run_is_packed(self) -> None:
        """/* REQ-ACTION-189: the record of a check that did not run names no document and no test report: its package is complete when the head it names is a commit of the repository, with no check and the policy files of that head, also when the parts its schema leaves optional are absent; the smallest record a job writes by hand, which is no record of version 2, gives an incomplete package whose record is `unreadable` */"""
        folder = WORK / "did-not-run"
        record = we.build_evidence(pr.not_run_report("", COMMIT, {"WINGBOX_VERSION": "0.14.0"}), RUN, {})
        self.assertEqual(([], [], ""), (record["documents"], record["tests"]["reports"], record["subject"]["checked"]))
        run_folder(folder / "run", record, ("wingbox-report.md", "wingbox-plan.sexp"))
        self.assertEqual(0, run_cli("pack", "--run-dir", folder / "run", "--out", folder / "run.zip", "--repo", REPO)[0])
        said = json.loads(dict(members_of((folder / "run.zip").read_bytes()))["manifest.json"])
        self.assertEqual(("complete", [], COMMIT, ["policy/.wingbox/baseline.sexp", "record/wingbox-evidence.json"]),
                         (said["state"], said["checks"], said["policy"]["commit"], [member["path"] for member in said["members"]][:2]))
        self.assertEqual([], found(folder / "run.zip", "--repo", REPO))
        # The parts of the record its schema leaves optional may be absent.
        for key in ("required", "manifest", "provenance", "step-outcome"):
            del record["tests"][key]
        del record["run"]["attempt"], record["run"]["job"]
        self.assertEqual([], schema_problems(record, RECORD_SCHEMA))
        run_folder(folder / "less", record, ("wingbox-report.md", "wingbox-plan.sexp"))
        self.assertEqual(0, run_cli("pack", "--run-dir", folder / "less", "--out", folder / "less.zip", "--repo", REPO)[0])
        self.assertEqual([], found(folder / "less.zip"))
        run_folder(folder / "small", b'{"result": {"boundaries": "UNKNOWN", "execution": "error", "reasons": ["check-did-not-run"]}}\n')
        status, text, _err = run_cli("pack", "--run-dir", folder / "small", "--out", folder / "small.zip", "--repo", REPO)
        self.assertEqual(1, status)
        self.assertIn("absent: record (unreadable: not a wingbox-evidence/2 record)\n", text)
        small = json.loads(dict(members_of((folder / "small.zip").read_bytes()))["manifest.json"])
        self.assertEqual(("", "incomplete", ["record", "policy-files"]), (small["created"], small["state"], small["absent"]))


class VerifyTest(unittest.TestCase):
    def test_a_complete_package_verifies_without_the_repository(self) -> None:
        """/* REQ-ACTION-176: `verify` of a complete package, run in a folder that is no repository, ends with status 0 and prints the bundle id, the SHA-256 of the manifest, `integrity: verified`, `state: complete`, the number of members, that the repository was not asked, the reviews, the three statements and the last line `verify: ok`; --json prints the same as one value */"""
        elsewhere = WORK / "elsewhere-cwd"
        elsewhere.mkdir()
        before = os.getcwd()
        os.chdir(elsewhere)
        try:
            status, out, err = run_cli("verify", COMPLETE)
            also, value, _err = run_cli("verify", COMPLETE, "--json")
        finally:
            os.chdir(before)
        manifest = dict(members_of(COMPLETE.read_bytes()))["manifest.json"]
        self.assertEqual((0, ""), (status, err))
        self.assertEqual([f"package: {COMPLETE}", f"bundle: {expected_manifest()['bundle']}", f"manifest-sha256: {sha(manifest)}", "integrity: verified",
                          "state: complete", "members: 11", "repository: not-asked", "attestation: none", "reviews: 0",
                          *(f"statement: {name}: {text}" for name, text in STATEMENTS.items()), "verify: ok"], out.splitlines())
        self.assertEqual(0, also)
        self.assertEqual({"package": str(COMPLETE), "bundle": expected_manifest()["bundle"], "manifest-sha256": sha(manifest), "integrity": "verified",
                          "state": "complete", "members": 11, "repository": "not-asked", "attestation": "none", "reviews": [], "problems": [],
                          "ok": True}, json.loads(value))

    def test_a_changed_a_missing_a_repeated_and_an_unexpected_member_are_each_named(self) -> None:
        """/* REQ-ACTION-177: `verify` ends with status 1 and names the member for: a member whose bytes changed (`altered-member`, with the length and SHA-256 found and the ones the manifest names), a member the manifest names that the archive does not hold (`missing-member`), a member the archive holds twice (`duplicate-member`), and a member the manifest does not name (`unexpected-member`); an archive without its manifest is refused */"""
        folder = WORK / "negative"
        folder.mkdir()
        edited = REPORT + b"edited\n"
        changed = rewritten(COMPLETE, folder / "changed.zip",
                            lambda members: [(name, edited if name == "record/wingbox-report.md" else data) for name, data in members])
        status, out, _err = run_cli("verify", changed)
        self.assertEqual(1, status)
        self.assertIn(f"problem: altered-member record/wingbox-report.md: {len(edited)} bytes with SHA-256 {sha(edited)}; the manifest names "
                      f"{len(REPORT)} bytes with {sha(REPORT)}\n", out)
        self.assertIn("integrity: refused\nstate: unknown\n", out)
        self.assertEqual([("altered-member", "record/wingbox-report.md")], found(changed))
        missing = rewritten(COMPLETE, folder / "missing.zip", lambda members: [member for member in members if member[0] != "record/wingbox-plan.sexp"])
        self.assertEqual([("missing-member", "record/wingbox-plan.sexp")], found(missing))
        twice = rewritten(COMPLETE, folder / "twice.zip", lambda members: members + [("record/wingbox-plan.sexp", PLAN)])
        self.assertEqual([("duplicate-member", "record/wingbox-plan.sexp")], found(twice))
        extra = rewritten(COMPLETE, folder / "extra.zip", lambda members: members + [("extra/notes.txt", b"note")])
        self.assertEqual([("unexpected-member", "extra/notes.txt")], found(extra))
        bare = rewritten(COMPLETE, folder / "bare.zip", lambda members: members[1:])
        self.assertEqual([("missing-member", "manifest.json")], found(bare))

    def test_a_manifest_that_does_not_match_is_refused(self) -> None:
        """/* REQ-ACTION-178: `verify` computes the manifest again from the members and refuses one that differs, naming the keys: a manifest edited to call an incomplete package complete, a check outcome changed from FAIL to PASS, a member entry with another digest (the member is then `altered-member`), a manifest that does not meet its schema (`manifest-invalid`), and one written in another form; with --manifest-sha256 a manifest whose bytes are not the ones noted is refused (`manifest-digest`), which also catches an edit of what was only stated, such as the retention */"""
        folder = WORK / "mismatch"
        folder.mkdir()
        self.assertEqual(1, run_cli("pack", "--run-dir", run_folder(folder / "no-plan", RECORD, ("wingbox-plan.sexp",)), "--out", folder / "no-plan.zip",
                                    "--repo", REPO)[0])

        def called_complete(said: dict) -> None:
            said["state"], said["absent"] = "complete", []
            for need in said["required"]:
                need["state"], need["reason"] = "present", ""
        self.assertEqual([("manifest-mismatch", "manifest.json")],
                         found(rewritten(folder / "no-plan.zip", folder / "called-complete.zip", lambda members: with_manifest(members, called_complete))))
        status, out, _err = run_cli("verify", folder / "called-complete.zip")
        self.assertEqual(1, status)
        self.assertIn("problem: manifest-mismatch manifest.json: its members give another manifest; what differs: state, required, absent\n", out)

        def passed(said: dict) -> None:
            self.assertEqual("FAIL", said["checks"][4]["outcome"])
            said["checks"][4]["outcome"] = "PASS"
        status, out, _err = run_cli("verify", rewritten(COMPLETE, folder / "passed.zip", lambda members: with_manifest(members, passed)))
        self.assertEqual(1, status)
        self.assertIn("problem: manifest-mismatch manifest.json: its members give another manifest; what differs: checks\n", out)

        def other_digest(said: dict) -> None:
            said["members"][3]["sha256"] = "0" * 64
        self.assertEqual([("altered-member", "record/wingbox-plan.sexp")],
                         found(rewritten(COMPLETE, folder / "digest.zip", lambda members: with_manifest(members, other_digest))))
        for name, edit in (("no-members.zip", lambda said: said.pop("members")), ("more.zip", lambda said: said.update(extra=1)),
                           ("word.zip", lambda said: said.update(state="fine"))):
            self.assertEqual([("manifest-invalid", "manifest.json")],
                             found(rewritten(COMPLETE, folder / name, lambda members: with_manifest(members, edit))), name)
        compact = rewritten(COMPLETE, folder / "compact.zip", lambda members: [
            (name, json.dumps(json.loads(data)).encode() if name == "manifest.json" else data) for name, data in members])
        self.assertEqual([("manifest-mismatch", "manifest.json")], found(compact))
        noted = sha(dict(members_of(COMPLETE.read_bytes()))["manifest.json"])
        self.assertEqual([], found(COMPLETE, "--manifest-sha256", noted))
        self.assertEqual([], found(COMPLETE, "--manifest-sha256", noted.upper()))
        self.assertEqual([("manifest-digest", "manifest.json")], found(COMPLETE, "--manifest-sha256", "0" * 64))
        longer = rewritten(COMPLETE, folder / "retention.zip", lambda members: with_manifest(members, lambda said: said.update(retention="900 days")))
        self.assertEqual([], found(longer))
        self.assertEqual([("manifest-digest", "manifest.json")], found(longer, "--manifest-sha256", noted))

    def test_with_a_repository_the_digests_are_computed_again_from_git(self) -> None:
        """/* REQ-ACTION-180: `verify --repo DIR` reads each policy file and each changed file of the record from the commit named and compares: a package as the repository has it ends with status 0 and `repository: matches`; a policy member that verifies against its manifest but is not what the commit holds is `policy-differs-from-commit` with both digests; a changed file whose digest in the record is not the one of the commit is `file-differs-from-commit`; a repository without the commit is `commit-not-found`; each ends with status 1 and `repository: does-not-match` */"""
        status, out, _err = run_cli("verify", COMPLETE, "--repo", REPO)
        self.assertEqual(0, status)
        self.assertIn("repository: matches\n", out)
        folder = WORK / "repository"
        folder.mkdir()
        other = b"(baseline . src/x src/y)\n"

        def swapped(members: list) -> list:
            """Another baseline in the package, with a manifest that names its length, its digest and the bundle id they give."""
            changed = [(name, other if name == "policy/.wingbox/baseline.sexp" else data) for name, data in members]

            def edit(said: dict) -> None:
                item = next(member for member in said["members"] if member["path"] == "policy/.wingbox/baseline.sexp")
                item["length"], item["sha256"] = len(other), sha(other)
                said["bundle"] = sha("".join(f"{member['sha256']}  {member['path']}\n" for member in said["members"]).encode("ascii"))
            return with_manifest(changed, edit)
        swapped_package = rewritten(COMPLETE, folder / "swapped.zip", swapped)
        self.assertEqual([], found(swapped_package))
        status, out, _err = run_cli("verify", swapped_package, "--repo", REPO)
        self.assertEqual(1, status)
        self.assertIn(f"problem: policy-differs-from-commit .wingbox/baseline.sexp: SHA-256 {sha(other)} in the package, {sha(BASELINE)} in commit {COMMIT}\n", out)
        self.assertIn("integrity: verified\nstate: complete\nmembers: 11\nrepository: does-not-match\n", out)
        self.assertTrue(out.endswith("verify: failed (1 problem)\n"))
        run_folder(folder / "other-file", record_of(COMMIT, files=(("app.py", b"another app\n"), ("gone.py", APP))))
        self.assertEqual(0, run_cli("pack", "--run-dir", folder / "other-file", "--out", folder / "other-file.zip", "--repo", REPO)[0])
        self.assertEqual([], found(folder / "other-file.zip"))
        status, out, _err = run_cli("verify", folder / "other-file.zip", "--repo", REPO)
        self.assertEqual(1, status)
        self.assertIn(f"problem: file-differs-from-commit app.py: SHA-256 {sha(b'another app' + bytes([10]))} in the record, {sha(APP)} in commit {COMMIT}\n", out)
        self.assertIn("problem: file-differs-from-commit gone.py: the commit has no such file\n", out)
        empty = folder / "empty-repository"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        self.assertEqual([("commit-not-found", COMMIT)], found(COMPLETE, "--repo", empty))
        self.assertEqual([("commit-not-found", COMMIT)], found(COMPLETE, "--repo", folder / "no-such-folder"))

    def test_a_changed_byte_is_always_detected_and_a_missing_required_member_is_never_a_pass(self) -> None:
        """/* REQ-ACTION-182: with the SHA-256 of the manifest noted, a change of any one byte of a package makes `verify` report a problem (tried on every byte of the first and the last 300 and on every eleventh between); without it, the same holds for every byte outside the manifest; an archive without any one of its members is never a pass; and the manifest of a package built without any one required input says `incomplete` */"""
        schemas = cli.schemas()
        members = {"record/wingbox-evidence.json": RECORD, "record/wingbox-report.md": REPORT, "record/wingbox-plan.sexp": PLAN,
                   "sources/test-reports/test-results/junit.xml": JUNIT, "policy/.wingbox/baseline.sexp": BASELINE,
                   **{f"schema/{name}": b"{}\n" for name in SCHEMA_NAMES}}
        given = pkg.Given(read=True, policy=((".wingbox/baseline.sexp", "baseline"),))
        data, said = pkg.pack(members, given, RECORD_SCHEMA)
        self.assertEqual(("complete", ()), (said["state"], pkg.verify(data, schemas).problems))
        manifest = dict(members_of(data))["manifest.json"]
        noted = sha(manifest)
        inside = range(data.index(manifest), data.index(manifest) + len(manifest))
        places = sorted({*range(300), *range(len(data) - 300, len(data)), *range(300, len(data) - 300, 11)})
        self.assertGreater(len(places), 1000)
        for place in places:
            changed = data[:place] + bytes([data[place] ^ 0x20]) + data[place + 1:]
            self.assertNotEqual((), pkg.verify(changed, schemas, noted).problems, place)
            if place not in inside:
                self.assertNotEqual((), pkg.verify(changed, schemas).problems, place)
        listed = members_of(data)
        for name, _data in listed:
            self.assertNotEqual((), pkg.verify(zip_bytes([member for member in listed if member[0] != name]), schemas).problems, name)
        for name in members:
            if name != "policy/.wingbox/baseline.sexp":
                self.assertEqual("incomplete", pkg.manifest({path: body for path, body in members.items() if path != name}, given, RECORD_SCHEMA)["state"], name)
        self.assertEqual("incomplete", pkg.manifest(members, pkg.Given(read=False, policy=given.policy), RECORD_SCHEMA)["state"])


class ReviewTest(unittest.TestCase):
    def test_a_review_is_a_new_member_and_no_earlier_byte_changes(self) -> None:
        """/* REQ-ACTION-181: `review` adds reviews/0001.json after the last member: the record of a decision with its number, the SHA-256 of the manifest reviewed, the bundle id, the reviewer, `accepted` or `rejected`, the grounds, the time and the SHA-256 of the review before it; every byte before the directory of the old archive is the same in the new one, and every earlier member keeps its bytes; a second review is 0002 and names the first; with --time the same review gives the same bytes; `verify` lists the reviews and still ends with status 0; a review that was edited, one whose predecessor was removed and one about another manifest are each `review-invalid`; a package that does not verify is not reviewed: status 1, the file unchanged; an incomplete package that is what its manifest says can be reviewed, at the time of the clock when --time is not given */"""
        folder = WORK / "review"
        folder.mkdir()
        target = folder / "package.zip"
        shutil.copyfile(COMPLETE, target)
        before = target.read_bytes()
        manifest = dict(members_of(before))["manifest.json"]
        status, out, err = run_cli("review", target, "--reviewer", "R. Viewer", "--decision", "accepted", "--grounds", "read the record and the reports",
                                   "--time", "2026-02-03T04:05:06Z")
        self.assertEqual((0, ""), (status, err))
        self.assertEqual([f"package: {target}", f"manifest-sha256: {sha(manifest)}", "state: complete", "member: reviews/0001.json",
                          "decision: accepted by R. Viewer at 2026-02-03T04:05:06Z", "review: recorded"], out.splitlines())
        after = target.read_bytes()
        directory = struct.unpack("<I", before[-6:-2])[0]      # where the directory of the old archive starts
        self.assertEqual(before[:directory], after[:directory])
        self.assertEqual(members_of(before), members_of(after)[:-1])
        first = {"schema": "wingbox-review/1", "sequence": 1, "manifest-sha256": sha(manifest), "bundle": expected_manifest()["bundle"],
                 "reviewer": "R. Viewer", "decision": "accepted", "grounds": "read the record and the reports", "time": "2026-02-03T04:05:06Z",
                 "previous": ""}
        self.assertEqual(("reviews/0001.json", as_json(first)), members_of(after)[-1])
        self.assertEqual([], schema_problems(first, REVIEW_SCHEMA))
        self.assertEqual(0, run_cli("review", target, "--reviewer", "S. Econd", "--decision", "rejected", "--grounds", "the log is cut short", "--time",
                                    "2026-02-04T00:00:00Z")[0])
        second = {**first, "sequence": 2, "reviewer": "S. Econd", "decision": "rejected", "grounds": "the log is cut short",
                  "time": "2026-02-04T00:00:00Z", "previous": sha(as_json(first))}
        twice = target.read_bytes()
        self.assertEqual(members_of(after) + [("reviews/0002.json", as_json(second))], members_of(twice))
        self.assertEqual(after[:struct.unpack("<I", after[-6:-2])[0]], twice[:struct.unpack("<I", after[-6:-2])[0]])
        again = folder / "again.zip"
        shutil.copyfile(COMPLETE, again)
        for reviewer, decision, grounds, time in (("R. Viewer", "accepted", "read the record and the reports", "2026-02-03T04:05:06Z"),
                                                  ("S. Econd", "rejected", "the log is cut short", "2026-02-04T00:00:00Z")):
            run_cli("review", again, "--reviewer", reviewer, "--decision", decision, "--grounds", grounds, "--time", time)
        self.assertEqual(twice, again.read_bytes())
        status, out, _err = run_cli("verify", target, "--repo", REPO)
        self.assertEqual(0, status)
        self.assertIn("reviews: 2\nreview: 1 accepted by R. Viewer at 2026-02-03T04:05:06Z: read the record and the reports\n"
                      "review: 2 rejected by S. Econd at 2026-02-04T00:00:00Z: the log is cut short\n", out)
        edited = rewritten(target, folder / "edited.zip", lambda members: [
            (name, as_json({**first, "decision": "rejected"}) if name == "reviews/0001.json" else data) for name, data in members])
        self.assertEqual([("review-invalid", "reviews/0002.json")], found(edited))
        removed = rewritten(target, folder / "removed.zip", lambda members: [member for member in members if member[0] != "reviews/0001.json"])
        self.assertEqual([("review-invalid", "reviews/0002.json")], found(removed))
        moved = rewritten(target, folder / "moved.zip", lambda members: with_manifest(members, lambda said: said.update(retention="1 day")))
        self.assertEqual([("review-invalid", "reviews/0001.json"), ("review-invalid", "reviews/0002.json")], found(moved))
        broken = rewritten(COMPLETE, folder / "broken.zip", lambda members: [(name, b"x" if name == "record/wingbox-plan.sexp" else data) for name, data in members])
        kept = broken.read_bytes()
        status, out, _err = run_cli("review", broken, "--reviewer", "R. Viewer", "--decision", "accepted", "--grounds", "looks fine")
        self.assertEqual(1, status)
        self.assertTrue(out.endswith("review: not recorded (the package does not verify)\n"), out)
        self.assertIn("problem: altered-member record/wingbox-plan.sexp: ", out)
        self.assertEqual(kept, broken.read_bytes())
        self.assertEqual(sorted(["package.zip", "again.zip", "edited.zip", "removed.zip", "moved.zip", "broken.zip"]), sorted(path.name for path in folder.iterdir()))
        # An incomplete package that is what its manifest says can be reviewed: the decision is the reviewer's.
        self.assertEqual(1, run_cli("pack", "--run-dir", run_folder(folder / "no-plan", RECORD, ("wingbox-plan.sexp",)), "--out", folder / "no-plan.zip",
                                    "--repo", REPO)[0])
        status, out, _err = run_cli("review", folder / "no-plan.zip", "--reviewer", "R. Viewer", "--decision", "rejected", "--grounds", "the plan is absent")
        self.assertEqual(0, status)
        self.assertIn("state: incomplete\nmember: reviews/0001.json\n", out)
        self.assertRegex(out, r"decision: rejected by R\. Viewer at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\nreview: recorded\n$")
        self.assertEqual([("incomplete", "document:wingbox-plan.sexp")], found(folder / "no-plan.zip"))


class CommandTest(unittest.TestCase):
    def test_the_exit_status_tells_usage_from_a_defect(self) -> None:
        """/* REQ-ACTION-184: the command ends with 0 when it did its work and the result is good, with 1 when it did its work and found a defect, and with 2 for wrong usage: no subcommand, an unknown one, a missing or unknown option, a decision that is neither accepted nor rejected, a run folder or a package that is not there, an archive that would be written over, a time or a digest in another form; wrong usage prints nothing on the standard output and says why on the error stream; started as a script with -P it behaves the same */"""
        folder = WORK / "usage"
        folder.mkdir()
        wrong = (
            (), ("unpack", COMPLETE), ("pack", "--run-dir", RUN_DIR), ("pack", "--run-dir", RUN_DIR, "--out", folder / "x.zip", "--unknown"),
            ("pack", "--run-dir", folder / "no-such-folder", "--out", folder / "x.zip"), ("pack", "--run-dir", RUN_DIR, "--out", COMPLETE, "--repo", REPO),
            ("pack", "--run-dir", RUN_DIR, "--out", folder / "x.zip", "--job-log", folder / "no.log"),
            ("pack", "--run-dir", RUN_DIR, "--out", folder / "x.zip", "--stated", "no-equals-sign"),
            ("pack", "--run-dir", RUN_DIR, "--out", folder / "x.zip", "--repo", REPO, "--max-bytes", "1000"),
            ("verify",), ("verify", folder / "no-such.zip"), ("verify", COMPLETE, "--manifest-sha256", "abc"), ("verify", COMPLETE, "--max-bytes", "0"),
            ("review", COMPLETE, "--reviewer", "R", "--decision", "maybe", "--grounds", "g"), ("review", COMPLETE, "--reviewer", "R", "--decision", "accepted"),
            ("review", COMPLETE, "--reviewer", " ", "--decision", "accepted", "--grounds", "g"),
            ("review", COMPLETE, "--reviewer", "R", "--decision", "accepted", "--grounds", "two\nlines"),
            ("review", COMPLETE, "--reviewer", "R", "--decision", "accepted", "--grounds", "g", "--time", "yesterday"),
            ("review", folder / "no-such.zip", "--reviewer", "R", "--decision", "accepted", "--grounds", "g"),
        )
        kept = COMPLETE.read_bytes()
        for args in wrong:
            status, out, err = run_cli(*args)
            self.assertEqual((2, ""), (status, out), args)
            self.assertNotEqual("", err, args)
        self.assertEqual(kept, COMPLETE.read_bytes())
        self.assertEqual([], list(folder.iterdir()))
        status, _out, err = run_cli("pack", "--run-dir", RUN_DIR, "--out", COMPLETE, "--repo", REPO)
        self.assertEqual(f"wingbox-evidence: {COMPLETE} exists: a package is not written over\n", err)
        script = [sys.executable, "-P", str(ROOT / "wingbox_evidence_cli.py")]
        good = subprocess.run([*script, "verify", str(COMPLETE)], capture_output=True, text=True, cwd=folder)
        self.assertEqual((0, "verify: ok"), (good.returncode, good.stdout.splitlines()[-1]))
        defect = subprocess.run([*script, "verify", str(COMPLETE), "--max-bytes", "10"], capture_output=True, text=True, cwd=folder)
        self.assertEqual((1, "verify: failed (1 problem)"), (defect.returncode, defect.stdout.splitlines()[-1]))
        usage = subprocess.run([*script, "verify"], capture_output=True, text=True, cwd=folder)
        self.assertEqual((2, ""), (usage.returncode, usage.stdout))
        self.assertIn("usage: wingbox-evidence verify", usage.stderr)

    def test_nothing_is_run_and_nothing_leaves_the_machine(self) -> None:
        """/* REQ-ACTION-187: wingbox_package.py imports only hashlib, json, re, struct, zlib, dataclasses and typing, and no module of this repository; wingbox_evidence_cli.py adds argparse, datetime, os, subprocess, sys, pathlib, wingbox_package and wingbox_ledger: no network, no archive library that unpacks, no model; the only program the command starts is git, with the words `cat-file` (and `rev-list` for the commits of a branch, REQ-ACTION-205); neither file calls exec, eval, compile, __import__ or os.system */"""
        def imported(path: Path) -> set:
            names = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names.add((node.module or "").split(".")[0])
            return names
        pure = {"__future__", "hashlib", "json", "re", "struct", "zlib", "dataclasses", "typing"}
        self.assertEqual(pure, imported(ROOT / "wingbox_package.py"))
        self.assertEqual(pure - {"hashlib", "struct", "zlib", "dataclasses"} | {"argparse", "datetime", "os", "subprocess", "sys", "pathlib", "wingbox_package",
                                                                                 "wingbox_ledger"}, imported(ROOT / "wingbox_evidence_cli.py"))
        for name in ("wingbox_package.py", "wingbox_evidence_cli.py"):
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
            called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            self.assertEqual(set(), called & {"exec", "eval", "compile", "__import__"}, name)
            attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertEqual(set(), attributes & {"system", "popen", "Popen", "extractall", "extract", "urlopen", "check_output", "call"}, name)
        tree = ast.parse((ROOT / "wingbox_evidence_cli.py").read_text(encoding="utf-8"))
        started = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
        self.assertEqual(1, len(started))
        self.assertEqual("git", started[0].args[0].elts[0].value)
        text = (ROOT / "wingbox_evidence_cli.py").read_text(encoding="utf-8")
        self.assertEqual(2, text.count('_git(repo, "cat-file", '))
        self.assertEqual(3, text.count("_git(") - text.count('_git(repo, "rev-list", '))


class ActionTest(unittest.TestCase):
    def test_the_action_and_gitlab_pack_when_asked(self) -> None:
        """/* REQ-ACTION-186: action.yml has the input `package`, off by default, handed to both steps that write a record as WINGBOX_PACKAGE, and the outputs `package` and `package-state`; with WINGBOX_PACKAGE true the scripts write wingbox-package.zip beside the record, name the workflow file of the run as a policy file, and end with status 1 when the package is incomplete; without it no package is written and nothing is said; in GitLab the same variable, the state as WINGBOX_PACKAGE_STATE in wingbox.env, and the shell-runner template hands the variable to the container */"""
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  package:\n    description: [^\n]+\n    default: "false"\n')
        self.assertEqual(2, action.count("WINGBOX_PACKAGE: ${{ inputs.package }}"))
        self.assertRegex(action, r"\n  package:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.package \|\| steps\.did-not-run\.outputs\.package \}\}\n")
        self.assertRegex(action, r"\n  package-state:\n    description: [^\n]+\n    value: \$\{\{ steps\.check\.outputs\.package-state \|\| steps\.did-not-run\.outputs\.package-state \}\}\n")
        self.assertRegex((ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8"), r"--env WINGBOX_PACKAGE\s")
        folder = WORK / "action"
        out = run_folder(folder / "out", RECORD)
        said: list = []
        self.assertEqual(("", "", 0), pr.write_package({}, str(out), str(REPO), {}, said.append))
        self.assertEqual(("", "", 0), pr.write_package({"WINGBOX_PACKAGE": "false"}, str(out), str(REPO), {}, said.append))
        self.assertEqual(([], 3), (said, len(list(out.iterdir()))))
        env = {"WINGBOX_PACKAGE": "true", "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/check.yml@refs/pull/12/merge", "GITHUB_REPOSITORY": "owner/repo"}
        path, state, status = pr.write_package(env, str(out), str(REPO), wingbox_policy.named(env), said.append)
        self.assertEqual((str(out / "wingbox-package.zip"), "complete", 0), (path, state, status))
        self.assertEqual("pack: complete", said[-1])
        manifest = json.loads(dict(members_of((out / "wingbox-package.zip").read_bytes()))["manifest.json"])
        self.assertIn({"path": ".github/workflows/check.yml", "kind": "workflow", "state": "present", "member": "policy:.github/workflows/check.yml"},
                      manifest["policy"]["files"])
        self.assertEqual([], found(out / "wingbox-package.zip", "--repo", REPO))
        # A second run in the same folder writes its own package anew; a folder without the reports gives an incomplete one.
        self.assertEqual("complete", pr.write_package(env, str(out), str(REPO), {}, said.append)[1])
        (folder / "elsewhere").mkdir()
        path, state, status = pr.write_package({**env, "WINGBOX_TEST_RESULTS_ROOT": str(folder / "elsewhere")}, str(out), str(REPO), {}, said.append)
        self.assertEqual(("incomplete", 1), (state, status))
        import wingbox_gitlab
        self.assertIn("WINGBOX_PACKAGE_STATE=complete\n", wingbox_gitlab.dotenv({"result": "PASS", **wingbox_gitlab.package_values("complete")}))
        self.assertEqual({}, wingbox_gitlab.package_values(""))

    def test_the_documents_describe_the_package(self) -> None:
        """/* REQ-ACTION-188: README.md has the section "The evidence package" with the three commands as `python wingbox_evidence_cli.py`, the name `wingbox-evidence` the library will install, the exit statuses, the form of the archive, the members, the manifest and its schema, the review record and its schema, the place of the attestation, what `verify` cannot show, and the input `package` with its outputs and the GitLab variable; CHANGELOG.md names the input, the command and the new schemas; the three schemas are in schema/ and each names itself */"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("\n## The evidence package\n", 1)[1].split("\n## ", 1)[0]
        for needed in ("python wingbox_evidence_cli.py pack --run-dir", "python wingbox_evidence_cli.py verify", "python wingbox_evidence_cli.py review",
                       "`wingbox-evidence`", "| 0 |", "| 1 |", "| 2 |", "manifest.json", "schema/wingbox-package-1.schema.json",
                       "schema/wingbox-review-1.schema.json", "attestation/manifest.json.attestation", "1980-01-01", "--manifest-sha256",
                       "`altered-member`", "`missing-member`", "`duplicate-member`", "`unexpected-member`", "`manifest-mismatch`",
                       "`policy-differs-from-commit`", "`unsafe-member-name`", "NOT RUN", "WINGBOX_PACKAGE", "wingbox-package.zip"):
            self.assertIn(needed, section, needed)
        for needed in ("| `package` |", "| `package-state` |", "WINGBOX_PACKAGE_STATE"):
            self.assertIn(needed, readme, needed)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for needed in ("`package: true`", "python wingbox_evidence_cli.py pack", "schema/wingbox-package-1.schema.json"):
            self.assertIn(needed, changelog, needed)
        for name, word in zip(SCHEMA_NAMES, ("wingbox-evidence/2", "wingbox-package/1", "wingbox-review/1")):
            schema = json.loads((ROOT / "schema" / name).read_text(encoding="utf-8"))
            self.assertEqual(([word], f"https://github.com/aileron-tech/wingbox-action/blob/main/schema/{name}"),
                             (schema["properties"]["schema"]["enum"], schema["$id"]))
        self.assertEqual((SCHEMA_NAMES, "wingbox-package/1", "wingbox-review/1"), (pkg.SCHEMA_FILES, pkg.SCHEMA, pkg.REVIEW_SCHEMA))


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class RunTest(unittest.TestCase):
    def test_a_run_with_the_input_leaves_a_package_that_verifies(self) -> None:
        """/* REQ-ACTION-186: run as on a runner on the fixture repository with WINGBOX_PACKAGE true, the script writes wingbox-package.zip beside the record, gives the outputs `package` and `package-state` (complete), ends with status 0, and `verify --repo` of that package against the fixture repository ends with status 0; the record in the package is the record of the run, byte for byte */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, temp = Path(tmp) / "repo", Path(tmp) / "runner"
            temp.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            base = make_fixture.commit(repo, make_fixture.BASE, "base")
            head = make_fixture.commit(repo, make_fixture.HEAD, "head")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_"))}
            env.update(WINGBOX_BASE=base, WINGBOX_HEAD=head, WINGBOX_COMMENT="false", WINGBOX_PACKAGE="true", RUNNER_TEMP=str(temp),
                       GITHUB_OUTPUT=str(temp / "output.txt"))
            done = subprocess.run([sys.executable, "-P", str(ROOT / "wingbox_pr.py")], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            outputs = dict(line.split("=", 1) for line in (temp / "output.txt").read_text(encoding="utf-8").splitlines())
            self.assertEqual((str(temp / "wingbox-package.zip"), "complete"), (outputs["package"], outputs["package-state"]))
            self.assertEqual(["output.txt", "wingbox-evidence.json", "wingbox-package.zip", "wingbox-plan.sexp", "wingbox-report.md"],
                             sorted(path.name for path in temp.iterdir()))
            self.assertEqual([], found(temp / "wingbox-package.zip", "--repo", repo))
            members = dict(members_of((temp / "wingbox-package.zip").read_bytes()))
            self.assertEqual((temp / "wingbox-evidence.json").read_bytes(), members["record/wingbox-evidence.json"])
            self.assertEqual((temp / "wingbox-report.md").read_bytes(), members["record/wingbox-report.md"])
            self.assertEqual("", subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout)


if __name__ == "__main__":
    unittest.main()

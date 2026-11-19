"""The evidence package of one run: one archive that stands on its own, with a manifest that can be checked.

A run leaves a record, a report and a plan. The package gathers them with what the record refers to: the test
reports as they were written, the job log when one is given, the policy files as the commit checked holds
them, and the schemas. `manifest.json` names every member by id, safe relative path, role, media type,
length and SHA-256, lists the required members with the state of each, and says `complete` or `incomplete`.

The order of sealing has no circle: the members are written, then the manifest that covers their bytes.
The manifest holds neither its own digest nor a signature; a review is a separate member that names the
SHA-256 of the manifest it reviewed, and a detached attestation of the manifest has a reserved member name
(`ATTESTATION`) that this module leaves empty: signing is not done here.

Three statements stay apart. The record states that a check ran and how it ended. A review states that a
person accepted or rejected the evidence. That a control conforms is stated nowhere.

Pure: bytes in, bytes and values out. No file, no clock, no git, no network; the same members give the same
archive bytes. The command script wingbox_evidence_cli.py is the I/O. Standard library only, and no import
of another module of this repository, so the module can be lifted out as it is.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

SCHEMA = "wingbox-package/1"
REVIEW_SCHEMA = "wingbox-review/1"
RECORD_SCHEMA = "wingbox-evidence/2"
MANIFEST = "manifest.json"
RECORD = "record/wingbox-evidence.json"
RECORD_FILE = "wingbox-evidence.json"
ATTESTATION = "attestation/manifest.json.attestation"   # the place of a detached attestation of the manifest; never written here
SCHEMA_FILES = ("wingbox-evidence-2.schema.json", "wingbox-package-1.schema.json", "wingbox-review-1.schema.json")
SIDECAR_ENDING = ".run.json"
# The policy files with a fixed place, and what each is (the same as wingbox_policy.FIXED; a test keeps them equal).
POLICY_FILES = {".wingbox/baseline.sexp": "baseline", ".wingbox/config.yaml": "rules", ".wingbox/learned.sexp": "rules",
                ".wingbox/required-tests.yaml": "required-tests"}
POLICY_KINDS = ("baseline", "rules", "declared-rules", "workflow", "required-tests", "named")
DECISIONS = ("accepted", "rejected")
MAX_BYTES = 256 * 1024 * 1024    # of an archive, unless the caller gives another limit
HARD_LIMIT = 0xFFFFFFFF          # the archive form has four bytes for a length
MAX_MEMBERS = 4096
NAME_LIMIT = 240
PRESENT, ABSENT, DIFFERS, UNREADABLE = "present", "absent", "differs", "unreadable"
COMPLETE, INCOMPLETE = "complete", "incomplete"
# One outcome per check and per case, in the words a reviewer reads.
PASS, FAIL, SKIP, NOT_RUN, UNKNOWN = "PASS", "FAIL", "SKIP", "NOT RUN", "UNKNOWN"
OUTCOMES = {"passed": PASS, "failed": FAIL, "skipped": SKIP, "no-record": NOT_RUN, "none": UNKNOWN}
STATEMENTS = {
    "check": "The record states which checks ran and how each ended.",
    "acceptance": "A review record states that a person accepted or rejected this evidence; the tool makes none.",
    "conformity": "Nothing in this package states that a control conforms.",
}
CONTROL = {"mapping": "none", "id": "", "edition": "", "scope": "", "owner": "", "applies": "", "risk": ""}
_COMMIT = re.compile(r"^[0-9a-f]{40,64}\Z")
_REVIEW = re.compile(r"^reviews/(\d{4})\.json$")
_SEGMENT = re.compile(r"^[A-Za-z0-9._%+=@,-]+$")
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))})
_MEDIA = {".json": "application/json", ".md": "text/markdown", ".sexp": "text/plain", ".txt": "text/plain", ".log": "text/plain",
          ".xml": "application/xml", ".yaml": "application/yaml", ".yml": "application/yaml"}


@dataclass(frozen=True)
class Problem:
    """One thing a check found: a fixed word, the member it is about ("" for the archive), and the detail."""
    code: str
    member: str
    detail: str


@dataclass(frozen=True)
class Given:
    """What a package states that its members do not hold: said by whoever packs, kept by a check as said."""
    retention: str = ""                          # how long the job keeps the package, in the job's own words
    stated: tuple[tuple[str, str], ...] = ()     # versions the job states (build, model, prompt, tool schema, test data): name, value
    read: bool = False                           # whether the files of the commit checked could be read
    policy: tuple[tuple[str, str], ...] = ()     # (path, kind) of every policy file asked for, in the commit or not
    job_log: str = ""                            # the file name of the job log given; "" for none


@dataclass(frozen=True)
class Schemas:
    """The schemas a check is made with: the ones of the tool that checks, never the ones inside a package."""
    package: Mapping[str, Any]
    review: Mapping[str, Any]
    record: Mapping[str, Any]


@dataclass(frozen=True)
class Wanted:
    """What `pack` has to read for a record: the documents and test reports it names, the commit checked and
    the policy files to ask that commit for."""
    documents: tuple[str, ...] = ()
    reports: tuple[str, ...] = ()
    commit: str = ""
    policy: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Checked:
    """What a check of an archive found."""
    problems: tuple[Problem, ...]
    stated: Optional[Mapping[str, Any]] = None      # the manifest, when it could be read and meets its schema
    manifest_sha256: str = ""
    reviews: tuple[Mapping[str, Any], ...] = ()
    record: Optional[Mapping[str, Any]] = None
    attested: bool = False                          # a member is at the reserved place; it is not checked here

    @property
    def intact(self) -> bool:
        """Whether every byte is as the manifest says: no problem but a required member the manifest itself calls absent."""
        return all(problem.code == "incomplete" for problem in self.problems)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Member names
# ---------------------------------------------------------------------------

def unsafe(name: str) -> str:
    """Why a member name is not a safe relative path; "" when it is one. Safe: segments of A-Z a-z 0-9 and
    `. _ % + = @ , -` joined by `/`; no empty segment (so no leading or trailing `/`), no `.` or `..`, no
    backslash, no colon, no segment that ends with a dot or is a device name of Windows."""
    if not name:
        return "empty"
    if len(name) > NAME_LIMIT:
        return f"longer than {NAME_LIMIT} characters"
    if name.startswith("/"):
        return "an absolute path"
    if "\\" in name:
        return "holds a backslash"
    for segment in name.split("/"):
        if not segment:
            return "an empty segment"
        if segment in (".", ".."):
            return "a segment that leaves or repeats its folder"
        if not _SEGMENT.match(segment):
            return "a character outside A-Z a-z 0-9 . _ % + = @ , -"
        if segment.endswith("."):
            return "a segment that ends with a dot"
        if segment.split(".", 1)[0].upper() in _DEVICES:
            return "a device name of Windows"
    return ""


def safe_name(path: str) -> str:
    """A path of a repository or a file name as a safe member name: every byte outside A-Z a-z 0-9 `.` `_` `-`
    is written %XX, segment by segment; an empty segment (a leading `/`) is %2F; a segment of dots, a dot at
    the end of a segment and the first letter of a device name are written %XX too. Two paths give two names."""
    parts = []
    for segment in path.replace("\\", "/").split("/"):
        text = "".join(chr(byte) if (byte < 128 and chr(byte).isalnum()) or chr(byte) in "._-" else f"%{byte:02X}" for byte in segment.encode("utf-8"))
        if not text:
            text = "%2F"
        elif set(text) == {"."}:
            text = "%2E" * len(text)
        elif text.endswith("."):
            text = text[:-1] + "%2E"
        if text.split(".", 1)[0].upper() in _DEVICES:
            text = f"%{ord(text[0]):02X}" + text[1:]
        parts.append(text)
    return "/".join(parts)


def media_type(path: str) -> str:
    dot = path.rfind(".")
    return _MEDIA.get(path[dot:].lower(), "application/octet-stream") if dot > path.rfind("/") else "application/octet-stream"


def document_path(name: str) -> str:
    """The member of a document the record names; "" when the name is not a plain file name of its own."""
    return "" if not name or "/" in name or "\\" in name or name == RECORD_FILE or unsafe(name) else f"record/{name}"


def report_path(path: str) -> str:
    return "sources/test-reports/" + safe_name(path)


def policy_path(path: str) -> str:
    return "policy/" + safe_name(path)


def job_log_path(name: str) -> str:
    return "sources/job-log/" + safe_name(name)


def schema_path(name: str) -> str:
    return "schema/" + name


# ---------------------------------------------------------------------------
# The archive: a zip file in one fixed form
# ---------------------------------------------------------------------------
# Members are stored as they are (no compression: a compressor may give other bytes on another machine), each
# with the date 1980-01-01 00:00:00, no extra field, no comment, the mode of a regular file, in the order
# given. So the same members give the same bytes, and a reader that knows only this form refuses a link, a
# compressed or encrypted member, and bytes that belong to no member.

_LOCAL = struct.Struct("<IHHHHHIIIHH")
_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_END = struct.Struct("<IHHHHIIH")
_LOCAL_MARK, _CENTRAL_MARK, _END_MARK = 0x04034B50, 0x02014B50, 0x06054B50
_VERSION = 20
_MADE_BY = (3 << 8) | _VERSION       # a Unix file system: the mode below is read
_DATE = (1 << 5) | 1                 # 1980-01-01
_MODE = 0o100644 << 16
_FILE_TYPE, _REGULAR, _LINK = 0o170000, 0o100000, 0o120000


def archive(members: Sequence[tuple[str, bytes]]) -> bytes:
    """The archive of the members, in the order given. ValueError for an unsafe or a repeated name."""
    seen: set[str] = set()
    parts: list[bytes] = []
    directory: list[bytes] = []
    offset = 0
    for name, data in members:
        if unsafe(name) or name in seen:
            raise ValueError(f"not a member name: {name!r}")
        seen.add(name)
        raw = name.encode("ascii")
        checksum = zlib.crc32(data) & 0xFFFFFFFF
        local = _LOCAL.pack(_LOCAL_MARK, _VERSION, 0, 0, 0, _DATE, checksum, len(data), len(data), len(raw), 0) + raw
        directory.append(_CENTRAL.pack(_CENTRAL_MARK, _MADE_BY, _VERSION, 0, 0, 0, _DATE, checksum, len(data), len(data), len(raw),
                                       0, 0, 0, 0, _MODE, offset) + raw)
        parts += [local, data]
        offset += len(local) + len(data)
    central = b"".join(directory)
    return b"".join(parts) + central + _END.pack(_END_MARK, 0, 0, len(directory), len(directory), len(central), offset, 0)


def read(data: bytes) -> tuple[list[tuple[str, bytes]], list[Problem]]:
    """(the members of an archive in its order, what is wrong with it). Nothing is unpacked to disk and
    nothing is run. A member with an unsafe or a repeated name is named and not returned; a link, a compressed
    or encrypted member, a directory, a comment and bytes outside the members are refused."""
    whole = [Problem("archive", "", "no end record in the last 22 bytes: not an archive of this form, or one with a comment or bytes after it")]
    if len(data) < _END.size or data[-_END.size:-_END.size + 4] != b"PK\x05\x06":
        return [], whole
    _mark, disk, first_disk, here, total, size, start, comment = _END.unpack(data[-_END.size:])
    if disk or first_disk or here != total or comment or start + size + _END.size != len(data):
        return [], [Problem("archive", "", "the end record does not describe this file")]
    if total > MAX_MEMBERS:
        return [], [Problem("archive", "", f"{total} members, more than {MAX_MEMBERS}")]
    members: list[tuple[str, bytes]] = []
    problems: list[Problem] = []
    seen: set[str] = set()
    position, expected = start, 0
    for _index in range(total):
        if position + _CENTRAL.size > start + size:
            return members, problems + [Problem("archive", "", "the directory ends before its last entry")]
        (mark, _made, _need, flags, method, _time, _date, checksum, packed, length, name_length, extra, noted, _disk, _inner, outer,
         offset) = _CENTRAL.unpack_from(data, position)
        raw = data[position + _CENTRAL.size:position + _CENTRAL.size + name_length]
        position += _CENTRAL.size + name_length + extra + noted
        if mark != _CENTRAL_MARK:
            return members, problems + [Problem("archive", "", "a directory entry is not one")]
        name = raw.decode("latin-1")
        shown = ascii(name)[1:-1]
        why = unsafe(name)
        kind = (outer >> 16) & _FILE_TYPE
        bad = bool(why) or name in seen
        if why:
            problems.append(Problem("unsafe-member-name", shown, why))
        elif name in seen:
            problems.append(Problem("duplicate-member", shown, "the archive holds this name more than once"))
        seen.add(name)
        if kind == _LINK:
            problems.append(Problem("link-member", shown, "a symbolic link"))
            bad = True
        elif kind not in (0, _REGULAR):
            problems.append(Problem("archive", shown, "not a regular file"))
            bad = True
        if method or flags or packed != length:
            problems.append(Problem("archive", shown, "compressed, encrypted or streamed; a package stores its members as they are"))
            return members, problems
        body_start = offset + _LOCAL.size + name_length
        if offset != expected or body_start + length > start or data[offset + 26:offset + 30] != struct.pack("<HH", name_length, 0):
            problems.append(Problem("archive", shown, "its bytes are not where the directory says: a gap, an overlap or an extra field"))
            return members, problems
        body = data[body_start:body_start + length]
        if zlib.crc32(body) & 0xFFFFFFFF != checksum:
            problems.append(Problem("altered-member", shown, "its bytes do not give the checksum the archive holds"))
            bad = True
        expected = body_start + length
        if not bad:
            members.append((name, body))
    if not problems and (expected != start or position != start + size or archive(members) != data):
        problems.append(Problem("archive", "", "not in the form `pack` writes: a header field, a date or bytes between the members differ"))
    return members, problems


# ---------------------------------------------------------------------------
# A schema check (the part of JSON Schema the schemas of this repository use)
# ---------------------------------------------------------------------------

_TYPES: dict[str, type] = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}


def schema_problems(value: Any, schema: Mapping[str, Any], where: str = "$") -> list[str]:
    """What of the schema the value does not meet: type, enum, pattern, required, properties, no other
    properties where the schema says so, items."""
    expected = _TYPES[schema["type"]]
    if not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
        return [f"{where} is not {schema['type']}"]
    found = []
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where} is {value!r}, not one of {', '.join(map(str, schema['enum']))}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        found.append(f"{where} does not match {schema['pattern']}")
    if isinstance(value, dict):
        found += [f"{where}.{name} is missing" for name in schema.get("required", ()) if name not in value]
        for name, item in value.items():
            if name in schema.get("properties", {}):
                found += schema_problems(item, schema["properties"][name], f"{where}.{name}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{where}.{name} is not in the schema")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            found += schema_problems(item, schema["items"], f"{where}[{index}]")
    return found


def parse(data: Optional[bytes]) -> Any:
    """The JSON value of the bytes; None when there are none or they are not JSON in UTF-8."""
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return None


def encode(value: Any) -> bytes:
    """A manifest or a review as its bytes: JSON with two spaces, the keys in the order written, ASCII, one
    line feed at the end."""
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def read_record(data: Optional[bytes], schema: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The record of the bytes when they are a record of version 2 that meets its schema; else None."""
    value = parse(data)
    return value if isinstance(value, dict) and value.get("schema") == RECORD_SCHEMA and not schema_problems(value, schema) else None


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def commit_of(record: Optional[Mapping[str, Any]]) -> str:
    """The commit whose files were checked, as the record names it; the head when the record names no checked
    commit; "" when neither is a full commit id."""
    subject = record["subject"] if record else {}
    return next((value for value in (subject.get("checked", ""), subject.get("head", "")) if _COMMIT.match(value)), "")


def _plain(path: str) -> str:
    """A path from the root of a repository in one spelling; "" when it is absolute or leaves the repository."""
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "" if not text or text.startswith("/") or ":" in text.split("/")[0] or ".." in text.split("/") else text


def policy_files(record: Optional[Mapping[str, Any]], named: Sequence[tuple[str, str]] = ()) -> tuple[tuple[str, str], ...]:
    """(path, kind) of the policy files to ask the commit for, by path: the ones with a fixed place, the ones
    the record says the change edits, the manifest of required tests the record read, and the ones named by
    whoever packs. The first kind found for a path stays."""
    found: dict[str, str] = dict(POLICY_FILES)
    if record:
        for change in record["policy"]["changed"]:
            found.setdefault(_plain(change["path"]), change["kind"])
        found.setdefault(_plain(record["tests"].get("manifest", {}).get("path", "")), "required-tests")
    for path, kind in named:
        found.setdefault(_plain(path), kind)
    found.pop("", None)
    return tuple(sorted(found.items()))


def wanted(record_bytes: Optional[bytes], schema: Mapping[str, Any], named: Sequence[tuple[str, str]] = ()) -> Wanted:
    """What to read for the record of these bytes."""
    record = read_record(record_bytes, schema)
    if record is None:
        return Wanted(policy=policy_files(None, named))
    return Wanted(tuple(sorted({document["name"] for document in record["documents"] if document_path(document["name"])})),
                  tuple(sorted({report["path"] for report in record["tests"]["reports"]})), commit_of(record), policy_files(record, named))


def _outcome(test: Mapping[str, Any]) -> str:
    """One outcome for a test from the word of the record: `partial` (a case passed, another was skipped or has
    no record) is NOT RUN when a case has no record, else SKIP; `none` (no test report was given) is UNKNOWN."""
    if test["status"] == "partial":
        return NOT_RUN if any(case["status"] == "no-record" for case in test["records"]) else SKIP
    return OUTCOMES[test["status"]]


def checks(record: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every required test and every test that uses what changed, each with its exact identifier, one outcome,
    and its cases with one outcome each."""
    if not record:
        return []

    def one(kind: str, test: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": test["test"], "kind": kind, "group": test.get("group", ""), "outcome": _outcome(test),
                "cases": [{"id": f"{case['where']}::{case['name']}", "outcome": OUTCOMES[case["status"]]} for case in test["records"]]}
    return ([one("required-test", test) for test in record["tests"].get("required", ())]
            + [one("uses-what-changed", test) for test in record["tests"]["to-run"]])


def manifest(members: Mapping[str, bytes], given: Given, record_schema: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest of the members (by member path, the manifest itself and the reviews left out) with what
    is given. Everything but `given` is computed from the bytes of the members, so a check computes it again
    and compares."""
    record = read_record(members.get(RECORD), record_schema)
    entries: list[dict[str, Any]] = []
    needs: list[dict[str, str]] = []

    def entry(name: str, path: str, role: str, required: bool, source: str = "", commit: str = "") -> None:
        if any(item["path"] == path for item in entries):
            return             # one member per path: the first that names it
        data = members[path]
        entries.append({"id": name, "path": path, "role": role, "media-type": media_type(path), "length": len(data), "sha256": sha256(data),
                        "required": required, "source": source, "commit": commit})

    def need(name: str, path: str, role: str, digest: str = "", source: str = "") -> None:
        """A required member: there, absent, or there with other bytes than the record names."""
        state, reason = PRESENT, ""
        if not path or path not in members:
            state, reason = ABSENT, "not a plain file name" if not path else "not found by `pack`, or larger than its limit"
        else:
            entry(name, path, role, True, source)
            if digest and sha256(members[path]) != digest:
                state, reason = DIFFERS, "its SHA-256 is not the one the record names"
        needs.append({"id": name, "path": path, "state": state, "reason": reason})

    if RECORD not in members:
        needs.append({"id": "record", "path": RECORD, "state": ABSENT, "reason": "no wingbox-evidence.json in the run folder"})
    else:
        entry("record", RECORD, "record", True, RECORD_FILE)
        needs.append({"id": "record", "path": RECORD, "state": PRESENT if record else UNREADABLE,
                      "reason": "" if record else f"not a {RECORD_SCHEMA} record"})
    commit = commit_of(record)
    if record:
        for name, digest in sorted({document["name"]: document["sha256"] for document in record["documents"]}.items()):
            need(f"document:{name}", document_path(name), "document", digest, name)
        for path, digest in sorted({report["path"]: report["sha256"] for report in record["tests"]["reports"]}.items()):
            need(f"test-report:{path}", report_path(path), "test-report", digest, path)
            beside = report_path(path + SIDECAR_ENDING)
            if beside in members:
                entry(f"sidecar:{path}", beside, "test-report-sidecar", False, path + SIDECAR_ENDING)
    read_commit = bool(given.read and commit)
    needs.append({"id": "policy-files", "path": "", "state": PRESENT if read_commit else ABSENT,
                  "reason": "" if read_commit else "the commit checked could not be read"})
    files = []
    for path, kind in given.policy:
        there = read_commit and policy_path(path) in members
        if there:
            entry(f"policy:{path}", policy_path(path), "policy", False, path, commit)
        files.append({"path": path, "kind": kind, "state": PRESENT if there else "not-in-commit" if read_commit else "not-read",
                      "member": f"policy:{path}" if there else ""})
    for name in SCHEMA_FILES:
        need(f"schema:{name}", schema_path(name), "schema")
    log = job_log_path(given.job_log) if given.job_log else ""
    if log in members:
        entry("job-log", log, "job-log", False, given.job_log)
    entries.sort(key=lambda item: item["path"])
    absent = [item["id"] for item in needs if item["state"] != PRESENT]
    subject = record["subject"] if record else {}
    run = record["run"] if record else {}
    return {
        "schema": SCHEMA,
        "bundle": sha256("".join(f"{item['sha256']}  {item['path']}\n" for item in entries).encode("ascii")),
        "state": INCOMPLETE if absent else COMPLETE,
        "created": record["generated"] if record else "",
        "tool": dict(record["tool"]) if record else {"name": "", "ref": "", "wingbox": ""},
        "subject": {"repository": run.get("repository", ""), "base": subject.get("base", ""), "head": subject.get("head", ""),
                    "checked": subject.get("checked", ""), "tree": subject.get("tree", "")},
        "run": {"provider": run.get("provider", ""), "id": run.get("id", ""), "attempt": run.get("attempt", ""), "job": run.get("job", ""),
                "change": run.get("change", ""), "url": run.get("url", "")},
        "retention": given.retention,
        "control": dict(CONTROL),
        "stated": [{"name": name, "value": value} for name, value in given.stated],
        "policy": {"commit": commit, "read": read_commit, "files": files},
        "checks": checks(record),
        "members": entries,
        "required": needs,
        "optional": [{"id": "job-log", "state": PRESENT if log in members else ABSENT}],
        "absent": absent,
        "statements": dict(STATEMENTS),
        "attestation": {"member": ATTESTATION, "over": MANIFEST},
    }


def given_of(stated: Mapping[str, Any]) -> Given:
    """What a manifest that meets its schema says was given."""
    log = next((entry["source"] for entry in stated["members"] if entry["id"] == "job-log"), "")
    return Given(stated["retention"], tuple((item["name"], item["value"]) for item in stated["stated"]), stated["policy"]["read"],
                 tuple((item["path"], item["kind"]) for item in stated["policy"]["files"]), log)


def pack(members: Mapping[str, bytes], given: Given, record_schema: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """(the archive, its manifest): the manifest first, then the members it names by path."""
    stated = manifest(members, given, record_schema)
    return archive([(MANIFEST, encode(stated))] + [(entry["path"], members[entry["path"]]) for entry in stated["members"]]), stated


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def review_name(sequence: int) -> str:
    return f"reviews/{sequence:04d}.json"


def review(sequence: int, manifest_sha256: str, bundle: str, reviewer: str, decision: str, grounds: str, time: str, previous: str) -> dict[str, Any]:
    """A decision of a person about the evidence: a record of its own that names the manifest it is about and
    the review before it."""
    return {"schema": REVIEW_SCHEMA, "sequence": sequence, "manifest-sha256": manifest_sha256, "bundle": bundle, "reviewer": reviewer,
            "decision": decision, "grounds": grounds, "time": time, "previous": previous}


def _review_problems(found: Sequence[tuple[str, bytes]], manifest_sha256: str, schema: Mapping[str, Any]) -> tuple[list[Problem], list[dict[str, Any]]]:
    """(what is wrong with the reviews of an archive, the reviews that could be read). They are numbered from
    1 without a gap, each names the manifest of this archive and the SHA-256 of the review before it."""
    problems: list[Problem] = []
    reviews: list[dict[str, Any]] = []
    previous = ""
    for index, (name, body) in enumerate(sorted(found), start=1):
        said = parse(body)
        if name != review_name(index):
            problems.append(Problem("review-invalid", name, f"{review_name(index)} is not there: a review before this one was removed"))
        elif not isinstance(said, dict) or schema_problems(said, schema):
            problems.append(Problem("review-invalid", name, "not a review record: " + "; ".join(schema_problems(said, schema)[:3])))
        elif said["sequence"] != index:
            problems.append(Problem("review-invalid", name, f"says it is review {said['sequence']}"))
        elif said["manifest-sha256"] != manifest_sha256:
            problems.append(Problem("review-invalid", name, "is about another manifest than the one of this archive"))
        elif said["previous"] != previous:
            problems.append(Problem("review-invalid", name, "does not name the review before it as it is"))
        else:
            reviews.append(said)
        previous = sha256(body)
    return problems, reviews


# ---------------------------------------------------------------------------
# The check of an archive
# ---------------------------------------------------------------------------

def _differing(one: Mapping[str, Any], other: Mapping[str, Any]) -> str:
    return ", ".join(key for key in dict.fromkeys([*one, *other]) if one.get(key) != other.get(key))


def verify(data: bytes, schemas: Schemas, expected_manifest: str = "", limit: int = MAX_BYTES) -> Checked:
    """Check an archive against itself. Every member the manifest names is there once with the length and the
    SHA-256 named; no other member is there but the reviews and the attestation at its place; the manifest
    meets its schema and is what its members give when it is computed again; each review names this manifest
    and the review before it. expected_manifest: the SHA-256 the manifest must have, when one was kept
    elsewhere (a ledger line, a receipt). A package whose manifest says a required member is absent is intact
    and incomplete: that is a problem too."""
    if len(data) > limit:
        return Checked((Problem("too-large", "", f"{len(data)} bytes, more than the limit of {limit}"),))
    members, problems = read(data)
    if problems:
        return Checked(tuple(problems))
    by_name = dict(members)
    raw = by_name.get(MANIFEST)
    if raw is None:
        return Checked((Problem("missing-member", MANIFEST, "the archive has no manifest"),))
    digest = sha256(raw)
    if expected_manifest and digest != expected_manifest.lower():
        problems.append(Problem("manifest-digest", MANIFEST, f"its SHA-256 is {digest}, not the one given"))
    stated = parse(raw)
    wrong = schema_problems(stated, schemas.package) if isinstance(stated, dict) else ["not a JSON object"]
    if wrong:
        return Checked((*problems, Problem("manifest-invalid", MANIFEST, "; ".join(wrong[:5]))), manifest_sha256=digest)
    named: dict[str, Mapping[str, Any]] = {}
    for entry in stated["members"]:
        path = entry["path"]
        body = by_name.get(path)
        if unsafe(path):
            problems.append(Problem("unsafe-member-name", ascii(path)[1:-1], unsafe(path)))
        elif path in named or path == MANIFEST:
            problems.append(Problem("duplicate-member", path, "the manifest names this path more than once"))
        elif body is None:
            problems.append(Problem("missing-member", path, f"the manifest names it ({entry['id']}), the archive does not hold it"))
        elif len(body) != entry["length"] or sha256(body) != entry["sha256"]:
            problems.append(Problem("altered-member", path, f"{len(body)} bytes with SHA-256 {sha256(body)}; the manifest names "
                                                            f"{entry['length']} bytes with {entry['sha256']}"))
        named[path] = entry
    reviews = []
    for name, body in members:
        if _REVIEW.match(name):
            reviews.append((name, body))
        elif name not in named and name not in (MANIFEST, ATTESTATION):
            problems.append(Problem("unexpected-member", name, "the manifest does not name it"))
    record = read_record(by_name.get(RECORD), schemas.record)
    if not problems or all(problem.code == "manifest-digest" for problem in problems):
        again = manifest({path: by_name[path] for path in named}, given_of(stated), schemas.record)
        if again != stated:
            problems.append(Problem("manifest-mismatch", MANIFEST, f"its members give another manifest; what differs: {_differing(again, stated)}"))
        elif encode(again) != raw:
            problems.append(Problem("manifest-mismatch", MANIFEST, "not written in the form `pack` writes"))
    found, read_reviews = _review_problems(reviews, digest, schemas.review)
    problems += found
    problems += [Problem("incomplete", need["id"], f"{need['state']}: {need['reason']}") for need in stated["required"] if need["state"] != PRESENT]
    return Checked(tuple(problems), stated, digest, tuple(read_reviews), record, ATTESTATION in by_name)


def with_review(data: bytes, schemas: Schemas, reviewer: str, decision: str, grounds: str, time: str,
                limit: int = MAX_BYTES) -> tuple[Optional[bytes], Checked]:
    """(the archive with one more review at its end, the check of the archive given). None when the archive
    given is not intact: a decision is recorded only about bytes that are what their manifest says. The
    members before the new one keep their bytes and their place."""
    checked = verify(data, schemas, limit=limit)
    if not checked.intact or checked.stated is None:
        return None, checked
    members, _problems = read(data)
    before = sorted((name, body) for name, body in members if _REVIEW.match(name))
    said = review(len(before) + 1, checked.manifest_sha256, checked.stated["bundle"], reviewer, decision, grounds, time,
                  sha256(before[-1][1]) if before else "")
    return archive([*members, (review_name(len(before) + 1), encode(said))]), checked


# ---------------------------------------------------------------------------
# The check against a repository
# ---------------------------------------------------------------------------

def git_wanted(checked: Checked) -> list[tuple[str, str]]:
    """(commit, path) of every file whose content in a commit a check against the repository compares: the
    policy files the manifest lists, in the commit it names, and the changed files of the record, in the
    commit checked."""
    stated, record = checked.stated or {}, checked.record or {}
    commit = stated.get("policy", {}).get("commit", "")
    found = [(commit, item["path"]) for item in stated.get("policy", {}).get("files", ())] if commit else []
    files_commit = record.get("subject", {}).get("checked", "")
    if _COMMIT.match(files_commit):
        found += [(files_commit, item["path"]) for item in record.get("files", ())]
    return found


def git_problems(checked: Checked, commits: Mapping[str, bool], digests: Mapping[tuple[str, str], Optional[str]]) -> list[Problem]:
    """What of a package is not as the repository has it. commits: whether the repository has each commit.
    digests: per (commit, path) of `git_wanted` the SHA-256 of the file in that commit, None when the commit
    has no such file."""
    stated, record = checked.stated or {}, checked.record or {}
    policy = stated.get("policy", {})
    commit = policy.get("commit", "")
    problems = []
    if not commit:
        problems.append(Problem("not-recomputable", "", "the package names no commit to compare with"))
    elif not commits.get(commit):
        problems.append(Problem("commit-not-found", commit, "the repository does not have this commit"))
    else:
        sums = {entry["id"]: entry["sha256"] for entry in stated["members"]}
        for item in policy["files"]:
            there, digest = sums.get(item["member"]), digests.get((commit, item["path"]))
            if there != digest:
                problems.append(Problem("policy-differs-from-commit", item["path"], (
                    "the commit has this file, the package does not" if there is None else
                    "the package holds this file, the commit does not" if digest is None else
                    f"SHA-256 {there} in the package, {digest} in commit {commit}")))
    files_commit = record.get("subject", {}).get("checked", "")
    files = record.get("files", ())
    if files and not _COMMIT.match(files_commit):
        problems.append(Problem("not-recomputable", "", "the record names no checked commit for its changed files (a working folder was checked)"))
    elif files and not commits.get(files_commit):
        if files_commit != commit:
            problems.append(Problem("commit-not-found", files_commit, "the repository does not have this commit"))
    else:
        for item in files:
            digest = digests.get((files_commit, item["path"]))
            if digest != item["sha256"]:
                problems.append(Problem("file-differs-from-commit", item["path"], (
                    "the commit has no such file" if digest is None else f"SHA-256 {item['sha256']} in the record, {digest} in commit {files_commit}")))
    return problems


# ---------------------------------------------------------------------------
# What a check says
# ---------------------------------------------------------------------------

def result(checked: Checked, compared: Optional[list[Problem]] = None) -> dict[str, Any]:
    """The check as one value. compared: what the check against a repository found; None when it was not asked."""
    problems = [*checked.problems, *(compared or ())]
    stated = checked.stated or {}
    return {
        "bundle": stated.get("bundle", ""),
        "manifest-sha256": checked.manifest_sha256,
        "integrity": "verified" if checked.intact else "refused",
        "state": stated.get("state", "unknown") if checked.intact else "unknown",
        "members": len(stated.get("members", ())),
        "repository": "not-asked" if compared is None else "does-not-match" if compared else "matches",
        "attestation": "present-not-checked" if checked.attested else "none",
        "reviews": [{"sequence": said["sequence"], "decision": said["decision"], "reviewer": said["reviewer"], "time": said["time"],
                     "grounds": said["grounds"]} for said in checked.reviews],
        "problems": [{"code": problem.code, "member": problem.member, "detail": problem.detail} for problem in problems],
        "ok": not problems,
    }


def lines(said: Mapping[str, Any]) -> list[str]:
    """The check for a reader, one fact per line; the last line says whether everything matched."""
    count = len(said["problems"])
    return [
        f"bundle: {said['bundle'] or 'unknown'}",
        f"manifest-sha256: {said['manifest-sha256'] or 'unknown'}",
        f"integrity: {said['integrity']}",
        f"state: {said['state']}",
        f"members: {said['members']}",
        f"repository: {said['repository']}",
        f"attestation: {said['attestation']}",
        f"reviews: {len(said['reviews'])}",
        *(f"review: {item['sequence']} {item['decision']} by {item['reviewer']} at {item['time']}: {item['grounds']}" for item in said["reviews"]),
        *(f"problem: {item['code']}{' ' + item['member'] if item['member'] else ''}: {item['detail']}" for item in said["problems"]),
        *(f"statement: {name}: {text}" for name, text in STATEMENTS.items()),
        "verify: ok" if not count else f"verify: failed ({count} problem{'' if count == 1 else 's'})",
    ]

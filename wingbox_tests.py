"""Evidence that the tests to run did run: the test cases of JUnit XML reports, matched with test files.

The check never runs a test. A job that ran its tests before the check hands over the report files the test
runner wrote (the `test-results` input); this module reads their test cases and says, for each test the
check names, which cases the reports hold of it and how each ended. A test is passed only when every case
recorded for it passed. It also reads the manifest of required tests: the tests a change of given paths must
pass, whatever the graph finds. Pure, standard library only: the files are read by wingbox_pr.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Mapping, Optional

PASSED, FAILED, SKIPPED, NO_RECORD = "passed", "failed", "skipped", "no-record"
PARTIAL = "partial"                # of a test file: a case passed, and another was skipped or did not run
STATUSES = (PASSED, FAILED, SKIPPED, NO_RECORD)    # of a case, in the order they are counted
SIZE_LIMIT = 64 * 1024 * 1024      # bytes of one report file; a larger one is refused
MANIFEST_PATH = ".wingbox/required-tests.yaml"   # the manifest of required tests, from the root of the repository
SIDECAR_LIMIT = 64 * 1024          # bytes of the sidecar of a report
SIDECAR_ENDING = ".run.json"       # the sidecar of a report: the name of the report with this ending
TRACKED, UNTRACKED, UNKNOWN = "tracked", "untracked", "unknown"     # what git says of a report file
STEP_OUTCOMES = ("success", "failure", "cancelled", "skipped")      # how the test step of the job ended, as GitHub names it
# A file Wingbox reads as source code, by its ending.
SOURCE_FILE = re.compile(r"\.(?:py|pyi|ts|tsx|mts|cts|js|jsx|mjs|cjs|rs|cs|java|kt|go|rb|php|dart|pas|pp)$")


@dataclass(frozen=True)
class Case:
    """One recorded test case: where the report says it is, and how it ended."""
    file: str         # the file attribute of the case or of its suite; "" when the report has none
    classname: str
    suite: str        # the name of the suite that holds it
    status: str       # PASSED, FAILED or SKIPPED
    name: str = ""    # the name of the case


@dataclass(frozen=True)
class TestReport:
    """A report file that was read: its path as given, the SHA-256 of its bytes, the cases it holds; and,
    when it was judged, where it comes from as far as the job knows, and why it was refused."""
    __test__ = False
    path: str
    sha256: str
    cases: int
    refused: tuple[str, ...] = ()    # why it does not count as evidence; empty: it counts
    timestamp: str = ""              # the earliest timestamp a suite of it states, as written
    modified: str = ""               # the modification time of the file, UTC; "" when it was not judged
    tracked: str = UNKNOWN           # TRACKED, UNTRACKED or UNKNOWN (the folder is not in a git work tree)
    commits: tuple[str, ...] = ()    # the commits it names
    sidecar: str = "none"            # none, matches, differs or unreadable
    sidecar_command: str = ""        # the test command its sidecar states


@dataclass(frozen=True)
class Stated:
    """What a report says of itself."""
    timestamps: tuple[str, ...]      # the timestamp of each suite that has one, as written
    commits: tuple[str, ...]         # the commits it names in a property or an attribute of a suite, as written


@dataclass(frozen=True)
class Sidecar:
    """What the test step wrote beside a report (`<report>.run.json`) about the run that made it."""
    commit: str
    run_id: str
    attempt: str
    command: str
    sha256: str                      # of the report


@dataclass(frozen=True)
class Origin:
    """What the job knows of itself, to judge a report by."""
    commit: str = ""                 # the commit checked; "" for a working directory
    started: str = ""                # the start of the run as the job knows it, UTC (YYYY-MM-DDTHH:MM:SSZ); "" when not known
    tolerance: int = 0               # seconds a report may be older than that: the start was dated by another machine's clock
    run_id: str = ""                 # the run a sidecar must name; "" when not known
    attempt: str = ""
    command: str = ""                # the test command the job states, verbatim
    require_sidecar: bool = False    # a report without a sidecar is refused
    utc_offset: int = 0              # of this machine, seconds east of UTC: a timestamp without a zone may be local time or UTC


@dataclass(frozen=True)
class CaseResult:
    """One case of a test to run: where the report puts it (its class name, else its file, else its suite),
    its name and how it ended. NO_RECORD: a test function the file holds and no report names."""
    where: str
    name: str
    status: str       # PASSED, FAILED, SKIPPED or NO_RECORD


@dataclass(frozen=True)
class Evidence:
    """What the reports hold about one test to run."""
    test: str
    status: str       # PASSED, FAILED, SKIPPED, PARTIAL or NO_RECORD
    cases: int        # the recorded cases that belong to it
    records: tuple[CaseResult, ...] = ()   # each of those cases, then the test functions of the file without a record


@dataclass(frozen=True)
class Group:
    """A group of the manifest: the tests a change that touches one of the paths must pass."""
    name: str
    paths: tuple[str, ...]    # globs, folders ending in `/`, or modules (a file without its ending)
    tests: tuple[str, ...]    # test files, or `<file, class or Go folder>::<case>`


@dataclass(frozen=True)
class Manifest:
    """The manifest of required tests as it was read."""
    path: str                 # from the root of the repository
    source: str               # "base": the base commit holds it; "head": only the checked files do; "none": not found
    commit: str               # the base commit it was read from; "" otherwise
    sha256: str               # of its bytes; "" when not found
    groups: tuple[Group, ...] = ()
    errors: tuple[str, ...] = ()   # why it gives no groups: it is refused as a whole


@dataclass(frozen=True)
class Required:
    """A test the manifest requires for this change, with the group that names it."""
    group: str
    test: str


# Names of a property or an attribute that state a commit, in lower case with every other character as `_`.
_COMMIT_NAMES = frozenset({"commit", "commit_sha", "commit_id", "git_commit", "git_commit_sha", "git_commit_id", "git_sha", "git_revision",
                           "sha", "revision", "vcs_revision", "github_sha", "ci_commit_sha", "build_vcs_number"})
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _names_commit(name: str, value: str) -> bool:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") in _COMMIT_NAMES and bool(_COMMIT.match(value))


def parse_junit(data: bytes) -> list[Case]:
    """The test cases of one JUnit XML report (see parse_report)."""
    return parse_report(data)[0]


def parse_report(data: bytes) -> tuple[list[Case], Stated]:
    """(the test cases of one JUnit XML report, what the report says of itself). ValueError when the file is too large, declares a document
    type or entities (none is needed, and they are how an XML file grows on reading), or is not such a report."""
    if len(data) > SIZE_LIMIT:
        raise ValueError(f"larger than {SIZE_LIMIT // (1024 * 1024)} MB")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("declares a document type")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError(f"not XML: {error}") from error
    if _local(root.tag) not in ("testsuites", "testsuite"):
        raise ValueError(f"not a JUnit report: its root is <{_local(root.tag)}>")
    cases: list[Case] = []
    timestamps: list[str] = []
    commits: list[str] = []

    def said(node: ElementTree.Element) -> None:
        """What a suite (or the root) says of itself: its timestamp, and a commit in an attribute or a property."""
        if node.get("timestamp"):
            timestamps.append(node.get("timestamp", ""))
        named = list(node.attrib.items())
        named += [(item.get("name", ""), item.get("value", "")) for group in node if _local(group.tag) == "properties"
                  for item in group if _local(item.tag) == "property"]
        commits.extend(value for name, value in named if _names_commit(name, value))

    def walk(node: ElementTree.Element, suite: str, file: str) -> None:
        said(node)
        for child in node:
            tag = _local(child.tag)
            if tag == "testsuite":
                walk(child, child.get("name", "") or suite, child.get("file", "") or file)
            elif tag == "testcase":
                kinds = {_local(result.tag) for result in child}
                status = FAILED if kinds & {"failure", "error"} else SKIPPED if "skipped" in kinds else PASSED
                cases.append(Case(_path(child.get("file", "") or file), child.get("classname", ""), suite, status, child.get("name", "")))

    walk(root, root.get("name", "") if _local(root.tag) == "testsuite" else "", root.get("file", "") if _local(root.tag) == "testsuite" else "")
    return cases, Stated(tuple(timestamps), tuple(dict.fromkeys(commits)))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _path(text: str) -> str:
    text = text.replace("\\", "/")
    return text[2:] if text.startswith("./") else text


def _forms(test: str) -> tuple[str, ...]:
    """A test as the check names it (a file, or a module: the file without its ending), with and without ending."""
    test = _path(test)
    bare = SOURCE_FILE.sub("", test)
    return (test,) if bare == test else (test, bare)


def _same_file(test: str, place: str) -> bool:
    """Whether a place a report names (a file, absolute or from another folder) is the test file."""
    if not place or "/" not in place and "." not in place:
        return False
    for mine in _forms(test):
        for theirs in _forms(place):
            if mine == theirs or theirs.endswith("/" + mine) or mine.endswith("/" + theirs):
                return True
    return False


def belongs(test: str, case: Case) -> bool:
    """Whether a recorded case is a case of this test file, by what the report says of the case alone.

    By file: the file of the case or of its suite, a class name or a suite name that is a path (JavaScript
    runners). By class name: a dotted name that is the file's path (Python: `tests.test_x.TestCase`) or its
    end (Java, C#: `com.shop.OrderTest` for `src/test/java/com/shop/OrderTest.java`). A record of a Go
    package names no file: see in_go_file."""
    if any(_same_file(test, place) for place in (case.file, _path(case.classname), _path(case.suite))):
        return True
    name = case.classname
    if name and "/" not in name:
        for form in _forms(test):
            dotted = form.replace("/", ".")
            if name == dotted or name.startswith(dotted + ".") or dotted.endswith("." + name):
                return True
    return False


_GO_TEST = re.compile(r"_test(?:\.go)?$")
_GO_TEST_FUNCTION = re.compile(r"^Test(?:[^a-z]|$)")   # what `go test` runs as a test: TestXxx, Xxx not starting in lower case


def _folder(test: str) -> str:
    test = _path(test)
    return test.rsplit("/", 1)[0] if "/" in test else ""


def _in_package(folder: str, case: Case) -> bool:
    """Whether the package a Go record names (its class name or its suite: an import path) is this folder."""
    return bool(folder) and any(place == folder or place.endswith("/" + folder) for place in (_path(case.classname), _path(case.suite)) if place)


def in_go_file(test: str, functions: tuple[str, ...], case: Case) -> bool:
    """Whether a record of a Go package is a case of this test file: the package is the folder of the file
    and the test function of the case (the name before the first `/`: a subtest is `TestX/name`) is one of
    the functions of the file."""
    return _in_package(_folder(test), case) and case.name.split("/", 1)[0] in functions


def go_candidates(tests: list[str], cases: list[Case]) -> list[str]:
    """The tests to run that may be Go test files with a package record: named `..._test.go` or `..._test`
    (a module), in a folder that is the end of a package a report names. Their functions are asked for."""
    return [test for test in tests if _GO_TEST.search(_path(test)) and any(_in_package(_folder(test), case) for case in cases)]


def _status(statuses: list[str]) -> str:
    """The status of a test file from those of its cases: failed when one failed; passed only when every one
    passed; skipped when none passed; partial when one passed and another was skipped or has no record."""
    recorded = [status for status in statuses if status != NO_RECORD]
    if not recorded:
        return NO_RECORD
    if FAILED in recorded:
        return FAILED
    if all(status == PASSED for status in statuses):
        return PASSED
    return PARTIAL if PASSED in recorded else SKIPPED


CASE_MARK = "::"    # in a required test: `<file, class or Go folder>::<case>`


def _is_case(where: str, name: str, case: Case) -> bool:
    """Whether a recorded case is the case `name` of `where`: a test file, a class or suite name as the
    report writes it, or the folder of a Go package. The name with parameters (`name[1]`, pytest) or as a
    subtest (`name/sub`, Go) counts too."""
    there = belongs(where, case) or where in (case.classname, case.suite) or _in_package(_path(where), case)
    return there and (case.name == name or case.name.startswith((name + "[", name + "/")))


def evidence(tests: list[str], cases: list[Case], functions: Optional[Mapping[str, tuple[str, ...]]] = None) -> tuple[Evidence, ...]:
    """For each test (a test to run, or a required test), the recorded cases that belong to it and the
    status they give it (see _status). A test named `<where>::<case>` gets the cases of that name there.

    functions: for a Go test file, by its name among the tests, the functions the file holds. A record of a
    Go package counts for the file that holds its test function, and a test function of the file that no
    report names is kept as a case without a record; a Go test file whose functions are not known gets no
    package record."""
    found = []
    for test in tests:
        if CASE_MARK in test:
            where, name = test.rsplit(CASE_MARK, 1)
            one = [CaseResult(case.classname or case.file or case.suite, case.name, case.status) for case in cases if _is_case(where, name, case)]
            found.append(Evidence(test, _status([record.status for record in one]), len(one), tuple(one)))
            continue
        mine = (functions or {}).get(test)
        matched = [case for case in cases if belongs(test, case) or (mine is not None and in_go_file(test, mine, case))]
        records = [CaseResult(case.classname or case.file or case.suite, case.name, case.status) for case in matched]
        if mine is not None:
            ran = {case.name.split("/", 1)[0] for case in matched}
            records += [CaseResult(_folder(test), name, NO_RECORD) for name in mine
                        if _GO_TEST_FUNCTION.match(name) and name != "TestMain" and name not in ran]
        found.append(Evidence(test, _status([record.status for record in records]), len(matched), tuple(records)))
    return tuple(found)


# ---------------------------------------------------------------------------
# The manifest of required tests
# ---------------------------------------------------------------------------
# A YAML file in a small subset, so that it needs no YAML library and every YAML reader gives the same:
#
#     billing:                  a group, at the start of a line
#       paths:                  the paths it applies to
#         - "src/billing/**"    one item per line, plain or in quotes
#       tests:                  the tests a change of those paths must pass
#         - tests/test_invoice.py
#         - tests/test_billing.py::test_total
#
# Blank lines and lines that start with `#` are skipped. Anything else refuses the whole file.

_GROUP = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):$")
_KEY = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_PLAIN = re.compile(r"^[A-Za-z0-9./_]")
_YAML_WORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "y", "n"})
MANIFEST_KEYS = ("paths", "tests")


def _item(text: str) -> tuple[str, str]:
    """(the value of a list item, why it is refused or "")."""
    if text[:1] in "\"'":
        quote = text[0]
        if len(text) < 2 or not text.endswith(quote) or quote in text[1:-1]:
            return "", "a quote is not closed, or the value holds its own quote"
        if "\\" in text:
            return "", "no backslash in a quoted value"
        return (text[1:-1], "") if text[1:-1] else ("", "an empty value")
    if " #" in text:
        return "", "a comment must be on a line of its own"
    if not _PLAIN.match(text) or ": " in text or text.endswith(":") or text.lower() in _YAML_WORDS or not text.strip("0123456789._"):
        return "", "quote this value (a plain value starts with a letter, a digit, `.`, `/` or `_` and is not a YAML word or number)"
    return text, ""


def parse_manifest(text: str) -> tuple[tuple[Group, ...], tuple[str, ...]]:
    """(the groups of a manifest of required tests, the errors). A manifest with an error gives no group:
    what a change must pass is then not known, which is never read as "nothing is required"."""
    errors: list[str] = []
    order: list[str] = []
    lines_of: dict[str, int] = {}
    lists: dict[str, dict[str, list[str]]] = {}
    group = ""
    key = ""
    key_indent = 0
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip("\r").rstrip()
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(body)
        if body.startswith("\t") or "\t" in line[:indent]:
            errors.append(f"line {number}: a tab in the indentation; use spaces")
            continue
        if indent == 0 and not body.startswith("- "):
            named = _GROUP.match(body)
            if named is None:
                errors.append(f"line {number}: not a group, a key or an item: a group is a name and a colon on its own line")
                group = key = ""
                continue
            group, key = named.group(1), ""
            if group in lists:
                errors.append(f"line {number}: group `{group}` is named twice")
                continue
            order.append(group)
            lines_of[group] = number
            lists[group] = {}
            continue
        if body.startswith("- ") or body == "-":
            if not group or not key or indent < key_indent:
                errors.append(f"line {number}: an item outside `paths` and `tests`")
                continue
            value, problem = _item(body[1:].strip())
            if problem:
                errors.append(f"line {number}: {problem}")
            lists[group][key].append(value)     # a refused item still fills its list: the error above is the one to read
            continue
        found = _KEY.match(body)
        if found is None:
            errors.append(f"line {number}: not a group, a key or an item")
            continue
        if not group:
            errors.append(f"line {number}: a key outside a group")
            continue
        name, rest = found.group(1), found.group(2).strip()
        key = ""
        if name not in MANIFEST_KEYS:
            errors.append(f"line {number}: unknown key `{name}`: a group has `paths` and `tests`")
        elif rest:
            errors.append(f"line {number}: write the items of `{name}` on the lines below, one `- item` each")
        elif name in lists[group]:
            errors.append(f"line {number}: `{name}` is given twice in group `{group}`")
        else:
            key, key_indent = name, indent
            lists[group][name] = []
    for name in order:
        errors += [f"line {lines_of[name]}: group `{name}` has no {wanted}" for wanted in MANIFEST_KEYS if not lists[name].get(wanted)]
    if errors:
        return (), tuple(errors)
    return tuple(Group(name, tuple(lists[name]["paths"]), tuple(lists[name]["tests"])) for name in order), ()


def _touches(pattern: str, path: str) -> bool:
    """Whether a changed file is one a group applies to: a glob as the `paths` input takes it (`*` crosses
    folders), a folder ending in `/`, or a module (the file without its ending)."""
    path = _path(path)
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(SOURCE_FILE.sub("", path), pattern)


def required_for(groups: tuple[Group, ...], changed: list[str]) -> tuple[Required, ...]:
    """The tests the manifest requires for a change of these files: those of every group one of whose paths
    a changed file matches, each test once, under the first group that names it."""
    found: dict[str, Required] = {}
    for group in groups:
        if any(_touches(pattern, path) for pattern in group.paths for path in changed):
            for test in group.tests:
                found.setdefault(test, Required(group.name, test))
    return tuple(found.values())


# ---------------------------------------------------------------------------
# Where a report comes from
# ---------------------------------------------------------------------------
# A report file proves what it holds, not where it comes from. The job knows a few things: whether git
# tracks the file, when it was last modified, the time and the commit the report states, when the run began
# and which commit was checked. A report that contradicts them is refused: it counts as no evidence.

SIDECAR_KEYS = ("commit", "run-id", "attempt", "command", "sha256")


def sidecar_name(report: str) -> str:
    """The name of the sidecar of a report file."""
    return report + SIDECAR_ENDING


def parse_sidecar(data: bytes) -> tuple[Optional[Sidecar], str]:
    """(the sidecar a test step wrote beside its report, ""), or (None, why it is not one): a JSON object
    with the texts or whole numbers `commit`, `run-id`, `attempt`, `command` and `sha256`."""
    if len(data) > SIDECAR_LIMIT:
        return None, f"larger than {SIDECAR_LIMIT // 1024} KiB"
    try:
        read = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None, "not JSON"
    if not isinstance(read, dict):
        return None, "not a JSON object"
    values = []
    for key in SIDECAR_KEYS:
        if key not in read:
            return None, f"`{key}` is missing"
        if isinstance(read[key], bool) or not isinstance(read[key], (str, int)):
            return None, f"`{key}` is not a text or a whole number"
        values.append(str(read[key]).strip() if key != "command" else str(read[key]))
    return Sidecar(*values), ""


def _same_commit(one: str, other: str) -> bool:
    """Whether two names of a commit agree: equal, or one the abbreviation of the other."""
    one, other = one.lower(), other.lower()
    return bool(one) and bool(other) and (one.startswith(other) or other.startswith(one))


def sidecar_state(digest: str, sidecar: Optional[Sidecar], error: str, origin: Origin) -> tuple[str, tuple[str, ...]]:
    """(none, matches, differs or unreadable; how it differs). A sidecar matches when it holds the SHA-256
    of the report, the commit checked, and the run and the attempt of this run, as far as the job knows
    them."""
    if sidecar is None:
        return ("unreadable", (f"its sidecar could not be read: {error}",)) if error else ("none", ())
    differs = []
    if sidecar.sha256.lower() != digest.lower():
        differs.append("its sidecar holds the SHA-256 of another file")
    if origin.commit and not _same_commit(sidecar.commit, origin.commit):
        differs.append(f"its sidecar names commit {sidecar.commit}, and the commit checked is {origin.commit}")
    if (origin.run_id and sidecar.run_id != origin.run_id) or (origin.attempt and sidecar.attempt != origin.attempt):
        differs.append(f"its sidecar names run {sidecar.run_id} attempt {sidecar.attempt}, and this is run {origin.run_id} attempt {origin.attempt}")
    return ("differs", tuple(differs)) if differs else ("matches", ())


def _moments(text: str, utc_offset: int) -> tuple[float, ...]:
    """The moments a timestamp may mean, in seconds since 1970 UTC: one when it names its zone; without a
    zone both readings, UTC and the local time of the machine. Empty when it is not a time."""
    try:
        read = datetime.datetime.fromisoformat(text.strip())
    except ValueError:
        return ()
    if read.tzinfo is not None:
        return (read.timestamp(),)
    as_utc = read.replace(tzinfo=datetime.timezone.utc).timestamp()
    return (as_utc, as_utc - utc_offset)


def earliest(timestamps: tuple[str, ...], utc_offset: int = 0) -> str:
    """The timestamp that is earliest in its latest reading, as written; the first one when none is a time."""
    timed = [(max(_moments(text, utc_offset)), text) for text in timestamps if _moments(text, utc_offset)]
    return min(timed)[1] if timed else (timestamps[0] if timestamps else "")


def refusals(stated: Stated, modified: str, tracked: str, digest: str, sidecar: Optional[Sidecar], sidecar_error: str,
             origin: Origin) -> tuple[str, ...]:
    """Why a report does not count as evidence; empty when it does.

    It is a file git tracks (a report of this run is written by the test step, not committed). Its sidecar
    differs or cannot be read, or it has none where one is required. Its modification time, or the timestamp
    of one of its suites, is before the start of the run: a sidecar that matches and names this run stands
    in for that comparison (a report of an earlier job of the same run is older than this job). It names a
    commit other than the commit checked."""
    reasons = []
    if tracked == TRACKED:
        reasons.append("it is a file tracked by git")
    state, differs = sidecar_state(digest, sidecar, sidecar_error, origin)
    reasons += differs
    if state == "none" and origin.require_sidecar:
        reasons.append("it has no sidecar (`<report>.run.json`), which require-test-provenance asks for")
    start = _moments(origin.started, 0) if origin.started else ()
    if start and not (state == "matches" and origin.run_id):
        limit = start[0] - origin.tolerance
        then = _moments(modified, 0)
        if then and then[0] < limit:
            reasons.append(f"it was last modified {modified}, before the start of the run {origin.started}")
        old = [text for text in stated.timestamps if _moments(text, origin.utc_offset) and max(_moments(text, origin.utc_offset)) < limit]
        if old:
            reasons.append(f"its timestamp {earliest(tuple(old), origin.utc_offset)} is before the start of the run {origin.started}")
    if origin.commit:
        reasons += [f"it names commit {commit}, and the commit checked is {origin.commit}" for commit in stated.commits
                    if not _same_commit(commit, origin.commit)]
    return tuple(reasons)

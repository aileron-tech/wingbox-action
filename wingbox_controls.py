"""A control mapping: which evidence of a run supports which control of a team, and whether a record holds it.

A team keeps its controls in its own words: an id, a title, the references of the frameworks the control
answers, who approved the mapping and until when, the scope, the narrow claim the evidence supports and its
limits, the method, and the evidence required. This module reads that file and evaluates it against one
evidence record. Each control gets one state of four:

    supported      the mapping is approved and in force, the run covered the scope, and every piece of the
                   required evidence is in the record and holds
    not-supported  the mapping supports no claim (a draft, an approval that is incomplete, not yet valid or
                   expired, a mapping that arrives with the change it would judge), or a piece of the required
                   evidence is in the record and fails
    incomplete     the mapping is in force and a piece of the required evidence is not in the record, is
                   unknown or does not apply, or the run left out a part of the scope that the required
                   evidence rests on
    not-evaluated  the method is one the tool does not evaluate: the record of a person is needed

A state says what one record supports for the narrow claim of one control. It never says that a control
conforms or operates, and nothing is concluded for a framework from a count of states. A file that exists, an
edge of the graph, a test name or a command that passed is not read as evidence here: only the verdicts of
the record and the recorded cases of the required tests are. "Not applicable" is never inferred: a verdict
the run calls NOT_APPLICABLE leaves the control incomplete.

Pure: text and values in, values out. No file, no clock, no git, no network; the date validity is judged by
is given by the caller and written into the result. Standard library only, and no import of another module of
this repository, so the module can be lifted out as it is.
"""
from __future__ import annotations

import datetime
import fnmatch
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

FORMAT = "wingbox-controls/1"
CONTROLS_PATH = ".wingbox/controls.yaml"     # the control mapping, from the root of the repository
SUPPORTED, NOT_SUPPORTED, INCOMPLETE, NOT_EVALUATED = "supported", "not-supported", "incomplete", "not-evaluated"
STATES = (SUPPORTED, NOT_SUPPORTED, INCOMPLETE, NOT_EVALUATED)
APPROVED, DRAFT = "approved", "draft"
STATUSES = (APPROVED, DRAFT)
# The typed evidence methods. The first three are evaluated from a record; the others need a record the tool does not make.
EVALUATED_METHODS = ("static-analysis", "executed-test", "configuration-check")
PERSON_METHODS = ("document-review", "human-judgement")
OPERATIONAL_METHOD = "operational-record"
METHODS = (*EVALUATED_METHODS, *PERSON_METHODS, OPERATIONAL_METHOD)
# The verdicts of a record a control may require, each with the field it is read from.
VERDICT_FIELDS = {
    "boundaries-introduced": "boundaries.introduced.verdict",
    "boundaries-existing": "boundaries.existing.verdict",
    "baseline": "baseline.verdict",
    "tests": "tests.evidence",
    "coverage": "coverage.complete",
    "policy-unchanged": "policy.changed",
}
VERDICTS = tuple(VERDICT_FIELDS)
# Per verdict: the words of the record that hold, and the words that fail. Any other word is unknown.
_HOLDS = {"boundaries-introduced": ("PASS",), "boundaries-existing": ("NONE",), "baseline": ("NONE", "ACCEPTED"), "tests": ("complete",),
          "coverage": ("complete",), "policy-unchanged": ("unchanged",)}
_FAILS = {"boundaries-introduced": ("FAIL",), "boundaries-existing": ("PRESENT",), "baseline": ("EXPIRED",), "tests": ("failed",),
          "coverage": (), "policy-unchanged": ("changed",)}
# The evidence of the kind each evaluated method names: a control of that method requires at least one of them.
_OF_METHOD = {"static-analysis": ("boundaries-introduced", "boundaries-existing", "baseline"), "executed-test": ("tests",),
              "configuration-check": ("policy-unchanged",)}
# The verdicts that rest on the graph of the run, and those among them that rest on the boundary rules of a part.
GRAPH_VERDICTS = ("boundaries-introduced", "boundaries-existing", "baseline", "tests", "coverage")
RULE_VERDICTS = ("boundaries-introduced", "boundaries-existing", "baseline")
HOLDS, FAILS, MISSING = "holds", "fails", "missing"
# The reason codes, each with the state it gives a control. The order is the order they are listed in.
REASONS = {
    "mapping-not-in-base": NOT_SUPPORTED,
    "mapping-draft": NOT_SUPPORTED,
    "approval-incomplete": NOT_SUPPORTED,
    "approval-not-yet-valid": NOT_SUPPORTED,
    "approval-expired": NOT_SUPPORTED,
    "person-record-needed": NOT_EVALUATED,
    "operational-record-needed": NOT_EVALUATED,
    "verdict-failed": NOT_SUPPORTED,
    "required-test-failed": NOT_SUPPORTED,
    "test-step-failed": NOT_SUPPORTED,
    "required-evidence-empty": INCOMPLETE,
    "method-evidence-missing": INCOMPLETE,
    "scope-not-covered": INCOMPLETE,
    "verdict-unknown": INCOMPLETE,
    "verdict-not-applicable": INCOMPLETE,
    "required-tests-absent": INCOMPLETE,
    "required-test-not-passed": INCOMPLETE,
    "required-tests-manifest-unreadable": INCOMPLETE,
    "test-step-incomplete": INCOMPLETE,
}
_RANK = {NOT_SUPPORTED: 0, NOT_EVALUATED: 1, INCOMPLETE: 2}    # the state of a control with reasons of several states: the first of these
STATEMENT = ("A state says what this record supports for the narrow claim of a control. "
             "It does not state that a control conforms or operates effectively.")


@dataclass(frozen=True)
class Reference:
    """What a control answers in a framework, in the team's own words: the tool does not hold or read the framework."""
    framework: str
    edition: str
    reference: str


@dataclass(frozen=True)
class Control:
    """One control of the mapping, as the file states it."""
    id: str
    title: str
    status: str                             # APPROVED or DRAFT
    method: str                             # one of METHODS
    claim: str                              # the narrow claim the evidence supports
    limits: str                             # what the evidence does not show
    references: tuple[Reference, ...]
    paths: tuple[str, ...] = ()             # the scope as paths: globs, folders ending in `/`, or modules
    parts: tuple[str, ...] = ()             # the scope as parts of the repository, as Wingbox names them
    verdicts: tuple[str, ...] = ()          # the verdicts of a record that must hold (VERDICTS)
    tests: tuple[str, ...] = ()             # the groups of the manifest of required tests that must have passed
    approver: str = ""
    approval: str = ""                      # the reference of the approval
    valid_from: str = ""                    # YYYY-MM-DD; the first day the approval is in force
    valid_until: str = ""                   # YYYY-MM-DD; the last day it is
    risk: str = ""                          # the reference of the related risk
    line: int = 0                           # where the control starts in the file


@dataclass(frozen=True)
class ControlMap:
    """A control mapping as it was read."""
    path: str                 # from the root of the repository, or as given by hand
    source: str               # "base": the base commit holds it; "head": only the checked files do; "given": named by hand; "none": not found
    commit: str               # the base commit it was read from; "" otherwise
    sha256: str               # of its bytes; "" when not found
    name: str = ""
    version: str = ""
    owner: str = ""
    controls: tuple[Control, ...] = ()
    errors: tuple[str, ...] = ()   # why it gives no controls: it is refused as a whole


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------
# A YAML file in a small subset, read line by line like the manifest of required tests, so that it needs no
# YAML library and every YAML reader gives the same. Two spaces per level, no tab:
#
#     format: wingbox-controls/1
#     mapping:
#       name: "Example team control map"
#       version: "2026.1"
#       owner: "Engineering lead"
#     controls:
#       SDLC-03:                              the team's own id of a control
#         title: "Changes are checked against the architecture rules"
#         status: approved                    approved or draft
#         approver: "Head of engineering"
#         approval: "CAB-2026-014"
#         valid-from: "2026-01-01"
#         valid-until: "2026-12-31"
#         risk: "RISK-07"
#         method: static-analysis             one of METHODS
#         claim: "..."
#         limits: "..."
#         references:                         one or more, each with these three keys
#           - framework: "Team security policy"
#             edition: "v3"
#             reference: "SEC-4.2"
#         scope-paths:                        paths, parts, or both
#           - "src/**"
#         scope-parts:
#           - backend
#         requires-verdicts:                  names of VERDICTS
#           - boundaries-introduced
#         requires-tests:                     groups of the manifest of required tests
#           - billing
#
# A value is on the line of its key, plain or in quotes; the items of a list are on the lines below, one
# `- item` each. Blank lines and lines that start with `#` are skipped. Anything else refuses the whole file.

TOP_KEYS = ("format", "mapping", "controls")
MAPPING_KEYS = ("name", "version", "owner")
SCALAR_KEYS = ("title", "status", "approver", "approval", "valid-from", "valid-until", "risk", "method", "claim", "limits")
LIST_KEYS = ("references", "scope-paths", "scope-parts", "requires-verdicts", "requires-tests")
REFERENCE_KEYS = ("framework", "edition", "reference")
REQUIRED_KEYS = ("title", "status", "method", "claim", "limits")
_ID = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):$")
_KEY = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_PLAIN = re.compile(r"^[A-Za-z./_]")
_YAML_WORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "y", "n"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_INDENT = 2


def _value(text: str) -> tuple[str, str]:
    """(a value, why it is refused or ""). The rules of an item of the manifest of required tests, and one
    more: a plain value does not start with a digit, so that a date or a version is always written in quotes
    and no YAML reader takes it for a date or a number."""
    if text[:1] in "\"'":
        quote = text[0]
        if len(text) < 2 or not text.endswith(quote) or quote in text[1:-1]:
            return "", "a quote is not closed, or the value holds its own quote"
        if "\\" in text:
            return "", "no backslash in a quoted value"
        return (text[1:-1], "") if text[1:-1].strip() else ("", "an empty value")
    if " #" in text:
        return "", "a comment must be on a line of its own"
    if not _PLAIN.match(text) or ": " in text or text.endswith(":") or text.lower() in _YAML_WORDS or not text.strip("0123456789._"):
        return "", "quote this value (a plain value starts with a letter, `.`, `/` or `_` and is not a YAML word or number)"
    return text, ""


def _date(text: str) -> Optional[datetime.date]:
    """The day a text names as YYYY-MM-DD; None when it is not a day of the calendar."""
    if not _DATE.match(text):
        return None
    try:
        return datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return None


class _Reader:
    """The state of one reading of the file: what was found so far, and where the next line may belong."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.seen: set[str] = set()                     # the keys of level 0 that were given
        self.format = ""
        self.mapping: dict[str, str] = {}
        self.order: list[str] = []                      # the control ids, in the order written
        self.lines_of: dict[str, int] = {}
        self.scalars: dict[str, dict[str, str]] = {}
        self.lists: dict[str, dict[str, list[Any]]] = {}
        self.section = ""                               # "mapping" or "controls": the open key of level 0
        self.control = ""                               # the open control
        self.key = ""                                   # the open list of the control
        self.reference: Optional[dict[str, str]] = None   # the open item of `references`

    def error(self, number: int, text: str) -> None:
        self.errors.append(f"line {number}: {text}")

    def top(self, number: int, body: str) -> None:
        self.section = self.control = self.key = ""
        self.reference = None
        found = _KEY.match(body)
        if found is None or body.startswith("- "):
            self.error(number, "not a key: at the start of a line stands `format: wingbox-controls/1`, `mapping:` or `controls:`")
            return
        name, rest = found.group(1), found.group(2).strip()
        if name not in TOP_KEYS:
            self.error(number, f"unknown key `{name}`: at the start of a line stands `format`, `mapping` or `controls`")
        elif name in self.seen:
            self.error(number, f"`{name}` is given twice")
        elif name == "format":
            self.seen.add(name)
            value, problem = _value(rest) if rest else ("", "write the value of `format` on its line")
            if problem:
                self.error(number, problem)
            elif value != FORMAT:
                self.error(number, f"format `{value}` is not read: this reader knows `{FORMAT}`")
            self.format = value
        elif rest:
            self.error(number, f"write what `{name}` holds on the lines below")
        else:
            self.seen.add(name)
            self.section = name

    def second(self, number: int, body: str) -> None:
        self.control = self.key = ""
        self.reference = None
        if self.section == "mapping":
            found = _KEY.match(body)
            if found is None or body.startswith("- ") or found.group(1) not in MAPPING_KEYS:
                self.error(number, "not a key of `mapping`: it has `name`, `version` and `owner`")
            elif found.group(1) in self.mapping:
                self.error(number, f"`{found.group(1)}` is given twice in `mapping`")
            else:
                self.mapping[found.group(1)] = self.scalar(number, found.group(1), found.group(2).strip())
        elif self.section == "controls":
            named = _ID.match(body)
            if named is None or named.group(1).lower() in _YAML_WORDS or not named.group(1).strip("0123456789._"):
                self.error(number, "not a control: a control is its id and a colon on its own line (letters, digits, `.`, `_`, `-`; not a number)")
            elif named.group(1) in self.scalars:
                self.error(number, f"control `{named.group(1)}` is named twice")
            else:
                self.control = named.group(1)
                self.order.append(self.control)
                self.lines_of[self.control] = number
                self.scalars[self.control] = {}
                self.lists[self.control] = {}
        else:
            self.error(number, "an indented line outside `mapping` and `controls`")

    def scalar(self, number: int, name: str, rest: str) -> str:
        if not rest:
            self.error(number, f"write the value of `{name}` on its line")
            return ""
        value, problem = _value(rest)
        if problem:
            self.error(number, problem)
        return value

    def third(self, number: int, body: str) -> None:
        self.key = ""
        self.reference = None
        if not self.control:
            self.error(number, "a key outside a control")
            return
        found = _KEY.match(body)
        if found is None or body.startswith("- "):
            self.error(number, "not a key of a control")
            return
        name, rest = found.group(1), found.group(2).strip()
        if name not in SCALAR_KEYS and name not in LIST_KEYS:
            self.error(number, f"unknown key `{name}` in control `{self.control}`")
        elif name in self.scalars[self.control] or name in self.lists[self.control]:
            self.error(number, f"`{name}` is given twice in control `{self.control}`")
        elif name in SCALAR_KEYS:
            self.scalars[self.control][name] = self.scalar(number, name, rest)
        elif rest:
            self.error(number, f"write the items of `{name}` on the lines below, one `- item` each")
        else:
            self.key = name
            self.lists[self.control][name] = []

    def item(self, number: int, body: str) -> None:
        self.reference = None
        if not self.key or not (body.startswith("- ") or body == "-"):
            self.error(number, "not an item: under a list stands one `- item` per line")
            return
        text = body[1:].strip()
        if self.key != "references":
            value, problem = _value(text)
            if problem:
                self.error(number, problem)
            self.lists[self.control][self.key].append(value)     # a refused item still fills its list: the error above is the one to read
            return
        self.reference = {}
        self.lists[self.control][self.key].append(self.reference)
        self.pair(number, text)

    def pair(self, number: int, body: str) -> None:
        found = _KEY.match(body)
        if self.reference is None or found is None or found.group(1) not in REFERENCE_KEYS:
            self.error(number, "not a key of a reference: it has `framework`, `edition` and `reference`")
        elif found.group(1) in self.reference:
            self.error(number, f"`{found.group(1)}` is given twice in one reference")
        else:
            self.reference[found.group(1)] = self.scalar(number, found.group(1), found.group(2).strip())


def _control(reader: _Reader, name: str) -> Optional[Control]:
    """The control of that id from what was read; None, with the errors added, when it lacks something."""
    number, scalars, lists = reader.lines_of[name], reader.scalars[name], reader.lists[name]
    before = len(reader.errors)
    for wanted in REQUIRED_KEYS:
        if not scalars.get(wanted):
            reader.error(number, f"control `{name}` has no {wanted}")
    if scalars.get("status") and scalars["status"] not in STATUSES:
        reader.error(number, f"control `{name}`: status `{scalars['status']}` is not one of {', '.join(STATUSES)}")
    if scalars.get("method") and scalars["method"] not in METHODS:
        reader.error(number, f"control `{name}`: method `{scalars['method']}` is not one of {', '.join(METHODS)}")
    days = {}
    for key in ("valid-from", "valid-until"):
        if scalars.get(key):
            days[key] = _date(scalars[key])
            if days[key] is None:
                reader.error(number, f"control `{name}`: {key} `{scalars[key]}` is not a day as 2026-01-31")
    if days.get("valid-from") and days.get("valid-until") and days["valid-from"] > days["valid-until"]:
        reader.error(number, f"control `{name}`: valid-from is after valid-until")
    references = []
    for found in lists.get("references", ()):
        lacking = [key for key in REFERENCE_KEYS if not found.get(key)]
        if lacking:
            reader.error(number, f"control `{name}`: a reference has no {' and no '.join(lacking)}")
        else:
            references.append(Reference(found["framework"], found["edition"], found["reference"]))
    if not lists.get("references"):
        reader.error(number, f"control `{name}` has no references")
    if not lists.get("scope-paths") and not lists.get("scope-parts"):
        reader.error(number, f"control `{name}` has no scope: give `scope-paths`, `scope-parts` or both")
    for key in LIST_KEYS:
        items = [item for item in lists.get(key, ()) if isinstance(item, str)]
        repeated = sorted({item for item in items if items.count(item) > 1})
        if repeated:
            reader.error(number, f"control `{name}`: `{repeated[0]}` is given twice in `{key}`")
    for verdict in lists.get("requires-verdicts", ()):
        if verdict and verdict not in VERDICTS:
            reader.error(number, f"control `{name}`: verdict `{verdict}` is not one of {', '.join(VERDICTS)}")
    if len(reader.errors) > before:
        return None
    return Control(name, scalars["title"], scalars["status"], scalars["method"], scalars["claim"], scalars["limits"], tuple(references),
                   tuple(lists.get("scope-paths", ())), tuple(lists.get("scope-parts", ())), tuple(lists.get("requires-verdicts", ())),
                   tuple(lists.get("requires-tests", ())), scalars.get("approver", ""), scalars.get("approval", ""),
                   scalars.get("valid-from", ""), scalars.get("valid-until", ""), scalars.get("risk", ""), number)


def parse(text: str) -> tuple[tuple[str, str, str], tuple[Control, ...], tuple[str, ...]]:
    """((name, version, owner) of the mapping, its controls, the errors). A file with an error gives no
    control: which evidence a control requires is then not known, which is never read as "nothing is mapped"."""
    reader = _Reader()
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip("\r").rstrip()
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(body)
        if body.startswith("\t") or "\t" in line[:indent]:
            reader.error(number, "a tab in the indentation; use spaces")
            continue
        if indent % _INDENT or indent > 4 * _INDENT:
            reader.error(number, f"indented by {indent} spaces: a level is {_INDENT} spaces, and the deepest line has {4 * _INDENT}")
            continue
        level = indent // _INDENT
        if level == 0:
            reader.top(number, body)
        elif level == 1:
            reader.second(number, body)
        elif level == 2:
            reader.third(number, body)
        elif level == 3:
            reader.item(number, body)
        else:
            reader.pair(number, body)
    if "format" not in reader.seen:
        reader.error(1, f"no `format: {FORMAT}` line")
    if "mapping" not in reader.seen:
        reader.error(1, "no `mapping:` with `name`, `version` and `owner`")
    else:
        for wanted in MAPPING_KEYS:
            if not reader.mapping.get(wanted) and not any(f"`{wanted}`" in error for error in reader.errors):
                reader.error(1, f"`mapping` has no {wanted}")
    if "controls" not in reader.seen:
        reader.error(1, "no `controls:`")
    controls = [_control(reader, name) for name in reader.order]
    if reader.errors:
        return ("", "", ""), (), tuple(reader.errors)
    return ((reader.mapping["name"], reader.mapping["version"], reader.mapping["owner"]),
            tuple(control for control in controls if control is not None), ())


def read(data: bytes, path: str, source: str, commit: str = "") -> ControlMap:
    """The mapping of the bytes of a file: its digest, and its controls or the errors that refuse it."""
    digest = hashlib.sha256(data).hexdigest()
    try:
        (name, version, owner), controls, errors = parse(data.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        return ControlMap(path, source, commit, digest, errors=(f"not UTF-8 text: {error.reason}",))
    return ControlMap(path, source, commit, digest, name, version, owner, controls, errors)


# ---------------------------------------------------------------------------
# The evidence of a record
# ---------------------------------------------------------------------------

def _at(record: Mapping[str, Any], dotted: str) -> Any:
    """The value at a dotted path of the record; None when a step is not there."""
    value: Any = record
    for step in dotted.split("."):
        if not isinstance(value, Mapping) or step not in value:
            return None
        value = value[step]
    return value


def verdict(record: Mapping[str, Any], name: str) -> tuple[str, str]:
    """(the word the record has for a verdict, HOLDS, FAILS or MISSING). The word is "" when the record has
    no such field. `coverage` is read as complete or incomplete, `policy-unchanged` as unchanged or changed."""
    value = _at(record, VERDICT_FIELDS[name])
    if name == "coverage":
        word = "" if not isinstance(value, bool) else "complete" if value else "incomplete"
    elif name == "policy-unchanged":
        word = "" if not isinstance(value, list) else "changed" if value else "unchanged"
    else:
        word = value if isinstance(value, str) else ""
    return word, HOLDS if word in _HOLDS[name] else FAILS if word in _FAILS[name] else MISSING


def required_groups(record: Mapping[str, Any]) -> tuple[str, ...]:
    """The groups of required tests the record holds a test of, in its order."""
    tests = _at(record, "tests.required")
    return tuple(dict.fromkeys(item.get("group", "") for item in tests if isinstance(item, Mapping))) if isinstance(tests, list) else ()


def _group(record: Mapping[str, Any], name: str) -> tuple[str, str, str, str]:
    """(what the record holds about a group of required tests in words, HOLDS, FAILS or MISSING, the reason
    code or "", its detail). A group holds only when the record has a test of it and every one passed."""
    outcome = _at(record, "tests.step-outcome")
    if outcome == "failure":
        return "test step: failure", FAILS, "test-step-failed", f"`{name}`: the test step of the job ended with failure"
    if outcome in ("cancelled", "skipped"):
        return f"test step: {outcome}", MISSING, "test-step-incomplete", f"`{name}`: the test step of the job ended with {outcome}"
    if _at(record, "tests.manifest.errors"):
        return "manifest unreadable", MISSING, "required-tests-manifest-unreadable", f"`{name}`: the manifest of required tests could not be read"
    listed = _at(record, "tests.required")
    tests = [item for item in listed if isinstance(item, Mapping) and item.get("group") == name] if isinstance(listed, list) else []
    if not tests:
        return "no test of this group", MISSING, "required-tests-absent", (
            f"`{name}`: the record holds no required test of this group (the change touches none of its paths, the manifest has no such group, "
            "or an earlier group names its tests)")
    failed = [str(item.get("test", "")) for item in tests if item.get("status") == "failed"]
    lacking = [f"{item.get('test', '')} ({item.get('status', '')})" for item in tests if item.get("status") not in ("passed", "failed")]
    passed = len(tests) - len(failed) - len(lacking)
    said = f"{passed} of {len(tests)} passed"
    if failed:
        return said, FAILS, "required-test-failed", f"`{name}`: {', '.join(failed)}"
    if lacking:
        return said, MISSING, "required-test-not-passed", f"`{name}`: {', '.join(lacking)}"
    return said, HOLDS, "", ""


def _fixed(pattern: str) -> str:
    """The part of a path pattern before its first wildcard."""
    return re.split(r"[*?\[]", pattern, maxsplit=1)[0]


def _may_lie_in(pattern: str, part: str) -> bool:
    """Whether a path a pattern matches may lie in a part of the repository (a folder; `.` is the root, `-` is
    the files outside every part). Only two fixed beginnings that part ways show that it cannot."""
    if part in (".", "-", ""):
        return True
    fixed, folder = _fixed(pattern), part.rstrip("/") + "/"
    return (fixed + "/").startswith(folder) or folder.startswith(fixed)


def _matches(pattern: str, path: str) -> bool:
    """Whether a file is one a scope path names: a glob (`*` crosses folders), a folder ending in `/`, or a
    module (the file without its ending)."""
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path, pattern)


def scope_gaps(control: Control, record: Mapping[str, Any]) -> tuple[str, ...]:
    """What the run left out that lies, or may lie, in the scope of a control and that the evidence the control
    requires rests on; empty when the run covered the scope.

    For every control: a part the scope names must be a part the run names, and the files analysed must be the
    commit named (a tree that is not is a gap, and so is a gap the reader does not know). For a control that
    requires a verdict resting on the graph (GRAPH_VERDICTS): a language left out, a graph that is stale or
    unconfirmed, a tool error, and a changed file the paths filter left out that a scope path matches or that
    lies in a part the scope names. For a control that requires a verdict of the boundary rules
    (RULE_VERDICTS): a part that was not checked, when the scope names it or a scope path may lie in it. The
    recorded cases of a group of required tests rest on neither: the manifest names them by path."""
    coverage = record.get("coverage")
    if not isinstance(coverage, Mapping):
        return ("the record holds no coverage account",)
    on_graph, on_rules = bool(set(control.verdicts) & set(GRAPH_VERDICTS)), bool(set(control.verdicts) & set(RULE_VERDICTS))
    parts = [part for part in coverage.get("parts", ()) if isinstance(part, Mapping)]
    named = {str(part.get("part", "")): part for part in parts}
    gaps = [f"part `{name}` is not a part the run names" for name in control.parts if name not in named]
    if on_rules:
        gaps += [f"part `{name}` was not checked ({named[name].get('reason', '')})" for name in control.parts
                 if name in named and not named[name].get("checked")]
    if coverage.get("complete") is True:
        return tuple(gaps)
    reasons = [str(reason) for reason in coverage.get("reasons", ())]
    for reason in reasons:
        if reason.startswith(("not-checked: ", "paths-filter: ")):
            continue        # read below from the parts and the files the record lists
        if reason.startswith("parts: "):
            gaps += [reason] if on_rules else []
        elif reason.startswith(("tool-error: ", "skipped-language: ", "graph: ")):
            gaps += [reason] if on_graph else []
        else:
            gaps.append(reason)
    for part in parts if on_rules else ():
        name = str(part.get("part", ""))
        if not part.get("checked") and name not in control.parts and any(_may_lie_in(pattern, name) for pattern in control.paths):
            gaps.append(f"part `{name}` was not checked ({part.get('reason', '')})")
    left_out = _at(coverage, "excluded.paths.changed-files")
    for path in left_out if on_graph and isinstance(left_out, list) else ():
        text = str(path)
        if any(_matches(pattern, text) for pattern in control.paths) or any(
                name in (".", "-") or text.startswith(name.rstrip("/") + "/") for name in control.parts):
            gaps.append(f"changed file `{text}` was left out by the paths filter")
    if not reasons:
        gaps.append("the coverage is not complete and the record gives no reason")
    return tuple(dict.fromkeys(gaps))


# ---------------------------------------------------------------------------
# The state of a control
# ---------------------------------------------------------------------------

def _mapping_reasons(control: Control, source: str, day: datetime.date) -> list[tuple[str, str]]:
    """Why the mapping of a control supports no claim, whatever the record holds."""
    reasons = []
    if source == "head":
        reasons.append(("mapping-not-in-base", "the base commit holds no mapping: this one arrives with the change it would judge"))
    if control.status == DRAFT:
        reasons.append(("mapping-draft", "status is draft"))
        return reasons
    lacking = [key for key, value in (("approver", control.approver), ("approval", control.approval), ("valid-from", control.valid_from),
                                      ("valid-until", control.valid_until)) if not value]
    if lacking:
        reasons.append(("approval-incomplete", f"approved without {', '.join(lacking)}"))
    first, last = _date(control.valid_from), _date(control.valid_until)
    if first is not None and day < first:
        reasons.append(("approval-not-yet-valid", f"valid from {control.valid_from}, evaluated at {day.isoformat()}"))
    if last is not None and day > last:
        reasons.append(("approval-expired", f"valid until {control.valid_until}, evaluated at {day.isoformat()}"))
    return reasons


def _evidence(control: Control, record: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """(each piece of evidence the control requires with what the record holds, the reasons they give)."""
    items, reasons = [], []
    for name in control.verdicts:
        word, result = verdict(record, name)
        items.append({"kind": "verdict", "name": name, "field": VERDICT_FIELDS[name], "value": word, "result": result})
        if result == FAILS:
            reasons.append(("verdict-failed", f"{name} is {word}"))
        elif result == MISSING:
            code = "verdict-not-applicable" if word == "NOT_APPLICABLE" else "verdict-unknown"
            reasons.append((code, f"{name} is {word}" if word else f"{name}: the record has no {VERDICT_FIELDS[name]}"))
    for name in control.tests:
        said, result, code, detail = _group(record, name)
        items.append({"kind": "required-tests", "name": name, "field": "tests.required", "value": said, "result": result})
        if code:
            reasons.append((code, detail))
    return items, reasons


def state_of(reasons: Sequence[tuple[str, str]]) -> str:
    """The state the reasons give a control: supported without a reason, else the first of not-supported,
    not-evaluated and incomplete that one of them gives."""
    states = {REASONS[code] for code, _detail in reasons}
    return min(states, key=lambda state: _RANK[state]) if states else SUPPORTED


def evaluate_control(control: Control, record: Mapping[str, Any], source: str, day: datetime.date) -> dict[str, Any]:
    """One control with its state, the reasons and the evidence it names, as the record writes it."""
    reasons = _mapping_reasons(control, source, day)
    items: list[dict[str, str]] = []
    if control.method in PERSON_METHODS:
        reasons.append(("person-record-needed", f"method {control.method}: the tool does not evaluate it; the record of a person is needed"))
    elif control.method == OPERATIONAL_METHOD:
        reasons.append(("operational-record-needed", "method operational-record: the tool does not evaluate it from one run; "
                                                     "a record of operation over a period is needed"))
    else:
        items, found = _evidence(control, record)
        if not control.verdicts and not control.tests:
            reasons.append(("required-evidence-empty", "the control names no verdict and no group of required tests: nothing could support it"))
        elif not (set(control.verdicts) & set(_OF_METHOD[control.method])) and not (control.method == "executed-test" and control.tests):
            wanted = ", ".join(_OF_METHOD[control.method]) + (" or a group of required tests" if control.method == "executed-test" else "")
            reasons.append(("method-evidence-missing", f"method {control.method} and no evidence of its kind is required ({wanted})"))
        gaps = scope_gaps(control, record)
        if gaps:
            reasons.append(("scope-not-covered", "; ".join(gaps)))
        reasons += found
    reasons = list(dict.fromkeys(reasons))
    return {
        "id": control.id, "title": control.title, "status": control.status, "method": control.method,
        "approver": control.approver, "approval": control.approval, "valid-from": control.valid_from, "valid-until": control.valid_until,
        "risk": control.risk, "claim": control.claim, "limits": control.limits,
        "references": [{"framework": found.framework, "edition": found.edition, "reference": found.reference} for found in control.references],
        "scope": {"paths": list(control.paths), "parts": list(control.parts)},
        "requires": {"verdicts": list(control.verdicts), "tests": list(control.tests)},
        "state": state_of(reasons),
        "reasons": [{"code": code, "detail": detail} for code, detail in reasons],
        "evidence": items,
    }


def identity(found: Optional[ControlMap], changed: bool = False) -> dict[str, Any]:
    """The identity of a mapping as the record writes it; the one of no mapping when found is None."""
    if found is None:
        return {"path": "", "source": "none", "commit": "", "sha256": "", "format": "", "name": "", "version": "", "owner": "",
                "errors": [], "changed": False}
    return {"path": found.path, "source": found.source, "commit": found.commit, "sha256": found.sha256,
            "format": "" if found.errors else FORMAT, "name": found.name, "version": found.version, "owner": found.owner,
            "errors": list(found.errors), "changed": changed}


def evaluate(found: Optional[ControlMap], record: Mapping[str, Any], date: str) -> dict[str, Any]:
    """The `controls` object of an evidence record: the mapping that was read, the day validity was judged at
    (YYYY-MM-DD, given by the caller), each control with its state, and the two lists of a trace: the controls
    the record gives no evidence for, and the evidence of the record that no control names.

    found is None when the repository has no mapping. A mapping with errors gives no control. The record is
    read as it is written (schema wingbox-evidence/2); a field it does not have is unknown, never a pass.
    ValueError when date is not a day."""
    changed = found is not None and any(isinstance(change, Mapping) and change.get("path") == found.path
                                        for change in (_at(record, "policy.changed") or ()))
    day = _date(date)
    if day is None:
        raise ValueError(f"the day of the evaluation must be a day as 2026-01-31, not {date!r}")
    controls = [evaluate_control(control, record, found.source, day) for control in found.controls] if found is not None else []
    named_verdicts = {name for control in controls for name in control["requires"]["verdicts"]}
    named_groups = {name for control in controls for name in control["requires"]["tests"]}
    unmapped: list[dict[str, str]] = []
    if found is not None and not found.errors:
        unmapped += [{"kind": "verdict", "name": name} for name in VERDICTS if name not in named_verdicts]
        unmapped += [{"kind": "required-tests", "name": name} for name in required_groups(record) if name not in named_groups]
    return {
        "mapping": identity(found, changed),
        "evaluated": date,
        "controls": controls,
        "without-evidence": [control["id"] for control in controls if control["state"] in (INCOMPLETE, NOT_EVALUATED)],
        "unmapped-evidence": unmapped,
        "statement": STATEMENT,
    }


def counts(said: Mapping[str, Any]) -> dict[str, int]:
    """How many controls of an evaluation are in each state, by state, always all four."""
    return {state: sum(1 for control in said["controls"] if control["state"] == state) for state in STATES}


def lines(said: Mapping[str, Any]) -> list[str]:
    """An evaluation for a reader: the mapping, each control with its state, reasons and evidence (the forward
    list), then the controls without evidence and the evidence no control names (the reverse list). The last
    line counts the states."""
    mapping = said["mapping"]
    found = [f"mapping: {mapping['path'] or 'none'}" + (f" ({mapping['source']})" if mapping["source"] != "none" else ""),
             *([f"mapping-sha256: {mapping['sha256']}"] if mapping["sha256"] else []),
             *([f"mapping-name: {mapping['name']} version {mapping['version']}, owner {mapping['owner']}"] if mapping["name"] else []),
             *(f"mapping-error: {error}" for error in mapping["errors"]),
             *(["mapping-changed: the change under evaluation edits the mapping; the one evaluated is named above"] if mapping["changed"] else []),
             f"evaluated: {said['evaluated'] or 'unknown'}"]
    for control in said["controls"]:
        found.append(f"control: {control['id']} {control['state']}: {control['title']}")
        found += [f"  reference: {item['framework']} {item['edition']}: {item['reference']}" for item in control["references"]]
        found += [f"  reason: {reason['code']}: {reason['detail']}" for reason in control["reasons"]]
        found += [f"  evidence: {item['kind']} {item['name']} ({item['field']}): {item['value'] or 'not in the record'}: {item['result']}"
                  for item in control["evidence"]]
    found.append("without-evidence: " + (", ".join(said["without-evidence"]) or "none"))
    found.append("unmapped-evidence: " + (", ".join(f"{item['kind']} {item['name']}" for item in said["unmapped-evidence"]) or "none"))
    found.append(f"statement: {said['statement']}")
    number = counts(said)
    found.append("controls: " + ", ".join(f"{number[state]} {state}" for state in STATES))
    return found


def ok(said: Mapping[str, Any]) -> bool:
    """Whether an evaluation found no defect: the mapping was read, and no control is not-supported or
    incomplete. A control that is not-evaluated is no defect of the evidence: it waits for a person."""
    number = counts(said)
    return not said["mapping"]["errors"] and said["mapping"]["source"] != "none" and not number[NOT_SUPPORTED] and not number[INCOMPLETE]

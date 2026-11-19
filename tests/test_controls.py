"""The control mapping: a file that says which evidence supports which control, and its check against a record.

What is expected here is written by hand, not computed by the module under test: the controls of the fixture
mapping as literals, the state and the reason codes of every worked example in one table, the record of the
fixture run as a file with the fields each run changes written out. The fixture is the mapping of an invented
team for an invented repository (tests/fixtures/controls); docs/controls/examples.md walks through the same
examples, and a test keeps the two equal.
"""
import ast
import copy
import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import wingbox_controls as wc  # noqa: E402
import wingbox_evidence as we  # noqa: E402
import wingbox_facts as facts  # noqa: E402
import wingbox_package as pkg  # noqa: E402
import wingbox_policy as policy  # noqa: E402
import wingbox_pr as pr  # noqa: E402
import wingbox_tests as wt  # noqa: E402
from test_coverage import whole  # noqa: E402
from test_evidence_record import RUN, problems  # noqa: E402
from test_package import as_json, commit, members_of, run_cli, run_folder  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "controls"
MAPPING_BYTES = (FIXTURES / "controls.yaml").read_bytes().replace(b"\r\n", b"\n")
MAPPING_TEXT = MAPPING_BYTES.decode("ascii")
RECORD = json.loads((FIXTURES / "record.json").read_text(encoding="ascii"))
RECORD_SCHEMA = json.loads((ROOT / "schema" / "wingbox-evidence-2.schema.json").read_text(encoding="utf-8"))
PACKAGE_SCHEMA = json.loads((ROOT / "schema" / "wingbox-package-1.schema.json").read_text(encoding="utf-8"))
FORMAT_SCHEMA = json.loads((ROOT / "schema" / "wingbox-controls-1.schema.json").read_text(encoding="utf-8"))
BASE_COMMIT = "1" * 40
DAY = "2026-01-02"      # the day of the fixture record
STATES = ("supported", "not-supported", "incomplete", "not-evaluated")
METHODS = ("static-analysis", "executed-test", "configuration-check", "document-review", "human-judgement", "operational-record")
VERDICTS = ("boundaries-introduced", "boundaries-existing", "baseline", "tests", "coverage", "policy-unchanged")
# Every reason code with the state it gives a control, written out.
REASONS = {
    "mapping-not-in-base": "not-supported", "mapping-draft": "not-supported", "approval-incomplete": "not-supported",
    "approval-not-yet-valid": "not-supported", "approval-expired": "not-supported",
    "person-record-needed": "not-evaluated", "operational-record-needed": "not-evaluated",
    "verdict-failed": "not-supported", "required-test-failed": "not-supported", "test-step-failed": "not-supported",
    "required-evidence-empty": "incomplete", "method-evidence-missing": "incomplete", "scope-not-covered": "incomplete",
    "verdict-unknown": "incomplete", "verdict-not-applicable": "incomplete", "required-tests-absent": "incomplete",
    "required-test-not-passed": "incomplete", "required-tests-manifest-unreadable": "incomplete", "test-step-incomplete": "incomplete",
}
STATEMENT = ("A state says what this record supports for the narrow claim of a control. "
             "It does not state that a control conforms or operates effectively.")
# The first control of the fixture mapping and one that names a part, as the file states them.
SDLC_03 = wc.Control(
    "SDLC-03", "Changes are checked against the architecture rules", "approved", "static-analysis",
    "The change introduces no violation of the declared architecture rules, and every part was looked at.",
    "Does not show that the rules are the right ones, nor that a person reviewed the change.",
    (wc.Reference("Lantern security policy", "v3", "SP-7.2 Secure design"), wc.Reference("Customer assurance questionnaire", "2026", "Q-14")),
    ("src/**",), (), ("boundaries-introduced", "coverage"), (), "Head of engineering", "CAB-2026-014", "2026-01-01", "2026-12-31", "RISK-07", 11)
SDLC_13 = wc.Control(
    "SDLC-13", "Web front end changes are checked against the rules of the front end", "approved", "static-analysis",
    "The change introduces no violation of the declared rules of the web part.", "Does not show that the rules of the web part are complete.",
    (wc.Reference("Lantern security policy", "v3", "SP-7.2 Secure design"),),
    (), ("web",), ("boundaries-introduced",), (), "Head of engineering", "CAB-2026-014", "2026-01-01", "2026-12-31", "RISK-07", 207)
# (id, status, method) of every control of the fixture mapping, in the order of the file.
CONTROLS = [("SDLC-03", "approved", "static-analysis"), ("SDLC-04", "approved", "static-analysis"), ("SDLC-05", "approved", "executed-test"),
            ("SDLC-06", "approved", "executed-test"), ("SDLC-07", "approved", "configuration-check"), ("SDLC-08", "draft", "executed-test"),
            ("SDLC-09", "approved", "static-analysis"), ("SDLC-10", "approved", "human-judgement"), ("SDLC-11", "approved", "document-review"),
            ("SDLC-12", "approved", "operational-record"), ("SDLC-13", "approved", "static-analysis"), ("SDLC-14", "approved", "executed-test"),
            ("SDLC-15", "approved", "static-analysis")]

# The runs of the worked examples: the fixture record with the fields that differ, each by its dotted path.
SKIPPED_TAX = {"group": "billing", "test": "tests/test_tax.py", "status": "skipped", "cases": 1,
               "records": [{"where": "tests.test_tax", "name": "test_rate", "status": "skipped"}]}
FAILED_INVOICE = {"group": "billing", "test": "tests/test_invoice.py", "status": "failed", "cases": 2,
                  "records": [{"where": "tests.test_invoice", "name": "test_total", "status": "passed"},
                              {"where": "tests.test_invoice", "name": "test_rounding", "status": "failed"}]}
WEB_NOT_CHECKED = [{"part": ".", "style": "layered", "modules": 9, "checked": True, "reason": ""},
                   {"part": "web", "style": "", "modules": 3, "checked": False, "reason": "declared-rules-not-read"}]
RUNS: dict = {
    "pass": {},
    "test-failed": {"tests.evidence": "failed", "tests.required.0": FAILED_INVOICE, "eligible": False, "eligible-reasons": ["tests: failed"]},
    "baseline-expired": {"baseline.verdict": "EXPIRED", "baseline.expired": 1, "eligible": False, "eligible-reasons": ["baseline: 1 past the end date"]},
    "test-skipped": {"tests.evidence": "incomplete", "tests.required.1": SKIPPED_TAX, "eligible": False, "eligible-reasons": ["tests: incomplete"]},
    "docs-only": {"boundaries.introduced.verdict": "NOT_APPLICABLE", "boundaries.introduced.reasons": ["no-source-module-changed"],
                  "result.boundaries": "NOT_APPLICABLE", "result.reasons": ["no-source-module-changed"],
                  "files": [{"path": "docs/architecture.md", "sha256": "9" * 64}], "tests.evidence": "none-found", "tests.required": [],
                  "tests.to-run": [], "eligible": False, "eligible-reasons": ["tests: none-found"]},
    "web-not-checked": {"boundaries.introduced.verdict": "UNKNOWN", "boundaries.introduced.reasons": ["not-checked: web (declared-rules-not-read)"],
                        "result.boundaries": "UNKNOWN", "result.reasons": ["not-checked: web (declared-rules-not-read)"],
                        "coverage.complete": False, "coverage.reasons": ["not-checked: web (declared-rules-not-read)"],
                        "coverage.parts": WEB_NOT_CHECKED, "eligible": False,
                        "eligible-reasons": ["boundaries-introduced: UNKNOWN", "boundaries-existing: UNKNOWN", "coverage: incomplete"],
                        "boundaries.existing.verdict": "UNKNOWN"},
    "policy-edited": {"policy.changed": [{"path": ".wingbox/config.yaml", "kind": "rules", "change": "changed"}], "policy.judged-with": BASE_COMMIT},
    "mapping-weakened": {"policy.changed": [{"path": ".wingbox/controls.yaml", "kind": "controls", "change": "changed"}],
                         "tests.evidence": "incomplete", "tests.required.1": SKIPPED_TAX, "eligible": False, "eligible-reasons": ["tests: incomplete"]},
}
# The worked examples: (example, control, run, the state, the reason codes in the order listed). Written by hand.
EXAMPLES = [
    ("E01", "SDLC-03", "pass", "supported", []),
    ("E02", "SDLC-05", "pass", "supported", []),
    ("E03", "SDLC-06", "test-failed", "not-supported", ["verdict-failed"]),
    ("E04", "SDLC-04", "baseline-expired", "not-supported", ["verdict-failed"]),
    ("E05", "SDLC-05", "test-skipped", "incomplete", ["required-test-not-passed"]),
    ("E06", "SDLC-05", "docs-only", "incomplete", ["required-tests-absent"]),
    ("E07", "SDLC-03", "docs-only", "incomplete", ["verdict-not-applicable"]),
    ("E08", "SDLC-13", "web-not-checked", "incomplete", ["scope-not-covered", "verdict-unknown"]),
    ("E09", "SDLC-08", "pass", "not-supported", ["mapping-draft"]),
    ("E10", "SDLC-09", "pass", "not-supported", ["approval-expired"]),
    ("E11", "SDLC-10", "pass", "not-evaluated", ["person-record-needed"]),
    ("E12", "SDLC-14", "pass", "incomplete", ["required-evidence-empty"]),
    ("E13", "SDLC-07", "policy-edited", "not-supported", ["verdict-failed"]),
    ("E14", "SDLC-05", "mapping-weakened", "incomplete", ["required-test-not-passed"]),
    ("E15", "SDLC-12", "pass", "not-evaluated", ["operational-record-needed"]),
    ("E16", "SDLC-15", "pass", "not-supported", ["approval-not-yet-valid"]),
]


def run_record(name: str = "pass", **more: object) -> dict:
    """The fixture record with the fields of a run set: a dotted path names the field, a number a list item."""
    record = copy.deepcopy(RECORD)
    for dotted, value in {**RUNS[name], **more}.items():
        steps = dotted.split(".")
        target = record
        for step in steps[:-1]:
            target = target[int(step)] if step.isdigit() else target[step]
        if steps[-1].isdigit():
            target[int(steps[-1])] = copy.deepcopy(value)
        else:
            target[steps[-1]] = copy.deepcopy(value)
    return record


def mapping(source: str = "base", text: str = MAPPING_TEXT) -> wc.ControlMap:
    return wc.read(text.encode("ascii"), ".wingbox/controls.yaml", source, BASE_COMMIT if source == "base" else "")


def state(control: str, run: str = "pass", source: str = "base", day: str = DAY, **more: object) -> tuple:
    """(the state, the reason codes) the fixture mapping gives a control for a run."""
    said = wc.evaluate(mapping(source), run_record(run, **more), day)
    found = next(item for item in said["controls"] if item["id"] == control)
    return found["state"], [reason["code"] for reason in found["reasons"]]


def one(control: wc.Control, record: dict, source: str = "base", day: str = DAY) -> tuple:
    """(the state, the reason codes) of one control built by hand."""
    found = wc.evaluate(wc.ControlMap("map.yaml", source, "", "0" * 64, "n", "1", "o", (control,)), record, day)["controls"][0]
    return found["state"], [reason["code"] for reason in found["reasons"]]


GOOD = """format: wingbox-controls/1
mapping:
  name: "Map"
  version: "1"
  owner: "Lead"
controls:
  C-1:
    title: "T"
    status: draft
    method: static-analysis
    claim: "C"
    limits: "L"
    references:
      - framework: "F"
        edition: "1"
        reference: "R"
    scope-paths:
      - "src/**"
"""


class FileTest(unittest.TestCase):
    def test_the_mapping_is_read_as_controls_with_their_references_scope_and_evidence(self) -> None:
        """/* REQ-ACTION-210: the control mapping is a YAML file with `format: wingbox-controls/1`, a `mapping` with name, version and owner, and `controls`, each under the team's own id with title, status, approver, approval, valid-from, valid-until, risk, method, claim, limits, a list of references (framework, edition, reference), scope-paths, scope-parts, requires-verdicts and requires-tests; comments on their own lines and blank lines are skipped, line ends may be CRLF, a byte order mark is dropped; the reader gives the SHA-256 of the bytes it read and needs the standard library only */"""
        names, controls, errors = wc.parse(MAPPING_TEXT)
        self.assertEqual((), errors)
        self.assertEqual(("Lantern team control map", "2026.1", "Engineering lead"), names)
        self.assertEqual(CONTROLS, [(control.id, control.status, control.method) for control in controls])
        self.assertEqual(SDLC_03, controls[0])
        self.assertEqual(SDLC_13, controls[10])
        self.assertEqual(((), ("src/billing/",), ("billing",)), (controls[2].verdicts, controls[2].paths, controls[2].tests))
        self.assertEqual(("", "", "", ""), (controls[5].approver, controls[5].approval, controls[5].valid_from, controls[5].valid_until))
        self.assertEqual(((), ()), (controls[11].verdicts, controls[11].tests))
        self.assertEqual((names, controls, ()), wc.parse(MAPPING_TEXT.replace("\n", "\r\n")))
        read = wc.read(b"\xef\xbb\xbf" + MAPPING_BYTES, ".wingbox/controls.yaml", "base", BASE_COMMIT)
        self.assertEqual((".wingbox/controls.yaml", "base", BASE_COMMIT, hashlib.sha256(b"\xef\xbb\xbf" + MAPPING_BYTES).hexdigest(),
                          "Lantern team control map", "2026.1", "Engineering lead", controls, ()),
                         (read.path, read.source, read.commit, read.sha256, read.name, read.version, read.owner, read.controls, read.errors))
        self.assertEqual(("wingbox-controls/1", ".wingbox/controls.yaml"), (wc.FORMAT, wc.CONTROLS_PATH))
        minimal = wc.parse(GOOD)
        self.assertEqual((("Map", "1", "Lead"), ()), (minimal[0], minimal[2]))
        self.assertEqual([wc.Control("C-1", "T", "draft", "static-analysis", "C", "L", (wc.Reference("F", "1", "R"),), ("src/**",), line=7)],
                         list(minimal[1]))

    def test_a_mapping_outside_the_subset_is_refused_as_a_whole_with_the_line(self) -> None:
        """/* REQ-ACTION-211: a mapping with anything outside the subset is refused as a whole, with the line and the reason, and gives no control: a tab, an indentation that is not a level of two spaces, an unknown key at any level, another format or none, a value missing after a key or written after a list key, a list in brackets, an unquoted date or number or YAML word, a comment after a value, a control or a key named twice, a control without title, status, method, claim, limits, references or scope, an unknown status, method or verdict, a day that is not one, a reference without one of its three keys, a quote that is not closed, a backslash, an item outside a list, a numeric id, an item given twice; a file that is not UTF-8 is refused too */"""
        for old, new, reason in (
                ('  name: "Map"', '\tname: "Map"', "line 3: a tab in the indentation"),
                ('    title: "T"', '     title: "T"', "line 8: indented by 5 spaces"),
                ('      - "src/**"', '          - "src/**"', "line 18: indented by 10 spaces"),
                ("format: wingbox-controls/1\n", "extra: x\nformat: wingbox-controls/1\n", "line 1: unknown key `extra`"),
                ("format: wingbox-controls/1", "format: wingbox-controls/2", "line 1: format `wingbox-controls/2` is not read"),
                ("format: wingbox-controls/1\n", "", "line 1: no `format: wingbox-controls/1` line"),
                ("format: wingbox-controls/1\n", "format: wingbox-controls/1\nformat: wingbox-controls/1\n", "line 2: `format` is given twice"),
                ("mapping:\n", "mapping: none\n", "line 2: write what `mapping` holds on the lines below"),
                ('  owner: "Lead"', '  team: "Lead"', "line 5: not a key of `mapping`"),
                ('  owner: "Lead"\n', "", "line 1: `mapping` has no owner"),
                ('    title: "T"', '    owners: "T"', "line 8: unknown key `owners` in control `C-1`"),
                ('    title: "T"', "    title:", "line 8: write the value of `title` on its line"),
                ("    scope-paths:\n      - \"src/**\"", "    scope-paths: [a, b]", "line 17: write the items of `scope-paths` on the lines below"),
                ('    claim: "C"', '    claim: "C"\n    valid-from: 2026-01-01', "line 12: quote this value"),
                ('  version: "1"', "  version: 1", "line 4: quote this value"),
                ("    status: draft", "    status: yes", "line 9: quote this value"),
                ("    status: draft", "    status: draft # later", "line 9: a comment must be on a line of its own"),
                ("    status: draft", "    status: draft\n    status: draft", "line 10: `status` is given twice in control `C-1`"),
                ("controls:\n", "controls:\n  C-1:\n", "line 8: control `C-1` is named twice"),
                ('    title: "T"\n', "", "line 7: control `C-1` has no title"),
                ('    claim: "C"\n', "", "line 7: control `C-1` has no claim"),
                ('    limits: "L"\n', "", "line 7: control `C-1` has no limits"),
                ("    status: draft", "    status: final", "line 7: control `C-1`: status `final` is not one of approved, draft"),
                ("    method: static-analysis", "    method: guess", "line 7: control `C-1`: method `guess` is not one of static-analysis"),
                ('    claim: "C"', '    claim: "C"\n    valid-until: "2026-02-30"', "line 7: control `C-1`: valid-until `2026-02-30` is not a day"),
                ('    claim: "C"', '    claim: "C"\n    valid-from: "2026-03-01"\n    valid-until: "2026-02-01"', "line 7: control `C-1`: valid-from is after valid-until"),
                ('    references:\n      - framework: "F"\n        edition: "1"\n        reference: "R"\n', "", "line 7: control `C-1` has no references"),
                ('        edition: "1"\n', "", "line 7: control `C-1`: a reference has no edition"),
                ('        edition: "1"', '        section: "1"', "line 15: not a key of a reference"),
                ('    scope-paths:\n      - "src/**"\n', "", "line 7: control `C-1` has no scope"),
                ('      - "src/**"', '      - "src/**"\n    requires-verdicts:\n      - eligible', "line 7: control `C-1`: verdict `eligible` is not one of"),
                ('      - "src/**"', '      - "src/**', "line 18: a quote is not closed"),
                ('      - "src/**"', '      - "src\\\\**"', "line 18: no backslash in a quoted value"),
                ('      - "src/**"', '      - "src/**"\n      - "src/**"', "line 7: control `C-1`: `src/**` is given twice in `scope-paths`"),
                ('    title: "T"', '    title: "T"\n      - "x"', "line 9: not an item"),
                ("  C-1:", "  2026:", "line 7: not a control"),
                ("  C-1:", "  - C-1", "line 7: not a control"),
                ('  name: "Map"', '- name: "Map"', "line 3: not a key"),
        ):
            self.assertEqual(1, GOOD.count(old), old)
            names, controls, errors = wc.parse(GOOD.replace(old, new))
            self.assertTrue(any(error.startswith(reason) for error in errors), (reason, errors))
            self.assertEqual((("", "", ""), ()), (names, controls), reason)
        self.assertEqual(((), ("line 1: no `format: wingbox-controls/1` line", "line 1: no `mapping:` with `name`, `version` and `owner`",
                               "line 1: no `controls:`")), wc.parse("# nothing yet\n")[1:])
        unreadable = wc.read(b"format: \xff\n", "map.yaml", "given")
        self.assertEqual(((), ("not UTF-8 text: invalid start byte",), hashlib.sha256(b"format: \xff\n").hexdigest()),
                         (unreadable.controls, unreadable.errors, unreadable.sha256))

    def test_a_value_follows_the_rules_of_the_manifest_of_required_tests(self) -> None:
        """/* REQ-ACTION-212: a value of the mapping is read by the rules of an item of the manifest of required tests: every value the mapping reader takes, the manifest reader takes as the same text, and every value the manifest reader refuses, the mapping reader refuses; one rule is added: a plain value does not start with a digit, so a date, a version or a number is always in quotes */"""
        samples = ["src/billing/", '"src/**"', "'tests/a b.py'", "billing", "static-analysis", "wingbox-controls/1", ".github/workflows/", "_private/x",
                   "true", "No", "null", "*.py", "[a, b]", "a: b", "key:", "x # note", '"open', '"a\\b"', '""', "1.5", "2026-01-01", "3rd/party",
                   "0x1F", "12", '"2026-01-01"', '"12"', "!tag", "&anchor", "{a: b}", "a b c", "-dash", "~"]
        both = 0
        for text in samples:
            mine, theirs = wc._value(text), wt._item(text)
            if not mine[1]:
                self.assertEqual((mine[0], ""), theirs, text)
                both += 1
            if theirs[1]:
                self.assertTrue(mine[1], text)
        self.assertEqual(11, both)
        for text in ("2026-01-01", "3rd/party", "0x1F"):
            self.assertEqual(("", ""), (wc._value(text)[0], wt._item(text)[1]), text)
            self.assertIn("quote this value", wc._value(text)[1])

    def test_the_format_has_a_schema_that_names_the_same_keys_and_words(self) -> None:
        """/* REQ-ACTION-213: schema/wingbox-controls-1.schema.json describes the value of a mapping for an editor: the format word, the keys of `mapping`, of a control and of a reference, and the words of status, method and verdict are those the reader knows; the fixture mapping, written as that value, meets the schema */"""
        control = FORMAT_SCHEMA["properties"]["controls"]["additionalProperties"]
        self.assertEqual([wc.FORMAT], FORMAT_SCHEMA["properties"]["format"]["enum"])
        self.assertEqual(sorted(wc.TOP_KEYS), sorted(FORMAT_SCHEMA["properties"]))
        self.assertEqual(sorted(wc.MAPPING_KEYS), sorted(FORMAT_SCHEMA["properties"]["mapping"]["properties"]))
        self.assertEqual(sorted((*wc.SCALAR_KEYS, *wc.LIST_KEYS)), sorted(control["properties"]))
        self.assertEqual(sorted(wc.REFERENCE_KEYS), sorted(control["properties"]["references"]["items"]["properties"]))
        self.assertEqual((["approved", "draft"], list(METHODS), list(VERDICTS)),
                         (control["properties"]["status"]["enum"], control["properties"]["method"]["enum"],
                          control["properties"]["requires-verdicts"]["items"]["enum"]))
        self.assertEqual((STATES, METHODS, VERDICTS, ("approved", "draft")), (wc.STATES, wc.METHODS, wc.VERDICTS, wc.STATUSES))
        for found in mapping().controls:
            value = {"title": found.title, "status": found.status, "method": found.method, "claim": found.claim, "limits": found.limits,
                     "references": [dataclasses.asdict(reference) for reference in found.references], "scope-paths": list(found.paths),
                     "scope-parts": list(found.parts), "requires-verdicts": list(found.verdicts), "requires-tests": list(found.tests),
                     **{key: text for key, text in (("approver", found.approver), ("approval", found.approval), ("valid-from", found.valid_from),
                                                    ("valid-until", found.valid_until), ("risk", found.risk)) if text}}
            self.assertEqual([], problems(value, control), found.id)
        self.assertEqual([], problems({"name": "n", "version": "1", "owner": "o"}, FORMAT_SCHEMA["properties"]["mapping"]))


class StateTest(unittest.TestCase):
    def test_the_states_and_the_reason_codes_are_a_fixed_set(self) -> None:
        """/* REQ-ACTION-214: a control gets one of four states, supported, not-supported, incomplete or not-evaluated, and each reason is one of nineteen codes with a fixed state; a control with reasons of several states is not-supported before not-evaluated before incomplete; no state and no code says that a control conforms, complies, passes or is effective */"""
        self.assertEqual(STATES, wc.STATES)
        self.assertEqual(REASONS, wc.REASONS)
        self.assertEqual(19, len(wc.REASONS))
        self.assertEqual("supported", wc.state_of([]))
        self.assertEqual("incomplete", wc.state_of([("verdict-unknown", "")]))
        self.assertEqual("not-evaluated", wc.state_of([("scope-not-covered", ""), ("person-record-needed", "")]))
        self.assertEqual("not-supported", wc.state_of([("verdict-unknown", ""), ("person-record-needed", ""), ("mapping-draft", "")]))
        for word in (*wc.STATES, *wc.REASONS):
            for banned in ("conform", "compli", "comply", "effective", "satisf", "certif"):
                self.assertNotIn(banned, word, word)

    def test_each_worked_example_ends_in_the_state_written_for_it(self) -> None:
        """/* REQ-ACTION-215: the sixteen worked examples on the fixture repository end as written by hand: a control whose evidence holds is supported; a failed test verdict, an expired baseline entry and an edited policy file make it not-supported; a skipped required test, a change that touches none of the paths of a required group, a verdict the run calls NOT_APPLICABLE, a part that was not checked and a control that names no evidence make it incomplete; a draft, an expired and a not yet valid approval make it not-supported whatever the record holds; a method of a person or of operation makes it not-evaluated */"""
        self.assertEqual(16, len(EXAMPLES))
        self.assertEqual(sorted(RUNS), sorted({run for _example, _control, run, _state, _codes in EXAMPLES}))
        for example, control, run, expected, codes in EXAMPLES:
            self.assertEqual((expected, codes), state(control, run), example)
            self.assertEqual([], problems(run_record(run), RECORD_SCHEMA), run)
        self.assertEqual({"supported", "not-supported", "incomplete", "not-evaluated"}, {expected for *_rest, expected, _codes in EXAMPLES})

    def test_a_control_that_names_no_evidence_or_none_of_its_method_is_not_supported(self) -> None:
        """/* REQ-ACTION-216: an approved control of an evaluated method that names no verdict and no group of required tests is incomplete with `required-evidence-empty`, never supported: an empty required set is not read as nothing is required; a control that names evidence but none of the kind of its method (a boundary verdict for static-analysis, the test verdict or a group for executed-test, policy-unchanged for configuration-check) is incomplete with `method-evidence-missing` */"""
        record = run_record()
        empty = dataclasses.replace(SDLC_03, verdicts=(), tests=())
        self.assertEqual(("incomplete", ["required-evidence-empty"]), one(empty, record))
        self.assertEqual(("incomplete", ["required-evidence-empty"]), state("SDLC-14"))
        for method, verdicts, tests, expected in (
                ("static-analysis", ("coverage",), (), ("incomplete", ["method-evidence-missing"])),
                ("static-analysis", ("tests",), ("billing",), ("incomplete", ["method-evidence-missing"])),
                ("static-analysis", ("baseline",), (), ("supported", [])),
                ("executed-test", ("boundaries-introduced",), (), ("incomplete", ["method-evidence-missing"])),
                ("executed-test", (), ("billing",), ("supported", [])),
                ("executed-test", ("tests",), (), ("supported", [])),
                ("configuration-check", ("coverage", "tests"), (), ("incomplete", ["method-evidence-missing"])),
                ("configuration-check", ("policy-unchanged",), (), ("supported", [])),
        ):
            self.assertEqual(expected, one(dataclasses.replace(SDLC_03, method=method, verdicts=verdicts, tests=tests), record), (method, verdicts, tests))

    def test_a_draft_an_incomplete_or_an_expired_approval_supports_no_claim(self) -> None:
        """/* REQ-ACTION-217: a draft mapping supports no claim although its evidence holds; so does an approved one without approver, approval reference, valid-from or valid-until, one evaluated before its first day or after its last day; the first and the last day themselves are in force; the day is the one the caller gives, and a text that is no day is refused */"""
        record = run_record()
        self.assertEqual(("not-supported", ["mapping-draft"]), one(dataclasses.replace(SDLC_03, status="draft"), record))
        self.assertEqual(("not-supported", ["mapping-draft"]),
                         one(dataclasses.replace(SDLC_03, status="draft", approver="", approval="", valid_from="", valid_until=""), record))
        for field in ("approver", "approval", "valid_from", "valid_until"):
            self.assertEqual(("not-supported", ["approval-incomplete"]), one(dataclasses.replace(SDLC_03, **{field: ""}), record), field)
        for day, expected in (("2025-12-31", ("not-supported", ["approval-not-yet-valid"])), ("2026-01-01", ("supported", [])),
                              ("2026-12-31", ("supported", [])), ("2027-01-01", ("not-supported", ["approval-expired"]))):
            self.assertEqual(expected, one(SDLC_03, record, day=day), day)
        self.assertEqual(("not-supported", ["approval-expired"]), state("SDLC-09"))
        self.assertEqual(("supported", []), state("SDLC-09", day="2025-06-30"))
        self.assertEqual(("not-supported", ["approval-not-yet-valid"]), state("SDLC-15"))
        self.assertEqual(("not-supported", ["mapping-draft", "person-record-needed"]),
                         one(dataclasses.replace(SDLC_03, status="draft", method="human-judgement", verdicts=()), record))
        for text in ("", "2026-1-2", "2026-02-30", "today", "2026-01-02T03:04:05Z"):
            with self.assertRaises(ValueError):
                wc.evaluate(mapping(), record, text)

    def test_a_scope_the_run_did_not_cover_is_not_supported(self) -> None:
        """/* REQ-ACTION-218: a control is incomplete with `scope-not-covered` when the run left out something in its scope that the evidence it requires rests on. For every control: a part the scope names that the run does not name, files analysed that are not the commit named, a coverage that is not complete without a reason, a record without a coverage account. For a control that requires a verdict resting on the graph (the boundary verdicts, tests, coverage): a language left out, a stale graph, a tool error, and a changed file the paths filter left out that a scope path matches or that lies in a named part. For a control that requires a boundary verdict: a part that was not checked, when the scope names it or a scope path may lie in it (the root part and the files outside every part hold every path). A part not checked elsewhere and a file left out elsewhere do not count, and a control that requires only groups of required tests rests on neither the graph nor the rules */"""
        rules = dataclasses.replace(SDLC_03, verdicts=("boundaries-introduced",))                                      # a boundary verdict
        graph = dataclasses.replace(SDLC_03, method="executed-test", verdicts=("tests",))                               # a verdict of the graph only
        group = dataclasses.replace(SDLC_03, method="executed-test", paths=("src/billing/",), verdicts=(), tests=("billing",))   # recorded cases only
        covered, gap = ("supported", []), ("incomplete", ["scope-not-covered"])
        web = run_record("web-not-checked", **{"boundaries.introduced.verdict": "PASS"})
        self.assertEqual(gap, one(SDLC_13, web))
        self.assertEqual(covered, one(rules, web))
        for paths, expected in ((("src/**",), covered), (("src/billing/",), covered), (("web/src/**",), gap), (("web/",), gap), (("**/*.py",), gap),
                                (("w*",), gap), (("docs/architecture.md",), covered), (("web",), gap), (("webhooks/**",), covered)):
            self.assertEqual(expected, one(dataclasses.replace(rules, paths=paths), web), paths)
        for control in (dataclasses.replace(graph, paths=("web/src/**",)), dataclasses.replace(group, paths=("web/",)),
                        dataclasses.replace(group, paths=(), parts=("web",)), dataclasses.replace(graph, paths=(), parts=("web",))):
            self.assertEqual(covered, one(control, web), (control.paths, control.parts, control.verdicts))
        root = run_record("web-not-checked", **{"boundaries.introduced.verdict": "PASS", "coverage.reasons": ["not-checked: . (too-few-files)"],
                                                "coverage.parts": [{"part": ".", "style": "", "modules": 9, "checked": False, "reason": "too-few-files"}]})
        self.assertEqual((gap, covered, covered), (one(rules, root), one(graph, root), one(group, root)))
        outside = run_record(**{"coverage.complete": False, "coverage.reasons": ["not-checked: - (in-no-part)"], "coverage.parts": [
            {"part": "-", "style": "", "modules": 2, "checked": False, "reason": "in-no-part"}, {"part": ".", "style": "layered", "modules": 9, "checked": True, "reason": ""}]})
        self.assertEqual((gap, covered), (one(rules, outside), one(group, outside)))
        for control in (dataclasses.replace(SDLC_13, parts=("mobile",)), dataclasses.replace(group, paths=(), parts=("mobile",))):
            self.assertEqual(gap, one(control, run_record()), control.verdicts)
        self.assertEqual(covered, one(SDLC_13, run_record()))
        filtered = run_record(**{"coverage.complete": False, "coverage.reasons": ["paths-filter: 1 changed file(s) left out"],
                                 "coverage.excluded.paths.patterns": ["!src/billing/tax.py"], "coverage.excluded.paths.changed-files": ["src/billing/tax.py"]})
        for paths, parts, expected in ((("src/billing/",), (), gap), (("src/billing/tax",), (), gap), (("src/catalog/",), (), covered),
                                       (("src/**",), (), gap), ((), ("web",), covered), ((), (".",), gap)):
            for control in (rules, graph):
                self.assertEqual(expected, one(dataclasses.replace(control, paths=paths, parts=parts), filtered), (paths, parts, control.verdicts))
        self.assertEqual(covered, one(group, filtered))
        for reason, on_rules, on_graph, on_group in (
                ("skipped-language: kotlin (4 files)", gap, gap, covered),
                ("graph: STALE (built-from-another-commit)", gap, gap, covered),
                ("graph: UNKNOWN (not-read)", gap, gap, covered),
                ("tool-error: wingbox boundaries exited 2", gap, gap, covered),
                ("parts: UNKNOWN (wingbox-names-no-checked-part)", gap, covered, covered),
                ("tree-differs-from-commit: 1 file(s)", gap, gap, gap),
                ("tree-not-at-head: checked 222222222222, named 333333333333", gap, gap, gap),
                ("something-new: a gap this reader does not know", gap, gap, gap)):
            record = run_record(**{"coverage.complete": False, "coverage.reasons": [reason]})
            self.assertEqual((on_rules, on_graph, on_group), (one(rules, record), one(graph, record), one(group, record)), reason)
            self.assertEqual(on_rules, one(dataclasses.replace(rules, paths=("docs/",)), record), reason)
        silent = run_record(**{"coverage.complete": False})
        without = run_record()
        del without["coverage"]
        for record in (silent, without):
            self.assertEqual((gap, gap, gap), (one(rules, record), one(graph, record), one(group, record)))
        self.assertEqual((("boundaries-introduced", "boundaries-existing", "baseline", "tests", "coverage"),
                          ("boundaries-introduced", "boundaries-existing", "baseline")), (wc.GRAPH_VERDICTS, wc.RULE_VERDICTS))

    def test_a_required_test_without_a_passing_record_is_not_supported(self) -> None:
        """/* REQ-ACTION-219: a group of required tests holds only when the record has a test of the group and every one passed: a failed test makes the control not-supported (`required-test-failed`); a skipped, a partly run, an unrecorded test and a run without a test report make it incomplete (`required-test-not-passed`); a group the record holds no test of makes it incomplete (`required-tests-absent`), never supported; a manifest that could not be read makes it incomplete; a test step that ended with failure makes it not-supported whatever the reports hold, and one that was cancelled or skipped makes it incomplete */"""
        self.assertEqual(("supported", []), state("SDLC-05"))
        self.assertEqual(("not-supported", ["required-test-failed"]), state("SDLC-05", "test-failed"))
        for status in ("skipped", "partial", "no-record", "none"):
            lacking = {**SKIPPED_TAX, "status": status, "records": []}
            self.assertEqual(("incomplete", ["required-test-not-passed"]), state("SDLC-05", **{"tests.required.1": lacking}), status)
        both = state("SDLC-05", "test-failed", **{"tests.required.1": SKIPPED_TAX})
        self.assertEqual(("not-supported", ["required-test-failed"]), both)
        self.assertEqual(("incomplete", ["required-tests-absent"]), state("SDLC-05", **{"tests.required": []}))
        self.assertEqual(("incomplete", ["required-tests-absent"]), one(dataclasses.replace(SDLC_03, method="executed-test", verdicts=(), tests=("payments",)),
                                                                        run_record()))
        other_group = [{**item, "group": "catalog"} for item in RECORD["tests"]["required"]]
        self.assertEqual(("incomplete", ["required-tests-absent"]), state("SDLC-05", **{"tests.required": other_group}))
        unread = {"path": ".wingbox/required-tests.yaml", "source": "base", "commit": BASE_COMMIT, "sha256": "c" * 64, "errors": ["line 2: a tab in the indentation"]}
        self.assertEqual(("incomplete", ["required-tests-manifest-unreadable"]), state("SDLC-05", **{"tests.manifest": unread, "tests.required": []}))
        self.assertEqual(("not-supported", ["test-step-failed"]), state("SDLC-05", **{"tests.step-outcome": "failure"}))
        for outcome in ("cancelled", "skipped"):
            self.assertEqual(("incomplete", ["test-step-incomplete"]), state("SDLC-05", **{"tests.step-outcome": outcome}), outcome)
        self.assertEqual(("supported", []), state("SDLC-05", **{"tests.step-outcome": ""}))

    def test_a_verdict_that_is_unknown_or_not_applicable_is_never_read_as_support(self) -> None:
        """/* REQ-ACTION-220: each verdict a control may require is read from one field of the record and holds only for the words written here: boundaries-introduced PASS, boundaries-existing NONE, baseline NONE or ACCEPTED, tests complete, coverage complete, policy-unchanged with no changed file; FAIL, PRESENT, EXPIRED, failed and a changed policy file make the control not-supported; UNKNOWN, incomplete, none, none-found, a word the reader does not know and a field the record does not have make it incomplete (`verdict-unknown`); NOT_APPLICABLE makes it incomplete (`verdict-not-applicable`): not applicable is never inferred */"""
        fields = {"boundaries-introduced": "boundaries.introduced.verdict", "boundaries-existing": "boundaries.existing.verdict",
                  "baseline": "baseline.verdict", "tests": "tests.evidence", "coverage": "coverage.complete", "policy-unchanged": "policy.changed"}
        self.assertEqual(fields, wc.VERDICT_FIELDS)
        changed = [{"path": ".github/workflows/check.yml", "kind": "workflow", "change": "changed"}]
        table = {
            "boundaries-introduced": (("PASS",), ("FAIL",), ("UNKNOWN", "", "MAYBE"), ("NOT_APPLICABLE",)),
            "boundaries-existing": (("NONE",), ("PRESENT",), ("UNKNOWN", "PASS"), ()),
            "baseline": (("NONE", "ACCEPTED"), ("EXPIRED",), ("UNKNOWN", ""), ()),
            "tests": (("complete",), ("failed",), ("incomplete", "none", "none-found", "passed"), ()),
            "policy-unchanged": (([],), (changed,), ("unchanged", None), ()),
        }
        for name, (holds, fails, unknown, not_applicable) in table.items():
            control = dataclasses.replace(SDLC_03, method="static-analysis" if name != "tests" else "executed-test", verdicts=(name, "baseline"))
            if name == "policy-unchanged":
                control = dataclasses.replace(control, method="configuration-check")
            for values, expected in ((holds, ("supported", [])), (fails, ("not-supported", ["verdict-failed"])),
                                     (unknown, ("incomplete", ["verdict-unknown"])), (not_applicable, ("incomplete", ["verdict-not-applicable"]))):
                for value in values:
                    self.assertEqual(expected, one(control, run_record(**{fields[name]: value})), (name, value))
            removed = run_record()
            target = removed
            for step in fields[name].split(".")[:-1]:
                target = target[step]
            del target[fields[name].split(".")[-1]]
            self.assertEqual(("incomplete", ["verdict-unknown"]), one(control, removed), name)
        whole_run = dataclasses.replace(SDLC_03, verdicts=("boundaries-introduced", "coverage"))
        self.assertEqual(("incomplete", ["scope-not-covered", "verdict-unknown"]),
                         one(whole_run, run_record(**{"coverage.complete": False, "coverage.reasons": ["skipped-language: kotlin (4 files)"]})))
        self.assertEqual(("incomplete", ["verdict-unknown"]),
                         one(whole_run, run_record("web-not-checked", **{"boundaries.introduced.verdict": "PASS"})))
        self.assertEqual(("incomplete", ["verdict-not-applicable"]), state("SDLC-03", "docs-only"))
        self.assertEqual(("not-supported", ["verdict-failed", "verdict-unknown"]),
                         one(dataclasses.replace(SDLC_03, verdicts=("boundaries-introduced", "boundaries-existing")),
                             run_record(**{"boundaries.introduced.verdict": "FAIL", "boundaries.existing.verdict": "UNKNOWN"})))

    def test_a_method_of_a_person_or_of_operation_is_not_evaluated(self) -> None:
        """/* REQ-ACTION-221: a control whose method is human-judgement or document-review is not-evaluated with `person-record-needed`, and the reason says that the record of a person is needed; one whose method is operational-record is not-evaluated with `operational-record-needed`; no evidence of the record is read for them, also when they name some; the tool never writes the record of a person */"""
        for method, code, words in (("human-judgement", "person-record-needed", "the record of a person is needed"),
                                    ("document-review", "person-record-needed", "the record of a person is needed"),
                                    ("operational-record", "operational-record-needed", "a record of operation over a period is needed")):
            said = wc.evaluate(wc.ControlMap("m", "base", "", "0" * 64, "n", "1", "o", (dataclasses.replace(SDLC_03, method=method),)), run_record(), DAY)
            found = said["controls"][0]
            self.assertEqual(("not-evaluated", [code], []), (found["state"], [reason["code"] for reason in found["reasons"]], found["evidence"]), method)
            self.assertIn(words, found["reasons"][0]["detail"])
            self.assertEqual(["SDLC-03"], said["without-evidence"])
        self.assertEqual(("not-evaluated", ["person-record-needed"]), state("SDLC-10"))
        self.assertEqual(("not-evaluated", ["person-record-needed"]), state("SDLC-11", "test-failed"))
        self.assertEqual(("not-evaluated", ["operational-record-needed"]), state("SDLC-12"))
        said = wc.evaluate(mapping(), run_record(), DAY)
        keys = set(said) | {key for control in said["controls"] for key in control}
        self.assertEqual(set(), keys & {"reviewer", "review", "decision", "accepted", "acceptance", "conforms", "conformity"})

    def test_a_mapping_that_arrives_with_the_change_supports_no_claim(self) -> None:
        """/* REQ-ACTION-222: a mapping only the checked files hold (the base commit has none) arrives with the change it would judge: every control of it is not-supported with `mapping-not-in-base`, whatever else holds; a mapping of the base commit and a mapping given by hand are evaluated as they are */"""
        said = wc.evaluate(mapping("head"), run_record(), DAY)
        self.assertEqual({"not-supported"}, {control["state"] for control in said["controls"]})
        self.assertEqual({"mapping-not-in-base"}, {control["reasons"][0]["code"] for control in said["controls"]})
        self.assertEqual(("not-supported", ["mapping-not-in-base"]), state("SDLC-03", source="head"))
        self.assertEqual(("not-supported", ["mapping-not-in-base", "mapping-draft"]), state("SDLC-08", source="head"))
        self.assertEqual(("not-supported", ["mapping-not-in-base", "person-record-needed"]), state("SDLC-10", source="head"))
        self.assertEqual(("supported", []), state("SDLC-03", source="given"))
        self.assertEqual(("supported", []), state("SDLC-03", source="base"))

    def test_no_change_of_one_required_piece_of_evidence_leaves_a_control_supported(self) -> None:
        """/* REQ-ACTION-223: for every control of the fixture mapping that the passing run supports, replacing any one required verdict by any other word of its field, removing its field, or giving any one test of a required group any status but passed never leaves the control supported; the same holds with the test step ended by failure, cancelled or skipped */"""
        words = {"boundaries.introduced.verdict": ("FAIL", "UNKNOWN", "NOT_APPLICABLE", ""), "boundaries.existing.verdict": ("PRESENT", "UNKNOWN", ""),
                 "baseline.verdict": ("EXPIRED", "UNKNOWN", ""), "tests.evidence": ("failed", "incomplete", "none", "none-found", ""),
                 "coverage.complete": (False, None, "yes"), "policy.changed": ([{"path": "x", "kind": "rules", "change": "changed"}], None, "none")}
        passing = wc.evaluate(mapping(), run_record(), DAY)
        supported = [control for control in passing["controls"] if control["state"] == "supported"]
        self.assertEqual(["SDLC-03", "SDLC-04", "SDLC-05", "SDLC-06", "SDLC-07", "SDLC-13"], [control["id"] for control in supported])
        tried = 0
        for control in supported:
            for name in control["requires"]["verdicts"]:
                for word in words[wc.VERDICT_FIELDS[name]]:
                    self.assertNotEqual("supported", state(control["id"], **{wc.VERDICT_FIELDS[name]: word})[0], (control["id"], name, word))
                    tried += 1
            for group in control["requires"]["tests"]:
                for index, item in enumerate(RECORD["tests"]["required"]):
                    if item["group"] != group:
                        continue
                    for status in ("failed", "partial", "skipped", "no-record", "none"):
                        changed = {**item, "status": status}
                        self.assertNotEqual("supported", state(control["id"], **{f"tests.required.{index}": changed})[0], (control["id"], item["test"], status))
                        tried += 1
                for outcome in ("failure", "cancelled", "skipped"):
                    self.assertNotEqual("supported", state(control["id"], **{"tests.step-outcome": outcome})[0], (control["id"], outcome))
                    tried += 1
        self.assertEqual(38, tried)


class RecordTest(unittest.TestCase):
    def test_the_record_names_the_mapping_and_each_control_with_its_state(self) -> None:
        """/* REQ-ACTION-224: the evidence record gains `controls`: the mapping that was read (path, source, the base commit, the SHA-256 of its bytes, format, name, version, owner, errors, and whether the change edits it), the day validity was judged at (the day of `generated`, no clock is read), each control with what the file states of it, its state, its reasons (code and detail) and each piece of evidence it names with the field of the record it was read from, what the field holds and whether it holds, fails or is missing; a repository without a mapping gives source none and no control; the record meets its schema, and the same inputs give the same bytes */"""
        found = mapping()
        report = whole(controls_map=found)
        record = we.build_evidence(report, RUN, {})
        self.assertEqual([], problems(record, RECORD_SCHEMA))
        said = record["controls"]
        self.assertEqual(["mapping", "evaluated", "controls", "without-evidence", "unmapped-evidence", "statement"], list(said))
        self.assertEqual({"path": ".wingbox/controls.yaml", "source": "base", "commit": BASE_COMMIT, "sha256": hashlib.sha256(MAPPING_BYTES).hexdigest(),
                          "format": "wingbox-controls/1", "name": "Lantern team control map", "version": "2026.1", "owner": "Engineering lead",
                          "errors": [], "changed": False}, said["mapping"])
        self.assertEqual(("2026-01-02", STATEMENT), (said["evaluated"], said["statement"]))
        self.assertEqual([name for name, _status, _method in CONTROLS], [control["id"] for control in said["controls"]])
        self.assertEqual({
            "id": "SDLC-03", "title": "Changes are checked against the architecture rules", "status": "approved", "method": "static-analysis",
            "approver": "Head of engineering", "approval": "CAB-2026-014", "valid-from": "2026-01-01", "valid-until": "2026-12-31", "risk": "RISK-07",
            "claim": "The change introduces no violation of the declared architecture rules, and every part was looked at.",
            "limits": "Does not show that the rules are the right ones, nor that a person reviewed the change.",
            "references": [{"framework": "Lantern security policy", "edition": "v3", "reference": "SP-7.2 Secure design"},
                           {"framework": "Customer assurance questionnaire", "edition": "2026", "reference": "Q-14"}],
            "scope": {"paths": ["src/**"], "parts": []},
            "requires": {"verdicts": ["boundaries-introduced", "coverage"], "tests": []},
            "state": "supported", "reasons": [],
            "evidence": [{"kind": "verdict", "name": "boundaries-introduced", "field": "boundaries.introduced.verdict", "value": "PASS", "result": "holds"},
                         {"kind": "verdict", "name": "coverage", "field": "coverage.complete", "value": "complete", "result": "holds"}],
        }, said["controls"][0])
        # The run of `whole` gave no test report and names one part, the root: written out for three controls.
        by_id = {control["id"]: control for control in said["controls"]}
        self.assertEqual(("incomplete", [{"code": "required-tests-absent", "detail": "`billing`: the record holds no required test of this group (the change "
                                          "touches none of its paths, the manifest has no such group, or an earlier group names its tests)"}],
                          [{"kind": "required-tests", "name": "billing", "field": "tests.required", "value": "no test of this group", "result": "missing"}]),
                         (by_id["SDLC-05"]["state"], by_id["SDLC-05"]["reasons"], by_id["SDLC-05"]["evidence"]))
        self.assertEqual(("incomplete", [{"code": "verdict-unknown", "detail": "tests is none"}]), (by_id["SDLC-06"]["state"], by_id["SDLC-06"]["reasons"]))
        self.assertEqual(("incomplete", [{"code": "scope-not-covered", "detail": "part `web` is not a part the run names"}]),
                         (by_id["SDLC-13"]["state"], by_id["SDLC-13"]["reasons"]))
        self.assertEqual(json.dumps(record), json.dumps(we.build_evidence(report, RUN, {})))
        self.assertEqual(json.dumps(wc.evaluate(found, run_record(), DAY)), json.dumps(wc.evaluate(mapping(), run_record(), DAY)))
        none = we.build_evidence(whole(), RUN, {})
        self.assertEqual([], problems(none, RECORD_SCHEMA))
        self.assertEqual({"mapping": {"path": "", "source": "none", "commit": "", "sha256": "", "format": "", "name": "", "version": "", "owner": "",
                                      "errors": [], "changed": False},
                          "evaluated": "2026-01-02", "controls": [], "without-evidence": [], "unmapped-evidence": [], "statement": STATEMENT}, none["controls"])
        refused = we.build_evidence(whole(controls_map=mapping(text=MAPPING_TEXT.replace("status: draft", "status: final"))), RUN, {})
        self.assertEqual([], problems(refused, RECORD_SCHEMA))
        self.assertEqual(("", ["line 117: control `SDLC-08`: status `final` is not one of approved, draft"], [], []),
                         (refused["controls"]["mapping"]["format"], refused["controls"]["mapping"]["errors"], refused["controls"]["controls"],
                          refused["controls"]["unmapped-evidence"]))
        old = {key: value for key, value in none.items() if key != "controls"}
        self.assertEqual([], problems(old, RECORD_SCHEMA))

    def test_the_trace_lists_controls_without_evidence_and_evidence_no_control_names(self) -> None:
        """/* REQ-ACTION-225: an evaluation lists, forward, each control with the evidence it names, and, in reverse, the controls the record gives no evidence that counts for (the incomplete and the not-evaluated ones) and the evidence of the record that no control of the mapping names: each of the six verdicts and each group of required tests the record holds a test of; a control that is not-supported is not without evidence: its evidence fails or its mapping supports nothing */"""
        said = wc.evaluate(mapping(), run_record(), DAY)
        self.assertEqual(["SDLC-10", "SDLC-11", "SDLC-12", "SDLC-14"], said["without-evidence"])
        self.assertEqual([], said["unmapped-evidence"])
        skipped = wc.evaluate(mapping(), run_record("test-skipped"), DAY)
        self.assertEqual(["SDLC-05", "SDLC-06", "SDLC-10", "SDLC-11", "SDLC-12", "SDLC-14"], skipped["without-evidence"])
        few = wc.ControlMap("map.yaml", "base", BASE_COMMIT, "0" * 64, "n", "1", "o", (SDLC_03, SDLC_13))
        narrow = wc.evaluate(few, run_record("test-failed"), DAY)
        self.assertEqual([], narrow["without-evidence"])
        self.assertEqual([{"kind": "verdict", "name": "boundaries-existing"}, {"kind": "verdict", "name": "baseline"}, {"kind": "verdict", "name": "tests"},
                          {"kind": "verdict", "name": "policy-unchanged"}, {"kind": "required-tests", "name": "billing"},
                          {"kind": "required-tests", "name": "catalog"}], narrow["unmapped-evidence"])
        self.assertEqual({"supported": 2, "not-supported": 0, "incomplete": 0, "not-evaluated": 0}, wc.counts(narrow))
        self.assertEqual({"supported": 6, "not-supported": 3, "incomplete": 1, "not-evaluated": 3}, wc.counts(said))
        self.assertEqual((True, False), (wc.ok(narrow), wc.ok(said)))
        text = wc.lines(said)
        self.assertEqual(["mapping: .wingbox/controls.yaml (base)", f"mapping-sha256: {hashlib.sha256(MAPPING_BYTES).hexdigest()}",
                          "mapping-name: Lantern team control map version 2026.1, owner Engineering lead", "evaluated: 2026-01-02",
                          "control: SDLC-03 supported: Changes are checked against the architecture rules",
                          "  reference: Lantern security policy v3: SP-7.2 Secure design", "  reference: Customer assurance questionnaire 2026: Q-14",
                          "  evidence: verdict boundaries-introduced (boundaries.introduced.verdict): PASS: holds",
                          "  evidence: verdict coverage (coverage.complete): complete: holds"], text[:9])
        self.assertEqual(["without-evidence: SDLC-10, SDLC-11, SDLC-12, SDLC-14", "unmapped-evidence: none", f"statement: {STATEMENT}",
                          "controls: 6 supported, 3 not-supported, 1 incomplete, 3 not-evaluated"], text[-4:])
        self.assertIn("  reason: mapping-draft: status is draft", text)
        self.assertIn("  evidence: required-tests catalog (tests.required): 1 of 1 passed: holds", text)
        self.assertEqual(["unmapped-evidence: verdict boundaries-existing, verdict baseline, verdict tests, verdict policy-unchanged, "
                          "required-tests billing, required-tests catalog"], [line for line in wc.lines(narrow) if line.startswith("unmapped")])

    def test_a_mapping_weakened_in_the_same_change_does_not_judge_that_change(self) -> None:
        """/* REQ-ACTION-226: the mapping is read from the base commit when that commit holds it, so a change that drops a required test from the mapping, or adds a control, is still judged by the mapping of the base, with the base commit and the SHA-256 of the base's file in the record; the record says that the change edits the mapping; a mapping only the checked files hold is read from them with source head; a path given by the job is read the same way, and a path that is nowhere or leaves the repository is an error of the mapping; without a mapping at the default path there is none */"""
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            self.assertIsNone(pr.read_controls(str(repo), "", ""))
            base = make_fixture.commit(repo, {"src/billing/tax.py": "RATE = 1\n", ".wingbox/controls.yaml": MAPPING_TEXT}, "base")
            weaker = MAPPING_TEXT.replace("    requires-tests:\n      - billing\n", "    requires-verdicts:\n      - tests\n")
            self.assertNotEqual(MAPPING_TEXT, weaker)
            make_fixture.commit(repo, {"src/billing/tax.py": "RATE = 2\n", ".wingbox/controls.yaml": weaker, "quality/controls.yaml": weaker}, "head")
            read = pr.read_controls(str(repo), "", base)
            self.assertEqual((".wingbox/controls.yaml", "base", base, hashlib.sha256(MAPPING_TEXT.encode()).hexdigest(), 13, ()),
                             (read.path, read.source, read.commit, read.sha256, len(read.controls), read.errors))
            self.assertEqual(("billing",), read.controls[2].tests)
            added = pr.read_controls(str(repo), "quality/controls.yaml", base)
            self.assertEqual(("quality/controls.yaml", "head", "", hashlib.sha256(weaker.encode()).hexdigest(), ()),
                             (added.path, added.source, added.commit, added.sha256, added.controls[2].tests))
            absent = pr.read_controls(str(repo), "quality/missing.yaml", base)
            self.assertEqual(("quality/missing.yaml", "none", (), ("the file is not in the base commit and not in the checkout",)),
                             (absent.path, absent.source, absent.controls, absent.errors))
            outside = pr.read_controls(str(repo), "../elsewhere.yaml", base)
            self.assertEqual(("none", ("the path leaves the repository",)), (outside.source, outside.errors))
        weakened = wc.evaluate(read, run_record("mapping-weakened"), DAY)
        self.assertEqual((True, base), (weakened["mapping"]["changed"], weakened["mapping"]["commit"]))
        by_id = {control["id"]: control for control in weakened["controls"]}
        self.assertEqual(("incomplete", {"verdicts": [], "tests": ["billing"]}), (by_id["SDLC-05"]["state"], by_id["SDLC-05"]["requires"]))
        self.assertEqual(False, wc.evaluate(read, run_record(), DAY)["mapping"]["changed"])
        self.assertEqual({"not-supported"}, {control["state"] for control in wc.evaluate(added, run_record(), DAY)["controls"]})

    def test_the_mapping_is_a_file_the_check_is_judged_by(self) -> None:
        """/* REQ-ACTION-227: `.wingbox/controls.yaml` is a policy file of kind `controls`, and so is the mapping whose path the job gives (WINGBOX_CONTROLS): a change of it is a policy change that the record lists and `policy-change: fail` fails; the report names it as the mapping from controls to evidence; the package asks the commit for it, at the default path and at the path the record names; action.yml has the input `controls` and hands it to the script as WINGBOX_CONTROLS, and the shell-runner template of GitLab hands the variable to the container */"""
        self.assertEqual("controls", policy.FIXED[".wingbox/controls.yaml"])
        self.assertEqual([(".wingbox/controls.yaml", "controls", "changed")],
                         [(change.path, change.kind, change.how) for change in policy.policy_changes({".wingbox/controls.yaml": ("a\n", "b\n")}, frozenset(), {})])
        self.assertEqual([".wingbox/controls.yaml"], policy.candidates(["src/a.py", ".wingbox/controls.yaml", "docs/controls.yaml"], frozenset(), {}))
        named = policy.named({"WINGBOX_CONTROLS": "./quality/controls.yaml", "WINGBOX_REQUIRED_TESTS": "quality/required.yaml"})
        self.assertEqual({"quality/controls.yaml": "controls", "quality/required.yaml": "required-tests"}, named)
        self.assertEqual([("quality/controls.yaml", "controls", "added")],
                         [(change.path, change.kind, change.how) for change in policy.policy_changes({"quality/controls.yaml": (None, "b\n")}, frozenset(), named)])
        changed = whole(policy=(facts.PolicyChange(".wingbox/controls.yaml", "controls", "changed"),), policy_mode="fail", controls_map=mapping())
        record = we.build_evidence(changed, RUN, {})
        self.assertEqual([], problems(record, RECORD_SCHEMA))
        self.assertEqual(([{"path": ".wingbox/controls.yaml", "kind": "controls", "change": "changed"}], True),
                         (record["policy"]["changed"], record["controls"]["mapping"]["changed"]))
        self.assertEqual(1, policy.policy_status(changed))
        self.assertIn("- `.wingbox/controls.yaml` (changed): the mapping from controls to evidence\n", pr.build_comment(changed))
        self.assertEqual("controls", pkg.POLICY_FILES[".wingbox/controls.yaml"])
        self.assertEqual(policy.FIXED, pkg.POLICY_FILES)
        self.assertIn("controls", pkg.POLICY_KINDS)
        elsewhere = copy.deepcopy(record)
        elsewhere["controls"]["mapping"]["path"] = "quality/controls.yaml"
        self.assertIn(("quality/controls.yaml", "controls"), pkg.policy_files(elsewhere))
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(action, r'\n  controls:\n    description: [^\n]+\n    default: ""\n')
        self.assertIn("WINGBOX_CONTROLS: ${{ inputs.controls }}", action)
        self.assertIn("--env WINGBOX_CONTROLS", (ROOT / "gitlab" / "wingbox-shell.gitlab-ci.yml").read_text(encoding="utf-8"))


class PackageTest(unittest.TestCase):
    def test_the_manifest_of_a_package_repeats_the_state_of_each_control(self) -> None:
        """/* REQ-ACTION-228: the `control` object of a package manifest is filled from the record: where the mapping was read from (base, head, given, or refused for a mapping with errors), its path, SHA-256, commit, name, version and owner, the day it was evaluated at, and each control with its id, title, state and reason codes; for a record that evaluated no mapping, or has no `controls`, it is `none` with empty values; the manifest meets its schema, `verify` computes the object again from the record, and a manifest whose control states were edited does not verify; the statement that nothing in a package states that a control conforms stays */"""
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder).resolve()
            repo = work / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            head = commit(repo, {".wingbox/controls.yaml": MAPPING_BYTES, "src/billing/tax.py": b"RATE = 2\n"})
            record = run_record("test-skipped", **{"subject.head": head, "subject.checked": head, "coverage.commit": head, "tests.reports": [],
                                                   "documents": [{"name": "wingbox-report.md", "sha256": hashlib.sha256(b"report\n").hexdigest()},
                                                                 {"name": "wingbox-plan.sexp", "sha256": hashlib.sha256(b"plan\n").hexdigest()}],
                                                   "files": [{"path": "src/billing/tax.py", "sha256": hashlib.sha256(b"RATE = 2\n").hexdigest()}]})
            record["controls"] = wc.evaluate(mapping(), record, DAY)
            self.assertEqual([], problems(record, RECORD_SCHEMA))
            run_folder(work / "run", record)
            self.assertEqual(0, run_cli("pack", "--run-dir", work / "run", "--out", work / "package.zip", "--repo", repo)[0])
            members = dict(members_of((work / "package.zip").read_bytes()))
            manifest = json.loads(members["manifest.json"])
            self.assertEqual([], problems(manifest, PACKAGE_SCHEMA))
            self.assertEqual({"mapping": "base", "path": ".wingbox/controls.yaml", "sha256": hashlib.sha256(MAPPING_BYTES).hexdigest(), "commit": BASE_COMMIT,
                              "name": "Lantern team control map", "version": "2026.1", "owner": "Engineering lead", "evaluated": "2026-01-02"},
                             {key: value for key, value in manifest["control"].items() if key != "controls"})
            self.assertEqual([
                {"id": "SDLC-03", "title": "Changes are checked against the architecture rules", "state": "supported", "reasons": []},
                {"id": "SDLC-04", "title": "No architecture violation stays without an accepted exception that has an end date", "state": "supported",
                 "reasons": []},
                {"id": "SDLC-05", "title": "Billing changes pass the billing tests", "state": "incomplete", "reasons": ["required-test-not-passed"]},
                {"id": "SDLC-06", "title": "Every change passes the tests that use what it changed", "state": "incomplete", "reasons": ["verdict-unknown"]},
                {"id": "SDLC-07", "title": "A change does not edit the files it is checked by", "state": "supported", "reasons": []},
                {"id": "SDLC-08", "title": "Catalog changes pass the catalog tests", "state": "not-supported", "reasons": ["mapping-draft"]},
            ], manifest["control"]["controls"][:6])
            self.assertEqual("Nothing in this package states that a control conforms.", manifest["statements"]["conformity"])
            self.assertEqual(MAPPING_BYTES, members["policy/.wingbox/controls.yaml"])
            self.assertIn({"path": ".wingbox/controls.yaml", "kind": "controls", "state": "present", "member": "policy:.wingbox/controls.yaml"},
                          manifest["policy"]["files"])
            self.assertEqual(0, run_cli("verify", work / "package.zip", "--repo", repo)[0])
            edited = copy.deepcopy(manifest)
            edited["control"]["controls"][2].update(state="supported", reasons=[])
            forged = pkg.archive([("manifest.json", pkg.encode(edited))] + [(name, data) for name, data in members_of((work / "package.zip").read_bytes())
                                                                             if name != "manifest.json"])
            (work / "forged.zip").write_bytes(forged)
            status, out, _err = run_cli("verify", work / "forged.zip", "--json")
            self.assertEqual((1, [("manifest-mismatch", "manifest.json")]),
                             (status, [(problem["code"], problem["member"]) for problem in json.loads(out)["problems"]]))
            self.assertIn("control", json.loads(out)["problems"][0]["detail"])
        none = {"mapping": "none", "path": "", "sha256": "", "commit": "", "name": "", "version": "", "owner": "", "evaluated": "", "controls": []}
        self.assertEqual(none, pkg.control(None))
        self.assertEqual(none, pkg.control(we.build_evidence(whole(), RUN, {})))
        self.assertEqual(none, pkg.control({key: value for key, value in record.items() if key != "controls"}))
        refused = we.build_evidence(whole(controls_map=mapping(text="format: wingbox-controls/2\n")), RUN, {})
        self.assertEqual(("refused", []), (pkg.control(refused)["mapping"], pkg.control(refused)["controls"]))
        self.assertEqual([], problems({**manifest, "control": pkg.control(refused)}, PACKAGE_SCHEMA))
        by_hand = copy.deepcopy(record)
        by_hand["controls"] = wc.evaluate(mapping("given"), record, DAY)
        self.assertEqual("given", pkg.control(by_hand)["mapping"])


class CommandTest(unittest.TestCase):
    def test_the_command_evaluates_a_mapping_against_a_record_or_a_package_by_hand(self) -> None:
        """/* REQ-ACTION-229: `controls --record FILE --mapping FILE` prints the mapping, each control with its state, reasons and evidence, the controls without evidence, the evidence no control names, the statement and the count of states, and says whether the mapping has the bytes the run evaluated; `--package FILE` evaluates the record of a package that verifies, with the mapping the package holds unless one is named; `--date` sets the day validity is judged at, and `--json` prints the evaluation as one value; the command ends with 0 when the mapping was read and no control is not-supported or incomplete, with 1 for a defect (such a control, a mapping that is refused, a package that does not verify, a file that is no record of version 2) and with 2 for wrong usage (no record and no package, both, no mapping, a day that is not one, a file that is not there) */"""
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder).resolve()
            record = run_record()
            (work / "record.json").write_bytes(as_json(record))
            (work / "controls.yaml").write_bytes(MAPPING_BYTES)
            status, out, err = run_cli("controls", "--record", work / "record.json", "--mapping", work / "controls.yaml")
            given = wc.evaluate(wc.read(MAPPING_BYTES, str(work / "controls.yaml").replace("\\", "/"), "given"), record, DAY)
            self.assertEqual((1, ""), (status, err))
            self.assertEqual([f"record: {work / 'record.json'}", "run-mapping: the record evaluated no mapping", *wc.lines(given)], out.splitlines())
            self.assertEqual("controls: 6 supported, 3 not-supported, 1 incomplete, 3 not-evaluated", out.splitlines()[-1])
            self.assertIn("without-evidence: SDLC-10, SDLC-11, SDLC-12, SDLC-14", out.splitlines())
            self.assertIn("unmapped-evidence: none", out.splitlines())
            status, out, _err = run_cli("controls", "--record", work / "record.json", "--mapping", work / "controls.yaml", "--json")
            self.assertEqual((1, given), (status, json.loads(out)))
            good = MAPPING_TEXT.split("  SDLC-04:")[0]
            (work / "good.yaml").write_text(good, encoding="ascii", newline="\n")
            status, out, _err = run_cli("controls", "--record", work / "record.json", "--mapping", work / "good.yaml")
            self.assertEqual((0, "controls: 1 supported, 0 not-supported, 0 incomplete, 0 not-evaluated"), (status, out.splitlines()[-1]))
            status, out, _err = run_cli("controls", "--record", work / "record.json", "--mapping", work / "good.yaml", "--date", "2027-01-01")
            self.assertEqual((1, "controls: 0 supported, 1 not-supported, 0 incomplete, 0 not-evaluated"), (status, out.splitlines()[-1]))
            self.assertIn("  reason: approval-expired: valid until 2026-12-31, evaluated at 2027-01-01", out.splitlines())
            (work / "refused.yaml").write_text(good.replace("status: approved", "status: final"), encoding="ascii", newline="\n")
            status, out, _err = run_cli("controls", "--record", work / "record.json", "--mapping", work / "refused.yaml")
            self.assertEqual(1, status)
            self.assertIn("mapping-error: line 11: control `SDLC-03`: status `final` is not one of approved, draft", out.splitlines())
            self.assertEqual("controls: 0 supported, 0 not-supported, 0 incomplete, 0 not-evaluated", out.splitlines()[-1])
            (work / "other.json").write_text('{"schema": "wingbox-evidence/1"}\n', encoding="ascii")
            status, out, _err = run_cli("controls", "--record", work / "other.json", "--mapping", work / "good.yaml")
            self.assertEqual((1, "controls: not evaluated (not a wingbox-evidence/2 record)"), (status, out.splitlines()[-1]))
            repo = work / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            head = commit(repo, {".wingbox/controls.yaml": good.encode("ascii"), "src/billing/tax.py": b"RATE = 2\n"})
            packed = run_record(**{"subject.head": head, "subject.checked": head, "coverage.commit": head, "tests.reports": [],
                                   "documents": [{"name": "wingbox-report.md", "sha256": hashlib.sha256(b"report\n").hexdigest()},
                                                 {"name": "wingbox-plan.sexp", "sha256": hashlib.sha256(b"plan\n").hexdigest()}],
                                   "files": [{"path": "src/billing/tax.py", "sha256": hashlib.sha256(b"RATE = 2\n").hexdigest()}]})
            packed["controls"] = wc.evaluate(wc.read(good.encode("ascii"), ".wingbox/controls.yaml", "base", BASE_COMMIT), packed, DAY)
            run_folder(work / "run", packed)
            self.assertEqual(0, run_cli("pack", "--run-dir", work / "run", "--out", work / "package.zip", "--repo", repo)[0])
            status, out, _err = run_cli("controls", "--package", work / "package.zip")
            self.assertEqual(0, status, out)
            self.assertEqual([f"package: {work / 'package.zip'}", "run-mapping: the same bytes as the run evaluated (base)",
                              "mapping: .wingbox/controls.yaml (given)"], out.splitlines()[:3])
            status, out, _err = run_cli("controls", "--package", work / "package.zip", "--mapping", work / "controls.yaml")
            self.assertEqual(1, status)
            self.assertEqual(f"run-mapping: other bytes than the run evaluated (base, SHA-256 {hashlib.sha256(good.encode('ascii')).hexdigest()})",
                             out.splitlines()[1])
            broken = bytearray((work / "package.zip").read_bytes())
            broken[bytes(broken).index(b'"schema": "wingbox-evidence/2"') + 3] ^= 1      # one byte of the record member
            (work / "broken.zip").write_bytes(bytes(broken))
            status, out, _err = run_cli("controls", "--package", work / "broken.zip")
            self.assertEqual((1, "controls: not evaluated (the package does not verify)"), (status, out.splitlines()[-1]))
            for arguments in (("controls",), ("controls", "--record", work / "record.json", "--package", work / "package.zip"),
                              ("controls", "--record", work / "record.json"), ("controls", "--record", work / "missing.json", "--mapping", work / "good.yaml"),
                              ("controls", "--record", work / "record.json", "--mapping", work / "missing.yaml"),
                              ("controls", "--record", work / "record.json", "--mapping", work / "good.yaml", "--date", "2026-02-30"),
                              ("controls", "--record", work / "record.json", "--mapping", work / "good.yaml", "--date", "tomorrow")):
                self.assertEqual(2, run_cli(*arguments)[0], arguments)
            empty_repo = work / "empty"
            empty_repo.mkdir()
            subprocess.run(["git", "init", "-q", str(empty_repo)], check=True)
            other_head = commit(empty_repo, {"src/billing/tax.py": b"RATE = 2\n"})
            no_mapping = run_record(**{"subject.head": other_head, "subject.checked": other_head, "coverage.commit": other_head, "tests.reports": [],
                                       "documents": [], "files": []})
            run_folder(work / "run2", no_mapping, leave_out=("wingbox-report.md", "wingbox-plan.sexp"))
            self.assertEqual(0, run_cli("pack", "--run-dir", work / "run2", "--out", work / "plain.zip", "--repo", empty_repo)[0])
            status, _out, err = run_cli("controls", "--package", work / "plain.zip")
            self.assertEqual((2, "wingbox-evidence: no control mapping: name one with --mapping (the package holds none at .wingbox/controls.yaml)\n"),
                             (status, err))


class PurityTest(unittest.TestCase):
    def test_the_module_stands_alone_and_reads_no_clock(self) -> None:
        """/* REQ-ACTION-230: wingbox_controls.py imports only datetime, fnmatch, hashlib, re, dataclasses and typing, and no module of this repository: no file, no process, no network, no model; it never asks for the time of day; it calls nothing that runs text as code */"""
        tree = ast.parse((ROOT / "wingbox_controls.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual({"__future__", "datetime", "fnmatch", "hashlib", "re", "dataclasses", "typing"}, names)
        called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertEqual(set(), called & {"exec", "eval", "compile", "__import__", "open"})
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertEqual(set(), attributes & {"now", "today", "utcnow", "time", "environ", "getenv", "system", "run", "urlopen", "read_text", "read_bytes"})

    def test_no_output_says_that_a_control_conforms(self) -> None:
        """/* REQ-ACTION-231: nothing an evaluation writes says that a control conforms, complies, is satisfied, is effective or is certified: the only sentence with such a word is the statement, which says that a state does not state it; the states are the four of the fixed set */"""
        for run in RUNS:
            said = wc.evaluate(mapping(), run_record(run), DAY)
            ours = copy.deepcopy(said)
            for control in ours["controls"]:      # the words of the team's own file are the team's
                for key in ("title", "claim", "limits", "references", "approver", "approval", "risk"):
                    control[key] = ""
            text = json.dumps(ours).replace(STATEMENT, "").lower() + "\n".join(wc.lines(ours)).replace(STATEMENT, "").lower()
            for word in ("conform", "compli", "comply", "satisf", "effective", "certif", "assur", "guarantee"):
                self.assertNotIn(word, text, (run, word))
            self.assertEqual(set(), {control["state"] for control in said["controls"]} - set(STATES))
        self.assertIn("does not state that a control conforms", STATEMENT)


class DocumentsTest(unittest.TestCase):
    def test_the_worked_examples_are_documented_as_the_tests_expect_them(self) -> None:
        """/* REQ-ACTION-232: docs/controls/examples.md walks through every worked example of the tests on the fixture repository: for each its heading with the example, the control and the state, the reason codes, the fields of the record it reads, and a line on what a reviewer can and cannot conclude; it names the fixture files, invents every id and reference, and names no standard, no criteria set and no product */"""
        text = (ROOT / "docs" / "controls" / "examples.md").read_text(encoding="utf-8")
        for example, control, run, expected, codes in EXAMPLES:
            heading = f"## {example}: {control}, run `{run}`: {expected}\n"
            self.assertIn(heading, text, example)
            body = text.split(heading)[1].split("\n## ")[0]
            self.assertIn("Reasons: " + (", ".join(f"`{code}`" for code in codes) if codes else "none") + "\n", body, example)
            self.assertIn("Record fields read:", body, example)
            self.assertIn("A reviewer can conclude:", body, example)
            self.assertIn("A reviewer cannot conclude:", body, example)
        self.assertEqual(len(EXAMPLES), text.count("\n## E"))
        for needed in ("tests/fixtures/controls/controls.yaml", "tests/fixtures/controls/record.json", "tests/fixtures/controls/required-tests.yaml",
                       "invented", "python wingbox_evidence_cli.py controls --record"):
            self.assertIn(needed, text, needed)
        for banned in ("ISO", "SOC 2", "27001", "42001", "NIST", "AICPA"):
            self.assertNotIn(banned, text, banned)
        self.assertNotIn("ISO", MAPPING_TEXT)

    def test_the_format_the_states_and_the_command_are_documented(self) -> None:
        """/* REQ-ACTION-233: README.md has the section "Control mapping" with the file and its default path, a full example the reader takes without an error, the keys, the six methods, the six verdicts with the field each is read from, the four states, every reason code, the rule of the base commit, the `controls` object of the record and its schemas, the command with its exit statuses, the input `controls` with WINGBOX_CONTROLS, and what a state does not say; the Development section names the module; CHANGELOG.md names the change under Unreleased */"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("\n## Control mapping\n")[1].split("\n## ")[0]
        example = section.split("```yaml\n# .wingbox/controls.yaml\n")[1].split("```")[0]
        names, controls, errors = wc.parse(example)
        self.assertEqual(((), True), (errors, len(controls) >= 2))
        for needed in ("`.wingbox/controls.yaml`", "`format: wingbox-controls/1`", "schema/wingbox-controls-1.schema.json", "wingbox-evidence-2.schema.json",
                       "python wingbox_evidence_cli.py controls", "`controls`", "WINGBOX_CONTROLS", "base commit", "`mapping-not-in-base`",
                       "docs/controls/examples.md", "does not state that a control conforms", "`without-evidence`", "`unmapped-evidence`",
                       *(f"`{word}`" for word in (*STATES, *METHODS, *VERDICTS, *REASONS, *wc.VERDICT_FIELDS.values(), *wc.SCALAR_KEYS, *wc.LIST_KEYS))):
            self.assertIn(needed, section, needed)
        self.assertIn("`.wingbox/controls.yaml`", readme.split("### A change that edits what it is checked by")[1].split("\n### ")[0])
        self.assertIn("| `controls` | `.wingbox/controls.yaml` |", readme)
        self.assertIn("`wingbox_controls.py`", readme.split("\n## Development\n")[1])
        self.assertIn("wingbox_evidence_cli.py controls", readme.split("\n## Development\n")[1])
        unreleased = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## Unreleased")[1].split("\n## ")[0]
        for needed in ("`.wingbox/controls.yaml`", "`controls`", "wingbox_evidence_cli.py controls", "schema/wingbox-controls-1.schema.json"):
            self.assertIn(needed, unreleased, needed)


E2E_MAP = """format: wingbox-controls/1
mapping:
  name: "Fixture control map"
  version: "1"
  owner: "Fixture owner"
controls:
  CHG-01:
    title: "Service changes pass the billing tests"
    status: approved
    approver: "Fixture approver"
    approval: "APPROVAL-1"
    valid-from: "2020-01-01"
    valid-until: "2099-12-31"
    risk: "RISK-1"
    method: executed-test
    claim: "Every test of the billing group has a passing record."
    limits: "Does not show that the tests are sufficient."
    references:
      - framework: "Fixture policy"
        edition: "1"
        reference: "P-1"
    scope-paths:
      - app/services/
    requires-tests:
      - billing
  CHG-02:
    title: "Changes are reviewed by a second person"
    status: approved
    approver: "Fixture approver"
    approval: "APPROVAL-2"
    valid-from: "2020-01-01"
    valid-until: "2099-12-31"
    method: human-judgement
    claim: "A second person approved the change."
    limits: "The run records no review."
    references:
      - framework: "Fixture policy"
        edition: "1"
        reference: "P-2"
    scope-paths:
      - "app/**"
  CHG-03:
    title: "Refund changes pass the refund tests"
    status: approved
    approver: "Fixture approver"
    approval: "APPROVAL-3"
    valid-from: "2020-01-01"
    valid-until: "2099-12-31"
    method: executed-test
    claim: "Every test of the refunds group has a passing record."
    limits: "Does not show that the tests are sufficient."
    references:
      - framework: "Fixture policy"
        edition: "1"
        reference: "P-3"
    scope-paths:
      - app/api/
    requires-tests:
      - refunds
"""
E2E_FILES = {"tests/__init__.py": "", "tests/test_billing.py": "from app.services.billing import bill\n\n\ndef test_bill():\n    assert bill(None) is None\n",
             ".wingbox/required-tests.yaml": "billing:\n  paths:\n    - app/services/**\n  tests:\n    - tests/test_billing.py\n"
                                             "refunds:\n  paths:\n    - app/api/**\n  tests:\n    - tests/test_refund.py\n",
             ".wingbox/controls.yaml": E2E_MAP}
E2E_JUNIT = '<testsuites><testsuite name="pytest"><testcase classname="tests.test_billing" name="test_bill"/></testsuite></testsuites>'


@unittest.skipUnless(importlib.util.find_spec("wingbox"), "wingbox is not installed in this Python")
class EndToEndTest(unittest.TestCase):
    def test_the_whole_script_evaluates_the_mapping_of_the_base_commit(self) -> None:
        """/* REQ-ACTION-234: run as a job would run it on a repository whose base commit holds a mapping and whose change weakens it: the record names the mapping of the base with its commit and SHA-256 and says that the change edits it, lists the change as a policy change of kind `controls`, and gives each control the state the base's mapping gives it: the control whose required group passed is supported (it requires recorded cases only, so a part without boundary rules is no gap in its scope); the control of a person is not-evaluated; the control whose group the change does not touch is incomplete; a second run whose base has no mapping gives every control `mapping-not-in-base`; the record meets its schema both times and the checkout stays clean */"""
        with tempfile.TemporaryDirectory() as tmp:
            repo, out = Path(tmp) / "repo", Path(tmp) / "out"
            repo.mkdir()
            out.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            first = make_fixture.commit(repo, make_fixture.BASE, "first")
            base = make_fixture.commit(repo, E2E_FILES, "base")
            weaker = E2E_MAP.replace("    requires-tests:\n      - billing\n", "    requires-verdicts:\n      - tests\n")
            head = make_fixture.commit(repo, {**make_fixture.HEAD, ".wingbox/controls.yaml": weaker}, "head")
            (repo / "test-results").mkdir()
            (repo / "test-results" / "junit.xml").write_text(E2E_JUNIT, encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("GITHUB_", "WINGBOX_", "CI_")) or key == "WINGBOX_JOBS"}
            env.update(WINGBOX_VERSION="test", WINGBOX_TEST_RESULTS="test-results/*.xml", GITHUB_OUTPUT=str(out / "output.txt"))
            command = [sys.executable, "-P", str(ROOT / "wingbox_pr.py"), "--root", str(repo), "--base", base, "--head", head, "--out", str(out)]
            done = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, RECORD_SCHEMA))
            said = record["controls"]
            self.assertEqual({"path": ".wingbox/controls.yaml", "source": "base", "commit": base, "sha256": hashlib.sha256(E2E_MAP.encode()).hexdigest(),
                              "format": "wingbox-controls/1", "name": "Fixture control map", "version": "1", "owner": "Fixture owner", "errors": [],
                              "changed": True}, said["mapping"])
            self.assertEqual(record["generated"][:10], said["evaluated"])
            self.assertIn({"path": ".wingbox/controls.yaml", "kind": "controls", "change": "changed"}, record["policy"]["changed"])
            by_id = {control["id"]: control for control in said["controls"]}
            self.assertEqual(["CHG-01", "CHG-02", "CHG-03"], list(by_id))
            self.assertEqual({"verdicts": [], "tests": ["billing"]}, by_id["CHG-01"]["requires"])
            self.assertEqual([{"kind": "required-tests", "name": "billing", "field": "tests.required", "value": "1 of 1 passed", "result": "holds"}],
                             by_id["CHG-01"]["evidence"])
            self.assertEqual(("head", []), (record["coverage"]["tree"], record["subject"]["differs"]))
            self.assertEqual(("supported", []), (by_id["CHG-01"]["state"], [reason["code"] for reason in by_id["CHG-01"]["reasons"]]))
            self.assertEqual(("not-evaluated", ["person-record-needed"]), (by_id["CHG-02"]["state"], [reason["code"] for reason in by_id["CHG-02"]["reasons"]]))
            self.assertEqual(("incomplete", ["required-tests-absent"]), (by_id["CHG-03"]["state"], [reason["code"] for reason in by_id["CHG-03"]["reasons"]]))
            self.assertEqual(["CHG-02", "CHG-03"], said["without-evidence"])
            self.assertIn("### This change edits what it is checked by", (out / "wingbox-report.md").read_text(encoding="utf-8"))
            self.assertEqual("", subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                                                text=True).stdout)
            again = [*command[:command.index("--base") + 1], first, *command[command.index("--base") + 2:]]
            done = subprocess.run(again, cwd=out, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            record = json.loads((out / "wingbox-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual([], problems(record, RECORD_SCHEMA))
            self.assertEqual(("head", "", hashlib.sha256(weaker.encode()).hexdigest()),
                             tuple(record["controls"]["mapping"][key] for key in ("source", "commit", "sha256")))
            self.assertEqual({"not-supported"}, {control["state"] for control in record["controls"]["controls"]})
            self.assertEqual({"mapping-not-in-base"}, {control["reasons"][0]["code"] for control in record["controls"]["controls"]})


if __name__ == "__main__":
    unittest.main()

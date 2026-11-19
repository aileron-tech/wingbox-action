# Control mapping: sixteen worked examples

These examples show what the control mapping of a team gives for a run, and what a reviewer can and
cannot conclude from it. Everything in them is invented: the Lantern team, its shop repository, the
control ids (`SDLC-03` and so on), the approvals, the risks, and the references, which name the team's
own security policy and a customer questionnaire. No framework is quoted: a reference is the team's
free text, which the tool repeats and does not read.

The files are in the repository, and the tests run every example
(`tests/test_controls.py`, REQ-ACTION-215):

- `tests/fixtures/controls/controls.yaml`: the mapping, thirteen controls.
- `tests/fixtures/controls/required-tests.yaml`: the manifest of required tests of the repository
  (the groups `billing` and `catalog`).
- `tests/fixtures/controls/record.json`: the evidence record of the run `pass`, a change of
  `src/billing/invoice.py` and `src/catalog/items.py` on 2026-01-02. Every other run below is this
  record with the fields named in its example set to other values.

To see an example yourself:

```bash
python wingbox_evidence_cli.py controls --record tests/fixtures/controls/record.json --mapping tests/fixtures/controls/controls.yaml
```

A state is one of `supported`, `not-supported`, `incomplete` and `not-evaluated`. It says what this
record supports for the narrow claim of the control. It is never a statement that the control works,
and no conclusion about a whole policy follows from counting states.

The run `pass` in short: `boundaries.introduced.verdict` is `PASS`, `boundaries.existing.verdict` is
`NONE`, `baseline.verdict` is `NONE`, `tests.evidence` is `complete`, `coverage.complete` is true with
the parts `.` and `web` checked, `policy.changed` is empty, and `tests.required` holds two passed tests
of the group `billing` and one of the group `catalog`.

## E01: SDLC-03, run `pass`: supported

Control: "Changes are checked against the architecture rules", approved until 2026-12-31, method
`static-analysis`, scope `src/**`, requires the verdicts `boundaries-introduced` and `coverage`.

Reasons: none

Record fields read: `boundaries.introduced.verdict` = `PASS`; `coverage.complete` = true.

A reviewer can conclude: for this change, the check found no new violation of the declared rules and
looked at every part of the repository.

A reviewer cannot conclude: that the declared rules are the right rules, or that anyone read the change.

## E02: SDLC-05, run `pass`: supported

Control: "Billing changes pass the billing tests", approved, method `executed-test`, scope
`src/billing/`, requires the group `billing` of the required tests.

Reasons: none

Record fields read: `tests.required` (group `billing`: `tests/test_invoice.py` passed,
`tests/test_tax.py` passed); `tests.step-outcome` = `success`; `coverage.complete` = true.

A reviewer can conclude: both tests the base commit required for a billing change have a passing record
in the test report of this run, case by case.

A reviewer cannot conclude: that those two tests are enough for billing, or what they assert.

## E03: SDLC-06, run `test-failed`: not-supported

Control: "Every change passes the tests that use what it changed", approved, method `executed-test`,
requires the verdict `tests`.

Reasons: `verdict-failed`

Record fields read: `tests.evidence` = `failed` (a case of `tests/test_invoice.py` failed).

A reviewer can conclude: this run is evidence against the claim for this change: a test that uses what
changed failed.

A reviewer cannot conclude: whether the change was merged anyway, which the code host records.

## E04: SDLC-04, run `baseline-expired`: not-supported

Control: "No architecture violation stays without an accepted exception that has an end date",
approved, method `static-analysis`, requires the verdicts `boundaries-existing` and `baseline`.

Reasons: `verdict-failed`

Record fields read: `boundaries.existing.verdict` = `NONE`; `baseline.verdict` = `EXPIRED`.

A reviewer can conclude: an accepted exception is past its end date at this commit, so its break counts
as a violation again.

A reviewer cannot conclude: who should have renewed or removed the exception.

## E05: SDLC-05, run `test-skipped`: incomplete

Control: as in E02.

Reasons: `required-test-not-passed`

Record fields read: `tests.required` (group `billing`: `tests/test_invoice.py` passed,
`tests/test_tax.py` skipped).

A reviewer can conclude: one required test did not run, so this run does not show that billing changes
pass the billing tests.

A reviewer cannot conclude: that the skipped test would have failed. A skipped test is no evidence
either way.

## E06: SDLC-05, run `docs-only`: incomplete

Control: as in E02. The change edits only `docs/architecture.md`.

Reasons: `required-tests-absent`

Record fields read: `tests.required` (empty: the change touches no path of the group `billing`).

A reviewer can conclude: this run holds no test evidence for the billing control.

A reviewer cannot conclude: that the control "did not apply" to this change. The tool never infers
that: not applicable is a decision someone approves and records.

## E07: SDLC-03, run `docs-only`: incomplete

Control: as in E01.

Reasons: `verdict-not-applicable`

Record fields read: `boundaries.introduced.verdict` = `NOT_APPLICABLE` (no source module changed);
`coverage.complete` = true.

A reviewer can conclude: the boundary check had nothing to judge in this change.

A reviewer cannot conclude: that the control is met for this change. A check that did not apply is not
a check that held.

## E08: SDLC-13, run `web-not-checked`: incomplete

Control: "Web front end changes are checked against the rules of the front end", approved, method
`static-analysis`, scope the part `web`, requires the verdict `boundaries-introduced`.

Reasons: `scope-not-covered`, `verdict-unknown`

Record fields read: `coverage.parts` (part `web`: not checked, `declared-rules-not-read`);
`boundaries.introduced.verdict` = `UNKNOWN`.

A reviewer can conclude: the run did not look at the part this control is about.

A reviewer cannot conclude: anything about the web part from this run, in either direction. In the same
run SDLC-03 names the path `src/**`, which cannot lie in the part `web`; its scope is covered, and it is
incomplete only because its verdicts are unknown.

## E09: SDLC-08, run `pass`: not-supported

Control: "Catalog changes pass the catalog tests", status `draft`, requires the group `catalog`.

Reasons: `mapping-draft`

Record fields read: `tests.required` (group `catalog`: one test, passed). The evidence holds and is
listed; it counts for nothing.

A reviewer can conclude: the mapping of this control is not approved, so the record supports no claim
through it.

A reviewer cannot conclude: that the catalog tests failed. They passed; the mapping is what is missing.

## E10: SDLC-09, run `pass`: not-supported

Control: "API changes are checked against the architecture rules", approved for 2025-01-01 to
2025-12-31, written against edition v2 of the team's policy.

Reasons: `approval-expired`

Record fields read: `generated` (the day 2026-01-02, which `controls.evaluated` repeats);
`boundaries.introduced.verdict` = `PASS`.

A reviewer can conclude: the approval of this mapping ended before the run.

A reviewer cannot conclude: that the control stopped working. The mapping needs a new approval before a
record counts for it again.

## E11: SDLC-10, run `pass`: not-evaluated

Control: "Each change is reviewed by a second engineer", approved, method `human-judgement`.

Reasons: `person-record-needed`

Record fields read: none. The record of a run holds no review.

A reviewer can conclude: this control needs the record of a person, which the tool does not make and
does not stand in for.

A reviewer cannot conclude: that a review took place, or that it did not. The approval of the change is
a record of the code host.

## E12: SDLC-14, run `pass`: incomplete

Control: "Tax rules are covered by tests", approved, method `executed-test`, names no verdict and no
group.

Reasons: `required-evidence-empty`

Record fields read: none.

A reviewer can conclude: the mapping of this control names no evidence, so nothing could support it.

A reviewer cannot conclude: that nothing is required. An empty list is a gap in the mapping, not a pass.

## E13: SDLC-07, run `policy-edited`: not-supported

Control: "A change does not edit the files it is checked by", approved, method `configuration-check`,
requires the verdict `policy-unchanged`.

Reasons: `verdict-failed`

Record fields read: `policy.changed` (`.wingbox/config.yaml`, kind `rules`, changed).

A reviewer can conclude: this change edits a rule file the check is judged by. The findings of the run
were judged with the rules of the base commit all the same.

A reviewer cannot conclude: that the edit was wrong. Someone other than the author has to look at it,
and the record does not show who did.

## E14: SDLC-05, run `mapping-weakened`: incomplete

Control: as in E02. The change itself edits `.wingbox/controls.yaml` and drops the group `billing` from
this control; one billing test is skipped.

Reasons: `required-test-not-passed`

Record fields read: `policy.changed` (`.wingbox/controls.yaml`, kind `controls`, changed), which sets
`controls.mapping.changed`; `tests.required` (group `billing`: one test skipped).

A reviewer can conclude: the change was judged by the mapping of the base commit, which still requires
the billing tests, and one of them did not run.

A reviewer cannot conclude: that the weaker mapping is in force. It counts only after it is merged,
and its own change is listed as a policy change for a reviewer to see.

## E15: SDLC-12, run `pass`: not-evaluated

Control: "Every change of the period has a record", approved, method `operational-record`.

Reasons: `operational-record-needed`

Record fields read: none.

A reviewer can conclude: one run cannot show a period. The ledger and its period report
(`wingbox_evidence_cli.py period`) are the record to read for this control.

A reviewer cannot conclude: anything about the period from this record.

## E16: SDLC-15, run `pass`: not-supported

Control: "Catalog changes are checked against the architecture rules from the second half of the year",
approved from 2026-07-01.

Reasons: `approval-not-yet-valid`

Record fields read: `generated` (the day 2026-01-02); `boundaries.introduced.verdict` = `PASS`.

A reviewer can conclude: the approval of this mapping was not yet in force on the day of the run.

A reviewer cannot conclude: that records made before the first day count later. With `--date` the
command shows what the same record gives on another day, and the first day stays what the mapping says.

## Reading the two lists

For the run `pass` the evaluation ends with:

```
without-evidence: SDLC-10, SDLC-11, SDLC-12, SDLC-14
unmapped-evidence: none
controls: 6 supported, 3 not-supported, 1 incomplete, 3 not-evaluated
```

`without-evidence` names the controls that are incomplete or not-evaluated: three wait for the record
of a person or of operation, one names no evidence. `unmapped-evidence` names what the record holds that
no control asks for: a verdict or a group of required tests nobody mapped. A mapping with only SDLC-03
and SDLC-13 leaves four verdicts and both groups of required tests unmapped, and the command lists them.

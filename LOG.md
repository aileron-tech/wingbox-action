# Log

## 2026-11-19: a control matrix for three regimes (issue #26)

What changed

- `docs/controls/matrix.md` (new): fourteen rows, seven of ISO/IEC 27001:2022 Annex A, four of ISO/IEC 42001:2023
  Annex A, three criteria of the 2017 Trust Services Criteria. Per row: why it concerns a software team, the
  fields a run gives, what is still needed from elsewhere, the limit, the source and the day it was checked. Each
  regime opens with what is not promised. The page carries `Status: DRAFT, not reviewed.` until the maintainer
  signs the review.
- `docs/controls/matrix.controls.yaml` (new): the same rows in the format `wingbox-controls/1`, every control a
  draft.
- `tests/test_matrix.py` (new): REQ-ACTION-235 (the page and the mapping hold the same rows, written out by hand
  in the test), 236 (every row fills its columns, cites a listed source with a day, and the page has its status
  line), 237 (no control of the reference mapping is supported, even with a record whose verdicts all hold).
- README, section "Control mapping": a paragraph that points to both files.

Why

- Issue #26: teams ask which controls the records can support, and without one answer in one place the tool is
  taken for more than it is.

How the identifiers were checked

- ISO/IEC 27001: against the public table of contents of ISO/IEC 27002:2022, which lists every control with its
  number and title. ISO/IEC 42001: against two public crosswalks that agree, not against Annex A itself.
  SOC 2: against the print of TSP Section 100 that includes the updates of March 2020 and a public crosswalk
  headed "Inclusive of 2022 updates". What the reviewer has to confirm against a copy of each standard is
  listed at the end of the page.

Not done

- The review by the maintainer, who signs the page; until then the pull request is a draft.
- Rows worked through on a public sample repository (the issue asks for ten). The sixteen worked examples of
  `docs/controls/examples.md` use an invented team's own controls and name no standard.

Verification

- `python3 -P -m unittest discover -s tests`: see the pull request.

## 2026-11-19: a control mapping and its check against a record (issue #22)

What changed

- `wingbox_controls.py` (new, pure, standard library only, no import of another module of this repository):
  `parse` and `read` (the file `.wingbox/controls.yaml`, format `wingbox-controls/1`, a YAML subset read line
  by line in the style of the manifest of required tests; anything outside the subset refuses the whole file
  with the line), `evaluate` (the `controls` object of a record), `evaluate_control`, `scope_gaps`, `verdict`,
  `state_of`, `counts`, `lines` (the evaluation for a reader) and `ok`.
- States: `supported`, `not-supported`, `incomplete`, `not-evaluated`. Nineteen reason codes, each with a
  fixed state (`REASONS`). Methods: `static-analysis`, `executed-test`, `configuration-check` (evaluated),
  `document-review`, `human-judgement`, `operational-record` (not evaluated). Verdicts a control may require:
  `boundaries-introduced`, `boundaries-existing`, `baseline`, `tests`, `coverage`, `policy-unchanged`.
- `wingbox_evidence.build_evidence`: the record gains `controls`, evaluated at the day of `generated`.
  `wingbox_facts.Report.controls_map`; `wingbox_pr.read_controls` reads the mapping from the base commit when
  it holds it (as `read_manifest` does), else from the checkout; `with_test_results` calls it
  (`WINGBOX_CONTROLS`, else the default place).
- `wingbox_policy`: `.wingbox/controls.yaml` and the path of `WINGBOX_CONTROLS` are policy files of kind
  `controls`; the report names the kind. `wingbox_package`: the same file in `POLICY_FILES`, the kind in
  `POLICY_KINDS`, and `control(record)` fills the `control` object of the manifest.
- `wingbox_evidence_cli.py controls (--record FILE | --package FILE) [--mapping FILE] [--date DAY] [--json]`.
- Schemas: `schema/wingbox-controls-1.schema.json` (new); `controls` and the policy kind `controls` in
  `schema/wingbox-evidence-2.schema.json`; `control` and the policy kind in `schema/wingbox-package-1.schema.json`.
- `action.yml`: the input `controls`. The shell-runner template of GitLab hands `WINGBOX_CONTROLS` to the
  container. `.github/workflows/test.yml`: the job `package` evaluates the fixture mapping against the
  package of its run.
- Fixtures `tests/fixtures/controls/` (the mapping of an invented team, its manifest of required tests, the
  record of one run), `docs/controls/examples.md` (sixteen worked examples), README section "Control mapping".
- Tests REQ-ACTION-210..234 (`tests/test_controls.py`).

Why

- Issue #22: a record did not say which control it is evidence for, and nothing checked that the evidence a
  control names was produced. The rules come from the approved inputs of the design (wingbox#163): a mapping
  describes the narrow claim an artifact supports; a file that exists, an edge of the graph, a test name or a
  command that passed is no evidence that a control operates; nothing is concluded for a framework from a
  count; a draft or unapproved mapping supports no claim; not applicable is never inferred; where the
  judgement of a person is required the tool does not write one.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-172 (`tests/test_package.py`): the `control` object of a manifest without a mapping is
  `{"mapping": "none", "path": "", "sha256": "", "commit": "", "name": "", "version": "", "owner": "",
  "evaluated": "", "controls": []}`; it was a placeholder with the keys id, edition, scope, owner, applies
  and risk, made for one control and never filled. The package form is not released yet.
- REQ-ACTION-172 and REQ-ACTION-174 (`tests/test_package.py`): the policy files of a manifest list
  `.wingbox/controls.yaml` (kind `controls`, here `not-in-commit`) after `.wingbox/config.yaml`.
- REQ-ACTION-187 (`tests/test_package.py`): the command script also imports `wingbox_controls`.
- REQ-ACTION-140 (`tests/test_policy.py`) is unchanged; its sibling check that README names every fixed
  policy file (REQ-ACTION-150) now covers `.wingbox/controls.yaml` too.

Decisions where the issue was open

- The record holds no `reviewer` field: acceptance is a review record of the package (`review`), written by
  a person through the command, not an empty field of the record.
- The evidence a control may name is what a record holds as a verdict (six names, one field each) and the
  groups of required tests. `eligible` is not a verdict a control can require: a team lists the verdicts it
  means. `policy-unchanged` is the one configuration check a record gives.
- A verdict is a word about the whole run and is not narrowed to the scope. The scope decides whether what
  the run left out lies in it, and what counts depends on what the control requires. For every control: a
  named part the run does not name, a tree that is not the commit named, a gap the reader does not know.
  For a verdict that rests on the graph (the boundary verdicts, `tests`, `coverage`): a language left out,
  a stale graph, a tool error, a changed file in the scope left out by `paths`. For a boundary verdict: a
  part that was not checked, when the scope names it or a scope path may lie in it (only two fixed
  beginnings that part ways show that it cannot). A control that requires only groups of required tests
  rests on neither: on a small repository Wingbox checks no boundary rule (`too-few-files`), and a control
  about recorded test cases is not incomplete for that (seen in the end-to-end test, REQ-ACTION-234).
- A group of required tests holds only when the record has a test of the group and all passed. The record
  lists a test once, under the first group that names it, so a group whose tests an earlier group names is
  `required-tests-absent`: name in a control the group that the manifest lists first.
- A change that touches no path of a required group gives `required-tests-absent`, and a verdict
  `NOT_APPLICABLE` gives `verdict-not-applicable`: both incomplete. The tool has no "not applicable" state.
- A mapping the base commit does not hold supports no claim (`mapping-not-in-base`): approved inputs come
  from the protected baseline. By hand (`controls --mapping`) a mapping is evaluated as given, and a line
  says whether it has the bytes the run evaluated.
- Validity is judged at the day of the record (`generated`), or at `--date`; no clock is read. The first and
  the last day are in force.
- Approval fields are checked when a control is evaluated (`approval-incomplete`), not when the file is
  read: one unfinished control does not refuse the mapping of the others. What the file must hold to be
  read at all: title, status, method, claim, limits, a reference, a scope.
- A plain value may not start with a digit (stricter than the manifest of required tests), so a date or a
  version is always quoted and no YAML reader takes it for a date or a number.
- `controls` ends with 1 when a control is not-supported or incomplete or the mapping is refused;
  not-evaluated controls do not make it 1: they wait for a person.
- `controls` is optional in the schema of the record, so a record of 1.5.0 still meets it.

Not done

- No state of a control fails the job, and the comment does not list the controls: the record and the
  command do. An input for that can follow.
- The edition in force of a framework is not checked: the tool does not know which edition a team runs
  against (the assurance profile of wingbox#163). The record repeats the edition of each reference.
- A scoped, approved "not applicable" (wingbox#163) has no form in the file yet.
- The period report does not count states per control.
- A package holds the mapping as the commit checked has it. When the change edits the mapping, the bytes
  that were evaluated (the base's) are named by commit and SHA-256 and are not a member.
- No mapping for a real framework: that is the matrix of issue #26, a reference example in draft status.

Verification

- `python3 -P -m unittest discover -s tests`: 282 tests, OK, 20 skipped (the ones that need Wingbox); before
  the change 257 and 19. With Wingbox 0.15.0 in the Python that runs them (`WINGBOX_JOBS=1`): 282 tests, OK,
  none skipped. `pyright *.py`: 0 errors.
- The end-to-end test (REQ-ACTION-234) runs the whole script with a real Wingbox on a repository whose base
  holds a mapping and whose change weakens it, and a second time against a base without a mapping.
- Order of work: the module and its tests were written together before the first run; the expected states
  and reason codes of the tests are written by hand. The first run of the new tests failed in two of them
  through errors of the tests themselves (a miscounted number of sample values, and a word of the fixture's
  own text taken for a word of the tool); no expectation of a state was changed to make a test pass.
  The end-to-end run then showed a control about recorded test cases as `scope-not-covered` on a repository
  too small for boundary rules; the rule of the scope was changed for that (see the decisions), and the
  expectations of REQ-ACTION-218 were written again by hand for the new rule.

## 2026-11-19: evidence over a period: a ledger and a period report (issue #25)

What changed

- `wingbox_ledger.py` (new, pure; it imports `wingbox_package` for the schema check and the digest): `line`
  (the line of a record), `encode` (JSON with sorted keys and no space), `read` (the chain: each line says its
  number and the SHA-256 of the line before, without its line end), `next_line`, `recorded`, `outcome`
  (`eligible`, `failed`, `unknown`), `period` (the report as a value) and `text` (the report for a reader),
  `moment` and `boundary` (a day or a UTC time as a boundary of a period).
- `wingbox_evidence_cli.py`: `append --ledger FILE (--record FILE | --package FILE)` and
  `period --ledger FILE --from DATE --to DATE [--repo DIR [--branch NAME]] [--max-gap-hours N]
  [--head-sha256 HEX] [--json]`. `append` opens the file to add to; `period --repo` asks git for the commits
  of a branch (`git rev-list --first-parent --parents --timestamp`), nothing else.
- `schema/wingbox-ledger-1.schema.json` (a line), `schema/wingbox-period-1.schema.json` (the report).
- `wingbox_pr.write_ledger`: with `WINGBOX_LEDGER` naming a file both entry scripts add the line of the run
  after the record and the package are written, also for a check that did not run; the output `ledger-head`,
  in GitLab `WINGBOX_LEDGER_HEAD`. `action.yml`: the input `ledger`, the output. Both GitLab templates name
  the variable; the template for shell runners hands it to the container.
- `.github/workflows/test.yml`: the job `package` gives the action a ledger, reads the period with and
  without the fixture repository, and adds the same run a second time.
- Tests REQ-ACTION-190..208 (`tests/test_ledger.py`).

Why

- Issue #25: each run was recorded on its own; nothing collected the records, showed that none was left
  out, or showed the gaps.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-187 (`tests/test_package.py`, added with #24): the command script now also imports
  `wingbox_ledger`, and git is started with `rev-list` too; the test counts the `cat-file` calls apart from
  the `rev-list` one, which REQ-ACTION-205 covers.

Decisions where the issue was open

- "The digest of its package" in a line is the SHA-256 of the manifest, with the bundle id and the state:
  the digest of the archive changes with every review, the manifest's does not, and a review names it too.
- A line is written from the record; its time is the record's `generated`. Neither command reads a clock.
- The last line cannot be protected by a line after it. `append` and `period` print its SHA-256, the action
  gives it as an output, and `period --head-sha256` checks it.
- A record already in the ledger (by its SHA-256) is not added again; that is status 0.
- A run is `failed` when a verdict found a defect (boundaries FAIL, existing PRESENT, baseline EXPIRED, tests
  failed), `unknown` when it is not eligible without one; `eligible` is the word of the record.
- "The same change" is the same pull or merge request of the same repository; outside one, the same head.
  What followed is looked for in the whole ledger, also after the end of the period.
- A commit has a record when a line names it as its head or as the commit checked. A merge commit whose
  merged head a line names is listed apart and is not a defect; a squashed commit has no record unless a
  run checked it.
- The time of a commit is its committer time; the period is taken as given (`--to` as a day belongs to it).
- Defects (status 1) are a broken ledger, a commit without a record and a stretch over the limit. Failed
  and unknown runs are listed and counted, not judged.
- `--branch` defaults to `HEAD` of the repository given.
- A relative `ledger` path of the action starts at the workspace of the job, so both steps that write a
  record name the same file.

Not done

- The tool hosts nothing: fetching the ledger before the job and storing it after is the team's set-up; the
  README shows one way (a branch of its own, one writer at a time).
- No line is written for a pipeline with nothing to compare (GitLab, the first push of a branch): it has no
  record.
- A ledger is of one repository in practice, but nothing enforces it: lines name their repository.
- Health of collection watched apart from the collector, retention, and delivery receipts (the design issue
  on sealing) are not part of this change.

## 2026-11-19: an evidence package, built and checked by a command (issue #24)

What changed

- `wingbox_package.py` (new, pure, standard library only, no import of another module of this repository):
  safe member names (`unsafe`, `safe_name`); the archive in one fixed form (`archive`, and `read`, which
  refuses a link, a compressed member, a comment and bytes outside the members); a schema check
  (`schema_problems`); the manifest (`manifest`, computed from the bytes of the members and what was given:
  retention, stated versions, the policy files asked for, the job log name); `checks` (one outcome per
  required test, per test that uses what changed and per case); `verify`; `review` and `with_review`;
  `git_wanted` and `git_problems` (the comparison with a repository, given the digests); `result` and `lines`.
- `wingbox_evidence_cli.py` (new, the I/O): `pack`, `verify`, `review`; exit status 0, 1, 2. It reads files,
  asks git with `git cat-file` (`-e` for a commit, `blob` for a file), reads the clock only for a review
  without `--time`, and writes the archive (`pack`: a new file only; `review`: beside the old one, then in
  its place).
- `schema/wingbox-package-1.schema.json`, `schema/wingbox-review-1.schema.json`.
- `wingbox_pr.write_package`: with `WINGBOX_PACKAGE` true both entry scripts pack the run folder after the
  record is written, also for a check that did not run; the outputs `package` and `package-state`, in GitLab
  `WINGBOX_PACKAGE_STATE` in `wingbox.env`. `action.yml`: the input `package` and the two outputs. Both
  GitLab templates name the variable; the template for shell runners hands it to the container.
- `.github/workflows/test.yml`: the job `package` (Linux, macOS, Windows): the action with `package: true` on
  the fixture repository, `verify --repo`, the package built again by hand and compared byte for byte, a
  review, `verify` again.
- Tests REQ-ACTION-170..189 (`tests/test_package.py`).

Why

- Issue #24: a run left three loose files; nothing gathered what the record refers to, listed what a
  complete package holds, or verified one. The rules of sealing follow the design of the evidence layer: a
  manifest that covers every member and holds neither its own digest nor a signature, reviews as separate
  records that name the manifest digest, safe handling of archives.

Decisions where the issue was open

- Members are stored without compression: deflate may give other bytes with another zlib, and the same
  inputs must give the same bytes on every machine. The archive form is written and read by this module, not
  by the zipfile module, whose defaults differ by platform.
- `verify` accepts only that form. An archive that was unpacked and packed again by another tool is refused
  as `archive`, also when its members are intact.
- The time in the manifest is the record's `generated`; `pack` reads no clock.
- The policy files are taken from `subject.checked` of the record (the head when the record names no checked
  commit). Which files: the four with a fixed place, the ones `policy.changed` names, `tests.manifest.path`,
  and the ones named with `--policy` and `--workflow`. A file the commit does not have is listed as
  `not-in-commit` and is not a defect; a commit that cannot be read makes the package incomplete.
- Required members follow from the record: its documents and its test reports, each with the digest the
  record gives. The job log is optional.
- The manifest holds what no member holds as stated: `retention`, `stated`, the list of policy files asked
  for. `verify` keeps them as said; an edit of them is caught only with `--manifest-sha256`.
- `review` records a decision only about a package that verifies; an incomplete one can be reviewed.
- An incomplete package fails the step of the action and the GitLab job.
- `control` is marked `"mapping": "none"` with empty values: there is no control mapping yet (#22).

Not done

- No signing and no attestation: the member name `attestation/manifest.json.attestation` is reserved and
  named in the manifest; `verify` reports a member there as present and not checked.
- The action does not pack a job log (a job has no access to its own log) and has no input for the
  retention or the stated versions; `pack --job-log`, `--retention` and `--stated` do it by hand.
- The step of the action that records a check that did not run has no working directory (REQ-ACTION-136: it
  must start also when that folder is not there), so its package reads the commit from the workspace folder:
  with another `working-directory` that package is incomplete (`policy-files` absent), and says so.
- No command unpacks a package. Nothing reads a record of version 1: its package says the record is
  `unreadable`.
- Retention classes, redaction and restore of an old package (the design issue on sealing) are not part of
  this change.

## 2026-11-19: separate verdicts and `eligible` (issue #19)

What changed

- `wingbox_verdicts.py` (new, pure): `existing` (PRESENT, NONE, UNKNOWN with the number and the first),
  `baseline` (ACCEPTED, EXPIRED, NONE, UNKNOWN with the numbers), `eligible` with its reasons, `record` (the
  objects `boundaries`, `baseline`, `eligible`, `eligible-reasons`), `lines` (the six lines of the report),
  `holds_violation`. The result of the boundary check and the word for the test evidence are arguments: the
  module does not own them.
- `wingbox_facts`: `accepted` (the breaks a baseline accepts, as findings with their terms; `known` reads
  from it), `expired` (violations with `:expired`), `existing` (the findings of the head that the base has
  too; one only the change's own policy edit hides stays, unless a complete entry accepts it).
  `Report.existing`, `accepted`, `expired`.
- `wingbox_report`: `FAIL_ON` gains `existing`; `exit_status`; the block "Verdicts" before the plan in the
  comment and the summary; the outputs `eligible`, `existing-violations`, `baseline-accepted`,
  `baseline-expired`. The test verdict is `tests_summary(report)`, which lives in this module since #28.
- `wingbox_pr.fail_on_status` says why `existing` failed; both entry scripts use it.
- `wingbox_evidence.build_evidence` adds the verdict objects; the schema of version 2 describes them.
- Tests REQ-ACTION-160..168 (`tests/test_verdicts.py`).

Why

- Issue #19: a head with a violation from before, with accepted breaks and without any test evidence read
  PASS, and neither the report nor the record held the word violation.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-028: the comment of a change nothing uses is one sentence above its verdicts; it was one
  sentence alone. The six verdict lines are the issue's request.

Not done

- The plan has no verdict lines: its `review-plan` line carries the result as before.
- The comment of a failed build stays the one sentence that says so, without verdict lines; the record of
  that run has them (UNKNOWN).
- A baseline entry without an end date does not make a record ineligible; it is counted
  (`without-end-date`).
- A change of a policy file does not by itself make a record ineligible; `policy` holds it.

## 2026-11-19: the coverage of a run in the evidence record (issue #21)

What changed

- `wingbox_coverage.py` (new, pure): `parts` (the strict account of `wingbox boundaries --strict`), `languages`
  (the `node` facts of the graph by language), `notes` and `graph_state` (FRESH, STALE, UNKNOWN from the
  `built-from` fact, from what the commands print about a stale or unconfirmed graph, and from a failed
  build), `unresolved_calls` (the use file), `left_out` and `ignored` (from git's lists), `reasons`,
  `complete`, and the `coverage` object of the record.
- `wingbox_facts`: `Part`, `Graph`, `Scan`, `Report.scan`; `tree_reasons` moved here from `wingbox_report`
  (the coverage says the same reasons); `findings` leaves `in-no-part` lines out of the parts not checked.
- `wingbox_pr`: `_boundaries` asks with `--strict` and falls back to the plain command when the output has no
  `boundaries-result` line; `_wingbox` keeps the stale notes per folder (`GRAPH_NOTES`); `_scan` reads the
  graph, the use file, `git ls-files` and `git ls-files --others --ignored --exclude-standard --directory`
  after the last query.
- `wingbox_report`: the output `coverage-complete`; the plan lines `coverage` and `coverage-gap` for a report
  with a scan.
- `schema/wingbox-evidence-2.schema.json`: `coverage`. Tests REQ-ACTION-152..159 (`tests/test_coverage.py`).

Why

- Issue #21: a partial run reached the record as free text in `result.reasons` and one number; the files left
  out by `paths` not at all.
- Verified with Wingbox 0.14.0 (a complete and a partial run end to end, a real stale note) and with 0.11.0
  (no `built-from`, no strict mode: the findings are read as before, the coverage says UNKNOWN twice).

Not done

- No count of tracked source files the graph does not hold for another reason than a folder Wingbox leaves
  out: that needs the graph's file list matched against git's, and the endings Wingbox reads per version.
- Reflection and imports built from strings are named, not counted: Wingbox does not count them.
- A graph that misses files makes the coverage incomplete, not the result UNKNOWN (the verdicts of issue #19
  carry it to `eligible`).

## 2026-11-19: a change is judged with the policy files of the base (issue #18)

What changed

- `wingbox_policy.py` (new, pure): the policy files (`FIXED`, the decisions log for the layers it sets, the
  rule files a graph names through `arch-part :declared`, `arch-declared-unread`, `arch-known` and
  `capability-known`, the workflow named by `GITHUB_WORKFLOW_REF` or `CI_CONFIG_PATH`, a manifest of required
  tests named by `WINGBOX_REQUIRED_TESTS`, and any changed workflow file that names wingbox), `policy_changes`, `baseline_entries`, `new_entries`, `default_mode`,
  `policy_status`, and the `policy` object of the record.
- `wingbox_facts`: `Acceptance`, `PolicyChange`, `BaselineEntry`; `Finding.accepted`; `known` (what the head's
  baseline accepts, from `violation-known` and `capability-violation-known`); `judged` (the findings of the
  head under its own policy and under the base's against those of the base). `Report` gains `policy`,
  `policy_mode`, `policy_base`, `self_accepted`, `baseline_entries`, `tests_deleted`.
- `wingbox_pr.collect`: after the two builds it reads the rule files both graphs name, the two sides of each
  candidate file, and, when the change edits the baseline or a rule file, builds a third time in
  `_judge_tree`: a temporary git worktree of the commit checked in which each such file is written as the
  base has it (removed when the base has none); for a working directory the changed files are copied in from
  disk first. A path that a symbolic link of the head leads out of that worktree is not written; the run
  records a tool error. `policy_base`: the merge base, or for a merge checkout the other parent of the merge.
  `tree_state` names a policy file of `.wingbox/` that is not as in the commit (`_policy_on_disk`).
  `policy_mode`, `policy_failed`; both entry scripts add the policy status to the exit status.
- `wingbox_report`: the section of the comment, the plan lines, the reason `accepted-only-by-this-change`,
  `deleted_tests` (asked of the graph of the base, at most 40 changed source files, only when a test file
  is deleted), the output `policy-changed`; `needs_comment` is true for a change of policy files alone.
- `schema/wingbox-evidence-2.schema.json`: the object `policy`, added to version 2.
- Tests REQ-ACTION-140..151 (`tests/test_policy.py`, `tests/test_policy_run.py`, the project of
  `tests/layers_fixture.py` built from text).

Why

- Issue #18: a break of a rule together with a baseline entry for it, or with an edit of the rules, was
  PASS, also with `fail-on: strict`. Reproduced on a four-layer TypeScript project before the change.
- The choice for "the policy of the base": a rebuild of the head with the base's policy files. Reading the
  head's `violation-known` lines against the base's baseline would cover the baseline only; an edit of
  `config.yaml`, of `learned.sexp` or of a declared rule file changes what Wingbox reports in ways only
  Wingbox can compute. The build runs only when such a file changed: on the fixture 4.2 s became 5.4 s.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-102: no expectation changed; the validator of that test now reads the JSON Schema type
  `boolean` (the record has `complete` and `accepts`).

Not done

- No approval is checked: who reviewed the change is the platform's (code owners, branch rules), as the
  README says.
- An edit of the layers in `.wingbox/decisions.sexp` that is on disk and in no commit is not named by
  `tree_state` (the file changes at every build); a committed one is a policy change.
- For a merge checkout the findings of the base still come from the merge base, as before.

## 2026-11-19: a record also when the tests fail or the check does not run (issue #20)

What changed

- Input `test-step-outcome` (`WINGBOX_TEST_STEP_OUTCOME`; `wingbox_pr.step_outcome` reads it, another
  value than `success`, `failure`, `cancelled`, `skipped` ends the run with status 2). `Report.
  test_step_outcome`; `tests_summary`: `failed` after `failure` whatever the reports hold, not complete
  after `cancelled` or `skipped`; `tests_status` and `tests_failed` fail on it; `tests.step-outcome` in
  the record, `(test-step :outcome ..)` in the plan, a sentence in the comment.
- `--did-not-run` for both scripts: `wingbox_pr.not_run_report` (`Report.did_not_run`; `result` returns
  UNKNOWN, error, `check-did-not-run` for it), `did_not_run` writes the record and the outputs. No
  Wingbox, git or repository needed.
- `action.yml`: a last step `did-not-run` with `if: ${{ always() && steps.check.outputs.evidence == '' }}`
  runs `wingbox_pr.py --did-not-run` by the Python that was set up, else the one of the runner; the
  outputs `evidence`, `result`, `execution`, `reasons` fall back on it.
- `examples/evidence-handover.yml`: the test step (`id: tests`, `continue-on-error: true`), the check
  (`if: ${{ !cancelled() }}`, `test-step-outcome`, `test-command`), the upload (`if: ${{ !cancelled() }}`),
  a last step that fails the job. README: the same form.
- GitLab templates: `WINGBOX_TEST_SCRIPT` runs first inside `script` (`tests_status`), the check runs
  (`check_status`), the job ends with the tests' status, else the check's. Without a record after a failed
  check the script is asked for one (`--did-not-run`); when that cannot run, the shell writes
  `{"result": {"boundaries": "UNKNOWN", "execution": "error", "reasons": ["check-did-not-run"]}}`.
- `tests/check_evidence.py`: what a record must say, and its hand-over with the command of the example.
  `.github/workflows/test.yml`: jobs `evidence`, `evidence-received` and `not-run` run these on GitHub.
- Tests REQ-ACTION-135..139 (`tests/test_failing_tests.py`); the GitLab scripts are run by a POSIX shell
  with stand-ins for pip, git, python and docker.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-104: the output `evidence` of action.yml is that of the check step, else of the step that
  records a check that did not run.

Why

- Issue #20: when the test step failed, the check step was skipped and the hand-over job found no
  artifact; the same when the install failed, and in GitLab when `before_script` failed.

Not done

- The red job of the example is not produced in this repository's workflow (a red job would fail the
  checks of every pull request): the workflow checks the condition of the example's last step instead.
- The smallest record the shell of a GitLab job writes names no run; it is written only when the scripts
  of this repository could not be fetched or run.

## 2026-11-19: where a test report comes from (issue #17)

What changed

- `wingbox_tests.py`: `parse_report` gives, beside the cases, what a report states (`Stated`: the
  timestamps of its suites, the commits named by a property or an attribute); `Sidecar`, `parse_sidecar`,
  `sidecar_name`, `sidecar_state`; `Origin` (what the job knows: commit checked, start of the run and its
  tolerance, run, attempt, test command, sidecar required, UTC offset of the machine); `refusals` (tracked
  by git, sidecar differs or is missing where required, modified or dated before the start, another
  commit); `TestReport` carries the refusal and the provenance. `parse_junit` is `parse_report(...)[0]`.
- `wingbox_pr.py`: `run_started` (GitHub: the modification time of the event file of the job; GitLab:
  `CI_PIPELINE_CREATED_AT`, else `CI_JOB_STARTED_AT`, with 300 s of tolerance), `test_origin`,
  `tracked_files` (`git ls-files`), `read_sidecar`, `utc`; `read_test_results(folder, patterns, origin)`
  judges each report and leaves the cases of a refused one out (without an origin it reads as before);
  a pattern never takes a `.run.json` file for a report; `github_run` reads `GITHUB_RUN_ATTEMPT` and
  `GITHUB_JOB`, `gitlab_run` `CI_JOB_NAME`.
- `wingbox_evidence.py`: `Run.attempt`, `Run.job`; `run.attempt`, `run.job`, `tests.provenance` and the
  provenance of each report in the record (additions to schema version 2). `wingbox_report.py`: plan
  lines `test-provenance`, `test-command`, `test-report-refused`, `test-report-origin`; the comment names
  refused reports; output `test-reports-refused`.
- Inputs `test-command`, `require-test-provenance`; the shell-runner template hands over
  `WINGBOX_TEST_COMMAND`, `WINGBOX_REQUIRE_TEST_PROVENANCE`, `CI_PIPELINE_ID`, `CI_PIPELINE_CREATED_AT`,
  `CI_JOB_STARTED_AT` and `CI_JOB_NAME`.
- Tests REQ-ACTION-130..134 (`tests/test_report_provenance.py`).

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-102: `run` of the record has `attempt` and `job`.
- The validator of `tests/test_evidence_record.py` reads the JSON Schema type `boolean`
  (`tests.provenance.sidecar-required`).

Why

- Issue #17: a report written for another commit, by an earlier attempt or long ago, or committed to the
  repository, gave the same `passed` evidence as the report of the test step of the run.

Not done

- GitHub gives a job no time for the start of the run, so the start is that of the job: a report of an
  earlier job of the same run is refused unless its sidecar names the run.
- The test command of a sidecar is recorded and not compared with `test-command`.
- The host a report names is not recorded.

## 2026-11-19: no test found is `none-found`; a manifest of required tests (issue #16)

What changed

- `wingbox_report.tests_summary` (moved from `wingbox_evidence`, which still exports it): `none-found`
  when test results were given, no test uses what changed and none is required; required tests and a
  manifest that could not be read count. `tests_status(report, require, strict)`: a required test without
  a passed record and an unreadable manifest fail; no test found fails only with `strict` and source code
  in the change (`holds_source`). `outputs`: `tests-evidence`, `tests-required`, `tests-required-passed`.
- `wingbox_tests.py`: `parse_manifest` (a strict reader of a YAML subset: groups, `paths`, `tests`, one
  item per line; every refusal names its line), `Group`, `Manifest`, `Required`, `required_for` (the
  groups a changed file touches), and in `evidence` a test named `<where>::<case>`.
- `wingbox_pr.read_manifest`: the file as the base commit holds it (`git cat-file blob <merge base>:<path>`),
  else the file of the checkout; `with_test_results` stores the manifest and the required tests of the
  change; `tests_failed` says each reason (and takes `strict`).
- Plan: `required-test`, `required-tests`, `required-tests-error` and `(tests :evidence <word>)` lines;
  comment: "Tests that use what changed: none found." and "Required tests (...)"; record: `tests.required`,
  `tests.manifest`, `none-found` (additions to schema version 2).
- Input `required-tests`, `require-tests: strict`; `WINGBOX_REQUIRED_TESTS` in the shell-runner template.
- Tests REQ-ACTION-117..129 (`tests/test_required_tests.py`).

Why

- Issue #16: a change no test uses, and test results without a case, read `complete`, and the tests a
  control requires could not be declared.

Not done

- The check does not look whether a required test file exists in the commit: a required test that is not
  in the reports is `no-record` either way.
- A change of the manifest itself is not flagged here (the policy files are issue #18); the record says
  which commit the manifest was read from.

## 2026-11-19: test evidence per test case (issue #15)

What changed

- `wingbox_tests.py`: `Case` keeps the name of the case; `Evidence` keeps every case of a test to run as
  `CaseResult(where, name, status)`; `_status` gives a test `passed` only when every recorded case passed,
  and the new `partial` when a case passed and another was skipped or has no record. `belongs` no longer
  gives a Go package record to every `_test.go` file of the folder: `in_go_file` matches it by the test
  function of the case (the name before the first `/`) against the functions of the file, and `evidence`
  adds a `Test...` function of the file that no report names as a case `no-record` (`TestMain`,
  benchmarks and helpers are not expected). `go_candidates` names the tests to ask about.
- `wingbox_facts.py`: `go_functions` reads the `fn` lines of `wingbox module <id>` of a Go module;
  `Report.test_functions`. `wingbox_pr.go_test_functions` asks Wingbox (one `wingbox module` for each Go
  test to run whose folder a report names as a package); `with_test_results` stores the answer.
- `wingbox_report.py`: `test_evidence` hands the functions over; `run-test` lines count the cases by
  status when they differ; a `test-case` line for every case, after the `run-test` lines; the comment
  counts `partial` apart ("1 with a case skipped or not run (`x`: 1 of 2 cases passed)").
- `wingbox_evidence.py`: `SCHEMA = "wingbox-evidence/2"`; each test to run carries `records`.
  `schema/wingbox-evidence-2.schema.json` (new); the file of version 1 stays for records of 1.4.
- Tests REQ-ACTION-108..116 (`tests/test_test_cases.py`), the last one with a real Wingbox on a Go
  repository.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-097: the Go test file is matched with its functions given (`TestBill`); without them a
  package record no longer counts for it.
- REQ-ACTION-102: the record says `wingbox-evidence/2`, is checked against the schema of version 2, and
  each test to run has `records`.

Why

- Issue #15: a file with one passed and one skipped case read `passed`, and one Go test function that ran
  marked every test file of its package `passed`.

Not done

- Outside Go the check does not know which cases a test file should have: a case that is in no report is
  not seen (the manifest of required tests of issue #16 names them).
- A Go test file in the root folder of the repository gets no package record (the package path names no
  folder to match).

## 2026-11-19: the end-to-end tests run in CI

What changed

- `.github/workflows/test.yml`: the unit job installs the default Wingbox release
  (`scripts/bump_version.py --show`, new) before the tests, so the tests marked "wingbox is not installed"
  run on the three systems.
- `tests/test_action.py` REQ-ACTION-021: the output folder holds `wingbox-evidence.json` too (the
  expectation was of 1.3.0; the behaviour is the one released in 1.4.0).
- `tests/test_test_evidence.py` REQ-ACTION-107: the whole script with test reports of the job, on a real
  repository: evidence passed, then no-record with `require-tests` failing the job.

Why

- A local run with a real Wingbox after the 1.4.0 release found the stale expectation; CI had not run it.

## 2026-11-19: the check on this repository's own pull requests

What changed

- `.github/workflows/wingbox.yml`: the released action (`@v1`) comments on every pull request of this
  repository. Default settings; it fails no job.

Why

- The maintainer asked for the check on our own builds.

## 2026-11-19: five review remarks (issues #7 to #11)

What changed

- #7: `wingbox_pr._wingbox` starts `python -P -m wingbox`; `action.yml` installs with `python -P -m pip`;
  the shell-runner template uses `python -P -m venv` and `-P -m pip`. Tests REQ-ACTION-084, 085
  (`tests/test_safe_start.py`): a repository with `wingbox/` and `pip/` that leave a mark when run, and a scan
  of the scripts and workflow files for `-m` without `-P`.
- #8: `_wingbox` reads `(skipped-language <name> :files N` from what a command prints (queries print it on
  stderr) into `SKIPPED_LANGUAGES`; `Report.skipped_languages`; `result` returns UNKNOWN with the reason before
  NOT_APPLICABLE and PASS; a sentence in the comment, a line in the plan; the install is `wingbox[all]`.
  Tests REQ-ACTION-086..089 (`tests/test_skipped_language.py`).
- #9: `Tree` and `relation` (pure, `wingbox_facts`), `tree_state` and `commit_hashes` (git, `wingbox_pr`):
  the commit of the working folder, its relation to the head named, the files that differ from it (asked
  before the first build; `.wingbox/`, the output folder of the GitLab wrapper, ignored files and untracked
  files that are not source do not count). The hashes come from `git cat-file blob <commit>:<path>`; the
  working-directory mode keeps the digests of the files on disk. Tests REQ-ACTION-090..094
  (`tests/test_checked_commit.py`).
- #10: `wingbox_tests.py` (new, pure): `parse_junit`, `belongs`, `evidence`. `wingbox_pr.read_test_results`
  and `with_test_results` read the files of `WINGBOX_TEST_RESULTS` (from `WINGBOX_TEST_RESULTS_ROOT`, else
  the repository). `wingbox_report`: `test_evidence`, `evidence_counts`, `tests_status`, `outputs` (the
  outputs both entry scripts write). Inputs `test-results`, `require-tests`; outputs `tests-passed`,
  `tests-failed`, `tests-without-record`. Tests REQ-ACTION-095..101 (`tests/test_test_evidence.py`).
- #11: `wingbox_evidence.py` (new, pure): `build_evidence`, `tests_summary`, `Run`;
  `schema/wingbox-evidence-1.schema.json`; `wingbox_pr.write_evidence`, `github_run`,
  `wingbox_gitlab.gitlab_run`; output `evidence`; `examples/evidence-handover.yml`. Tests
  REQ-ACTION-102..106 (`tests/test_evidence_record.py`): the record meets the schema (checked by a small
  validator of the part of JSON Schema the file uses), and the hand-over command of the example runs
  against a local stand-in server.

Expectations of existing tests that changed with the behaviour

- REQ-ACTION-067: the templates install `"wingbox[all]==$WINGBOX_VERSION"`.
- REQ-ACTION-069: the stand-in for a failing build reads the command after `-P -m wingbox`.
- REQ-ACTION-023: the plan's `changed-file` line names the commit and the head line ends with
  `:checked <head> :tree head`.
- REQ-ACTION-058: `run-test` lines end with `:evidence none`.

Why

- A review of 1.3.0 with Wingbox 0.12: the check could run a module of the checked repository; an
  incomplete analysis of a mixed-language repository could pass in strict mode; the file hashes were not
  tied to the commit named; a recommendation to run tests is not evidence that they ran; no public,
  tested path from the check to a system that keeps evidence.

Not done

- No adapter for a particular compliance product: the record and the hand-over example are neutral.

## 2026-11-18: the check of a working directory

What changed

- `wingbox_pr.settings`: keys `--root`, `--base`, `--head`, `--out` before the
  environment variables, the pull request event and the defaults; with `--base`
  as a key and no head anywhere the head is `WORKING_TREE`.
- `wingbox_pr.collect` with `WORKING_TREE`: the names and hunks come from
  `git diff <merge base>` (index and working files) plus the untracked files
  (`git ls-files --others --exclude-standard`, each one hunk of added lines by
  `wingbox_changes.added_file`); the graph of the head is the folder on disk,
  the graph of the base a worktree at the merge base, as before; hashes from
  the files on disk. No commit, the index untouched.
- Tests REQ-ACTION-080..082 (`tests/test_working_tree.py`), the last one end to
  end with wingbox on a repository with an uncommitted edit and an untracked
  file, run from another directory.

Why

- The maintainer: the check must take the working directory by a key. His
  agents review a change before the commit; to check it they had to clone the
  repository and make a snapshot commit.

## 2026-11-18: a changed method, not the whole class

What changed

- `wingbox_facts.Item.methods`: the method names of a class, from the
  `(:methods ...)` list of its fact.
- `wingbox_changes.methods_of`: the methods of a class as items of their own,
  found in the layout: a line at the level of the class's members that
  defines one of those names (`def name`, `async name(`, `name = (`,
  `name<T>(`, `Type name(`); `spans` adds them, so the innermost holder of a
  changed line is the method. A field or the constructor stays the class.
- `wingbox_pr.changed_items`: the users of a method are those of
  `wingbox usages Class.method` that also use the class (one more query per
  class); without an answer for the method the class is reported as before.
  Overloads are one item.
- Tests REQ-ACTION-073..078 (`tests/test_methods.py`).
- Checked on a public NestJS-style server (707 files): a change inside one
  method of a service class named 1 module outside the tests with the exact
  call line, where the class as a whole has 6 users.

Why

- A run of the check on real service-class code (NestJS, Python services):
  a change inside one method was reported as the class with every user of the
  class, so the lists were long and mostly not affected by the change.

## 2026-11-18: the review plan, the tests to run, the strict result

What changed

- `wingbox_report.tests_to_run`: the test modules that use a changed item, and
  for a file taken as a whole the tests that use it directly; the comment
  names up to five in one line ("Tests that use what changed"), the job
  summary all. The per-item count of test modules is gone.
- `wingbox_report.changed_tests` and `wingbox_changes.removes_code`: a changed
  test that uses a changed source file directly and loses or replaces a line
  of code is paired with that file; the comment has one line for them.
- `wingbox_report.result`: PASS / FAIL / UNKNOWN / NOT_APPLICABLE, an
  execution status and reasons. `wingbox_pr` records a wingbox command that
  ends abnormally (`TOOL_ERRORS`) and the report then cannot be a PASS.
  `fail-on: strict`; outputs `result`, `execution`, `reasons`.
- `wingbox_report.build_plan`: the review plan as S-expression lines (schema
  1), written to `wingbox-plan.sexp` (output `plan`), shown whole in the job
  summary and folded in the comment (60 lines, then a count) when there is
  something to review. `Report.hashes`: SHA-256 of each changed file at the
  head.
- Tests REQ-ACTION-052..059 (`tests/test_review_plan.py`); REQ-ACTION-048,
  030 and the end-to-end test follow.

Why

- Pains named by people who review agent-written changes: a worker ran only
  the tests of the file it edited and a dependent module's test failed later;
  a test bent to pass is hard to tell from a wrong test; nobody reads a large
  diff. And a customer of the audit use: what was not checked must never read
  as passed. The maintainer: do all of it.

## 2026-11-18: the comment names the item that changed

What changed

- `wingbox_changes.py` (new, pure): reads `git diff -U0` into hunks per file;
  gives each changed line of code to the innermost item that holds it (the
  item's first line from `wingbox module`, its end from the indentation, the
  attribute and decorator lines above it included); comments and blank lines
  change nothing; the lines of a Rust `#[cfg(test)]` module are tests; code in
  no item is "other code", never blamed on the item above.
- `wingbox_facts.py`: `Item`, `Site`, `User`, `ChangedItem` (with `via`),
  `ChangedFile.items` and `other_code`; `items()` reads `wingbox module`,
  `use_sites()` and `users()` read `wingbox usages --sexp`.
- `wingbox_pr.changed_items`: for each touched item the users outside the pull
  request; an item of the same file that calls a touched item is followed too
  (calls only, at most 3 steps), carrying the names it goes through. 60
  queries a pull request; an ambiguous name counts as other code.
- `wingbox_report`: the heading and a block per item ("`X` changed" or "`X`
  calls `Y`, which changed", then who uses it and where); test modules are
  counted, not listed; the modules that use the file but not what changed go
  into the folded block; with nothing used outside, one sentence. A module
  named `tests` is a test module.
- Tests REQ-ACTION-034..051 (`tests/test_changes.py`,
  `tests/test_changed_items.py`, `tests/test_changed_items_gathering.py`); the
  fixture's expected report and the end-to-end test follow.
- Replayed on a public Rust commit whose diff touches one private function:
  before, the comment listed the 6 modules that import the file; now it names
  the two public functions that call the changed one and the 4 modules that
  call those, each call site checked against the source.

Why

- The maintainer looked at the comment on a live pull request that changed
  two comment lines and read "7 modules use what it changes": true of the
  file, false of the change. His words: for this one only has to ask usages.

## 2026-11-18: the comment says the thing first

What changed

- `wingbox_report.build_comment`: a heading in a full sentence ("N modules
  outside this pull request use what it changes", with the new boundary
  findings named first when there are any) and a first paragraph that names
  the changed files those modules use and why it matters; then the findings,
  then the modules. Without a direct user and without a finding the comment is
  one sentence and has no heading.
- The diagram and the counts (facade users, farther modules, tests, files
  outside the graph) go into one `<details>` block in the comment, with a
  summary line that says what it holds; the job summary (`full`) shows them
  unfolded. `cut` closes a folded block it leaves open, so the notice and the
  footer are never hidden.
- A row omits the changed file when every listed module uses the same single
  file; "No new boundary finding." replaces the section with "None.".
- The last line links Wingbox to https://wingbox.dev and says that the check
  ran on the runner without a language model (REQ-ACTION-033); the examples'
  last lines follow.
- Tests REQ-ACTION-026..032 (`tests/test_comment_form.py`). The tests that
  pinned the old wording (REQ-ACTION-004, 005, 010, 011, 012, 014, 019, 020,
  022 and `tests/check_fixture.py`) are moved to the new wording in the same
  change: the requirement itself changed, on the maintainer's word.
- `examples/` replayed with wingbox 0.10.1 (the same commits; the diagrams are
  byte for byte the same) and the README follows.

Why

- The maintainer read the first comment on a real pull request and could not
  tell what it was for or how to read it: the first line was a row of counts,
  the findings sat at the bottom, and the diagram took the screen. A comment
  that appears on every pull request is read only if its first line is worth
  reading.

Not in this change

- Saying which item changed ("`load_session_timeline` changed; five modules
  outside the pull request use it") needs the extent of each item from
  Wingbox; it is queued there.

## 2026-11-18: Wingbox 0.10.1, the reasons, the diagram

What changed

- The script is split by duty: `wingbox_facts.py` reads the S-expression facts
  of `wingbox impact --sexp` and `wingbox boundaries`, `wingbox_report.py`
  builds the Markdown report, `wingbox_diagram.py` the Mermaid diagram (all
  three pure), and `wingbox_pr.py` keeps the I/O (git, the wingbox commands,
  the runner files, the GitHub API).
- The facts of Wingbox 0.10 are read whole: per dependent the module it is
  reached through (`:through`, `:more`) and the name lists of its edge
  (`:use`, `:reexport`, `:glob`, `:member`, `:macro`, with `:of N`). The
  impact query asks for every dependent (`--all`).
- A direct dependent says what it uses from each changed file (at most three
  names and a count). A dependent of a changed facade or of the parent module
  of a changed file is a direct use when its fact names items; without names
  it stays counted with the facade users.
- The blast radius is drawn as a Mermaid flowchart inside the comment and the
  job summary: changed files, direct dependents (solid), farther ones and
  facade users (dashed), grouped by folder, arrows from a module to what it
  uses, generated node identifiers, escaped labels, a fixed order.
- Caps: 40 boxes and 60 arrows. Over a cap the farther modules are counted by
  folder, then by top folder, then the direct ones by folder, then the changed
  files by folder, then everything by top folder; what still does not fit is
  left out and counted in the legend.
- The comment is cut to GitHub's limit of 65,536 (measured in UTF-8 bytes) at
  a line, never inside a code block; the job summary lists every direct
  dependent and finding, which the comment now says truthfully.
- `upload-map` (off by default) uploads `.wingbox/map.html` as the workflow
  artifact `wingbox-map`; the script copies the map out of the hidden folder
  into the runner's temporary folder first.
- A module ID named like a test (`web/a.spec`) is counted as a test; before,
  its last dotted part was cut as if it were a file extension.
- The test workflow checks the fixture report with `tests/check_fixture.py`
  (its inline expectations were those of an older report format).
- Default wingbox release 0.10.1; README rewritten for the reader; examples
  replayed with 0.10.1.

Why

- The owner asked for a picture in the comment. A comment cannot carry an
  uploaded image through the API, and the action must not write into the
  repository or send anything elsewhere, so the picture is text that GitHub
  draws.
- Wingbox 0.10 says why a file is on the impact list; the comment passes that
  on instead of naming only the file.

Why 40 boxes and 60 arrows

- GitHub fits a diagram to the width of the comment: a wider one is scaled
  down, labels included. Rendered with Mermaid 11.12.3 in a column of 900
  pixels, the replays with 14, 20 and 23 boxes are 672, 698 and 733 pixels
  wide (774 to 1,022 high). A deliberately tangled diagram of 40 boxes and 52
  arrows is 900 wide and 906 high: it fills the width at full label size and
  each arrow can still be followed. More boxes would shrink the text; more
  arrows would hide each other.
- The six replays that have a diagram need 12 to 24 arrows for 13 to 24
  boxes, so the caps do not touch an ordinary pull request. Mermaid's own
  limits (500 arrows, 50,000 characters) are far above.

Verification

- `python -m unittest discover -s tests`: 46 tests pass on Python 3.11, 3.12
  and 3.14 (2 end-to-end tests skipped without wingbox); with wingbox 0.10.1
  installed all 46 run, the script included, on the fixture repository.
- pyright: no error in the four modules, the tools, the scripts and the new
  tests.
- Ten commits of a public Rust project replayed as pull requests with
  wingbox 0.10.1; two of them replayed twice, byte for byte the same. Every
  "uses ... from ..." line of three replays checked against the source: 29
  of 29 held.
- Diagrams rendered locally with Mermaid 11.12.3 in headless Chrome, light
  and dark, and one with hostile names.
- Not verified: a run on GitHub (the rendering in a comment and in the job
  summary, the artifact upload, the three runner systems).

## 2026-11-18: GitLab CI

- Why: the owner asked for the check in GitLab (merge requests and CI). What: `wingbox_gitlab.py` picks the
  two commits from GitLab's variables (merge request diff base, or the commit before a push), builds the
  report with the functions of `wingbox_pr`, writes `wingbox-out/` (report, plan, dotenv) and, with a token,
  one merge request note found by the marker; `gitlab/wingbox.gitlab-ci.yml` is the job to include.
- Found while running it locally with a Python that has no wingbox: `_wingbox` accepted exit 1 of
  `wingbox build`, so a build that never ran gave "no source module changed; nothing to check" with
  execution ok. A build must end with 0 now; the comment says the change could not be checked.
- Tests REQ-ACTION-060..070 (tests/test_gitlab.py). Run by hand on a public Rust repository as a merge
  request pipeline against a local stand-in for the notes API: note created, then updated on the second
  run; the headline equals the one the GitHub run of the same commits gives.
- First runs on a real GitLab (17.11, two shell runners), in a private test project with a copy of a
  public Rust repository. The template with `image:` failed at once (a shell executor ignores the image;
  the machine had Python 3.10 without pip), hence `gitlab/wingbox-shell.gitlab-ci.yml`: the check runs in
  a container as the runner's user on its own clone. Then, all green: a push that changes only CI
  configuration (NOT_APPLICABLE, 10 s), a merge request (note created, 14 s; updated in place on the
  second push), the merge to the default branch (push pipeline, report in the log, artifacts and the
  dotenv report). GitLab kept serving the old template from a branch address for some minutes: the
  trial pins the include and the scripts to a commit, and the README says how.
- The report says "merge request" in GitLab; `--root`, `--base`, `--head`, `--out` keys.

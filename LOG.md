# Log

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

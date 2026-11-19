# Changelog

## Unreleased

### Changes

- Test evidence is kept per test case. The plan has a `test-case` line for
  every case the test reports hold of a test to run (the test, where the
  report puts the case, its name, its status), and the evidence record lists
  the same under `records` of each test.
- New status `partial` for a test to run: a case passed and another was
  skipped or did not run. Its `run-test` line counts the cases by status, the
  comment names it ("1 with a case skipped or not run"), the output
  `tests-without-record` counts it and `require-tests` fails on it.
- The evidence record is `wingbox-evidence/2`
  (`schema/wingbox-evidence-2.schema.json`): each test to run gains `records`,
  and its status may be `partial`.

### Fixes

- A test file with a passed and a skipped case was `passed`. A test is now
  `passed` only when every case recorded for it passed.
- One record of a Go package counted for every test file of its folder. A
  record now goes to the file that holds its test function (Wingbox lists the
  functions of each Go test file), a test function of the file that no report
  names is a case `no-record`, and Go tests named as modules are matched too.

## 1.4.2 - 2026-11-19

### Changes

- The default Wingbox release is 0.14.0. The check asks Wingbox for the same facts as before
  (`--sexp`), so reports do not change.

## 1.4.1 - 2026-11-19

### Changes

- The default Wingbox release is 0.13.0: the parser of every language is part of the package
  again, edges of the graph carry their source line, and the graph names what it was built from.

### Fixes

- The repository's own tests: the end-to-end tests (the whole script with a real Wingbox) now run
  in the unit job on Linux, macOS and Windows; they were skipped there. One of them still expected
  the output folder of 1.3.0, without `wingbox-evidence.json`.

## 1.4.0 - 2026-11-19

### Changes

- A change inside one method of a class is reported as that method
  (`Class.method`) with the modules that use the class and call it, not as a
  change of the whole class with every user of the class.
- Next to the parts that were not checked the report says on how many modules
  the boundary rules were checked.
- Check a working directory before a commit: `python wingbox_pr.py --root DIR
  --base REF [--head REF] [--out DIR]`. With `--base` and no head the change is
  the base against the files on disk, uncommitted and untracked ones included;
  nothing is committed.
- The report names the commit whose files were checked, and its file hashes
  are of that commit. The plan's head line gains `:checked <commit> :tree
  head|merge|other`, each `changed-file` line names the commit its SHA-256 is
  of (the digest of `git show <commit>:<path>`), and the comment says when the
  pull request was checked as merged into the base. A checkout that is neither
  the head nor its merge, or that holds a changed tracked file or an untracked
  source file, makes the result `UNKNOWN`
  ([#9](https://github.com/aileron-tech/wingbox-action/issues/9)).
- Evidence that the tests ran: the input `test-results` takes the JUnit XML
  reports the job's test step wrote. Each test that uses what changed then
  carries what the reports hold about it (`:evidence passed`, `failed`,
  `skipped`, `no-record`; `none` without the input), each report file is named
  with its SHA-256, the comment counts them, and the outputs `tests-passed`,
  `tests-failed` and `tests-without-record` hold the numbers. `require-tests:
  true` fails the job unless every such test has a passed record. The check
  still runs no test itself
  ([#10](https://github.com/aileron-tech/wingbox-action/issues/10)).
- An evidence record of each run: `wingbox-evidence.json` (output `evidence`,
  in GitLab in `wingbox-out/`), with the run, the commits, the result and its
  reasons, the changed files and their SHA-256, the tests to run and their
  evidence, and the SHA-256 of the report and the plan. Its shape is
  `schema/wingbox-evidence-1.schema.json`. `examples/evidence-handover.yml`
  hands it to another system from a job that never checks out the code
  ([#11](https://github.com/aileron-tech/wingbox-action/issues/11)).
- The action installs Wingbox with the parser of every language
  (`wingbox[all]`).

### Fixes

- A folder of the checked repository named `wingbox/` or `pip/` was run in
  place of the installed module: the check started Python as `python -m ...`
  inside the checkout, and Python puts the starting folder first on its module
  search path. Every Python the check starts now gets `-P`: the Wingbox
  commands, the install step, both GitLab templates. The check reads the
  repository and never runs it
  ([#7](https://github.com/aileron-tech/wingbox-action/issues/7)).
- With a Wingbox release that leaves a language out because its parser is not
  installed, the files of that language were missing from the report without a
  word, and the result could be `PASS` or `NOT_APPLICABLE`, also with
  `fail-on: strict`. The report now says how many files of which language were
  not analysed, the plan holds `(skipped-language <name> :files N)`, and the
  result is `UNKNOWN`
  ([#8](https://github.com/aileron-tech/wingbox-action/issues/8)).

## 1.3.0 - 2026-11-18

### Changes

- GitLab CI: `gitlab/wingbox.gitlab-ci.yml` (Docker and Kubernetes runners)
  and `gitlab/wingbox-shell.gitlab-ci.yml` (shell runners: the check runs in
  a container and writes only `wingbox-out/` on the runner) run the same
  check for merge requests and for pushes to the default branch. The report goes to the job
  log, to artifacts (`wingbox-out/`) and, with a project access token in
  `WINGBOX_GITLAB_TOKEN`, to one note on the merge request that is updated on
  each push. The result and the counts are handed to later jobs as `WINGBOX_`
  variables.

### Fixes

- A `wingbox build` that fails (wingbox not installed for the Python that runs
  the check, a locked project) is a tool error: the result is UNKNOWN and the
  comment says the change could not be checked. It used to read as "no source
  module changed; nothing to check".

## 1.2.0 - 2026-11-18

### Changes

- The comment names the tests that use what changed, and says when a test
  changes together with the code it tests.
- A review plan for a reviewing agent: the changed items, the places that use
  them, the tests to run and what was not checked, as S-expression lines in
  the order to check; in a folded block of the comment and in a file (output
  `plan`).
- A strict result as outputs (`result`, `execution`, `reasons`): PASS, FAIL,
  UNKNOWN or NOT_APPLICABLE. A part that was not checked or a tool error is
  UNKNOWN, never PASS. `fail-on: strict` fails the job on FAIL and UNKNOWN.
- The plan names the SHA-256 of each changed file at the head commit.
- The default Wingbox release the check installs is 0.11.0 (input `version`).

## 1.1.0 - 2026-11-18

### Changes

- The comment names what changed, not only which file: the function or type
  that holds the changed lines, and the modules outside the pull request that
  use it, with the place of each use. A changed private helper is reported
  through the public functions that call it.
- A change that nothing outside the pull request uses (a comment, a blank
  line, a test kept in a Rust file, a private item nobody calls) gives one
  sentence instead of a list of every module that imports the file.
- Code outside the items Wingbox lists (an import, an `impl` block) still
  lists the modules that use the file.
- The last line of the comment links Wingbox to its site and says that the
  check runs without a language model.

## 1.0.1 - 2026-11-18

### Changes

- The comment says the thing first: a heading and a sentence with how many
  modules outside the pull request use what it changes, then those modules.
  When nothing outside the pull request uses the changed files and there is
  no new finding, the comment is one sentence.
- New boundary findings lead the comment and its heading; the empty "None."
  section is gone ("No new boundary finding." in one line).
- The diagram, the modules farther away, the tests and the files outside the
  code graph are folded under one "More" line in the comment; the job summary
  shows them unfolded.
- A row no longer repeats the changed file when every listed module uses the
  same one.

## 1.0.0 - 2026-11-18

### Changes

- First release: on `pull_request`, one comment (updated on each push) and a
  job summary with the modules that depend on the changed files within N hops
  and are not changed, the boundary findings the pull request introduces, and
  the parts that could not be checked.
- Each direct dependent says what it uses from the changed file (the names
  Wingbox 0.10 records, at most three and a count).
- The blast radius is drawn as a Mermaid diagram inside the comment and the
  job summary: changed files, direct dependents, farther ones dashed, grouped
  by folder, at most 40 boxes and 60 arrows (larger ones are collapsed into
  counted boxes).
- The comment names at most ten direct dependents; the job summary lists them
  all. A comment over GitHub's limit of 65,536 characters is cut at a line.
- Inputs `version` (default wingbox 0.10.1), `hops`, `fail-on`, `comment`,
  `upload-map` (the interactive map as a workflow artifact, off by default),
  `paths`, `python-version`, `base`, `head`, `working-directory`,
  `github-token`; outputs `report`, `map`, `untouched-dependents`,
  `introduced-findings`, `not-checked-parts`.

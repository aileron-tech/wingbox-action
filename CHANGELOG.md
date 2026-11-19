# Changelog

## Unreleased

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

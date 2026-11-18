# Changelog

## Unreleased

### Changes

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

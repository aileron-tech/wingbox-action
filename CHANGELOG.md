# Changelog

## 1.0.0 - unreleased

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

# Log

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

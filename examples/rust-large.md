<!-- Wingbox Pull Request Check on a fifteen-file commit of a public Rust project, replayed as a pull request against its parent, built locally with wingbox 0.10.1 and tools/replay.py; not posted anywhere. -->

## Wingbox: 9 modules outside this pull request use what it changes

These modules use `src/app/mod.rs`, `src/domain/types.rs` and 2 more, which this pull request changes. They are not in the pull request, so a change in behaviour reaches them without showing in the diff:

- `src/app/fork` uses `ForkContext`, `ForkCut`, `SessionEngine` and 2 more from `src/domain/types.rs`
- `src/app/mouse` uses `AppCommand`, `AppData`, `AppEvent` and 11 more from `src/app/mod.rs`; `ProjectSummary` from `src/domain/types.rs`
- `src/domain/parse` uses `ProjectSummary`, `SessionEngine`, `SessionMeta` and 1 more from `src/domain/types.rs`
- `src/domain/stats` uses `SessionMeta` from `src/domain/types.rs`
- `src/infra/claude` uses `SessionEngine`, `SessionMeta`, `SessionSummary` from `src/domain/types.rs`
- `src/infra/codex_fork` uses `ForkCut` from `src/domain/types.rs`
- `src/infra/scan` uses `SessionEngine`, `SessionSummary` from `src/domain/types.rs`
- `src/infra/session_detail` uses `is_gemini_session_path`, `load_gemini_last_assistant_output`, `load_gemini_session_timeline` from `src/infra/gemini.rs`
- `src/infra/session_index` uses `SessionEngine`, `SessionSummary` from `src/domain/types.rs`; `read_tail` from `src/infra/processes.rs`

<details>
<summary>More: the picture, 3 through a facade or declaration, 2 within 2 hops, 2 files outside the code graph</summary>

### Blast radius

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 12, "rankSpacing": 60, "padding": 8}}}%%
flowchart RL
  subgraph pr["Changed files"]
    c0["src/app/mod.rs"]
    c1["src/domain/gemini.rs"]
    c2["src/domain/types.rs"]
    c3["src/infra/gemini.rs"]
    c4["src/infra/mod.rs"]
    c5["src/infra/processes.rs"]
  end
  subgraph g0["src/"]
    m0("domain")
  end
  subgraph g1["src/app/"]
    m1("fork")
    m2("mouse")
  end
  subgraph g2["src/domain/"]
    m3("parse")
    m4("stats")
    m5("claude")
    m6("timeline")
  end
  subgraph g3["src/infra/"]
    m7("claude")
    m8("codex_fork")
    m9("scan")
    m10("session_detail")
    m11("session_index")
    m12("clipboard")
    m13("tasks")
  end
  m0 -.-> c1
  m0 -.-> c2
  m1 --> c2
  m2 --> c0
  m2 --> c2
  m3 --> c2
  m4 --> c2
  m5 -.-> m0
  m6 -.-> m0
  m7 --> c2
  m8 --> c2
  m9 --> c2
  m10 --> c3
  m11 --> c2
  m11 --> c5
  m12 -.-> c4
  m13 -.-> c4
  classDef changed stroke-width:3px
  classDef far stroke-dasharray:5 4
  class c0,c1,c2,c3,c4,c5 changed
  class m0,m5,m6,m12,m13 far
```

<sub>An arrow points from a module to what it uses. Thick border: changed in this pull request. Dashed: it reaches a changed file through a module facade, a declaration or within 2 hops.</sub>

Through `src/domain/gemini.rs`, `src/domain/types.rs` and 1 more (a module facade or the declaration of the file): 3 module(s), by folder: `src/infra/` 2, `src/` 1. They see the change only if it alters an item they use.

Within 2 hops, by folder: `src/domain/` 2.

Not in the code graph (2: documents, configuration or deleted files): `CHANGELOG.md`, `README.md`.

</details>

No new boundary finding.

**Not checked**: part `.` (its style has no layers). No finding is reported there, and that is not a pass.

<sub>Wingbox 0.10.1, base `a34c249`, head `8543fd1`. Built and checked on the runner; no code left it.</sub>

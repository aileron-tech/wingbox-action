<!-- Wingbox Pull Request Check on a one-file commit of a public Rust project, replayed as a pull request against its parent, built locally with wingbox 0.10.1 and tools/replay.py; not posted anywhere. -->

## Wingbox: what this pull request touches

**6** module(s) not changed by this pull request use a changed file directly; 1 more through a module facade or declaration; 6 within 2 hops. **0** new boundary finding(s). 1 changed file(s), 1 in the code graph.

### Blast radius

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 12, "rankSpacing": 60, "padding": 8}}}%%
flowchart RL
  subgraph pr["Changed files"]
    c0["src/infra/session_detail.rs"]
  end
  subgraph g0["src/"]
    m0("cli")
    m1("main")
    m2("app")
    m3("infra")
  end
  subgraph g1["src/app/"]
    m4("mouse")
  end
  subgraph g2["src/infra/"]
    m5("claude")
    m6("codex_fork")
    m7("gemini")
    m8("opencode")
    m9("clipboard")
    m10("scan_all")
    m11("session_index")
    m12("tasks")
  end
  m0 --> c0
  m1 --> c0
  m2 -.-> m3
  m3 -.-> c0
  m4 -.-> m3
  m5 --> c0
  m6 --> c0
  m7 --> c0
  m8 --> c0
  m9 -.-> m3
  m10 -.-> m3
  m11 -.-> m3
  m12 -.-> m3
  classDef changed stroke-width:3px
  classDef far stroke-dasharray:5 4
  class c0 changed
  class m2,m3,m4,m9,m10,m11,m12 far
```

<sub>An arrow points from a module to what it uses. Thick border: changed in this pull request. Dashed: it reaches a changed file through a module facade, a declaration or within 2 hops.</sub>

### Not changed, but uses what changed

Directly:

- `src/cli` uses `LoadSessionTimelineError`, `load_session_timeline` from `src/infra/session_detail.rs`
- `src/infra/claude` uses `LastAssistantOutput` from `src/infra/session_detail.rs`
- `src/infra/codex_fork` uses `load_session_timeline` from `src/infra/session_detail.rs`
- `src/infra/gemini` uses `LastAssistantOutput` from `src/infra/session_detail.rs`
- `src/infra/opencode` uses `load_session_timeline` from `src/infra/session_detail.rs`
- `src/main` uses `load_last_assistant_output`, `load_session_timeline` from `src/infra/session_detail.rs`

Through `src/infra/session_detail.rs` (a module facade or the declaration of the file): 1 module(s), by folder: `src/` 1. They see the change only if it alters an item they use.

Within 2 hops, by folder: `src/infra/` 4, `src/` 1, `src/app/` 1.

### Boundary findings introduced by this pull request

None.

**Not checked**: part `.` (its style has no layers). No finding is reported there, and that is not a pass.

<sub>Wingbox 0.10.1, base `70939f6`, head `cf6d8c8`. Built and checked on the runner; no code left it.</sub>

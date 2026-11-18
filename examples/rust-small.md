<!-- Wingbox Pull Request Check on a one-file commit of a public Rust project, replayed as a pull request against its parent, built locally with wingbox 0.11.0 and tools/replay.py; not posted anywhere. -->

## Wingbox: 2 items touched by this change are used by 4 modules outside this pull request

`load_session_timeline` (function, `src/infra/session_detail.rs:97`) calls `detect_log_format`, which calls `looks_like_claude_jsonl`, which changed. These modules use it and are not in the pull request:

- `src/cli` calls it at `src/cli/mod.rs:331` and in 1 more place
- `src/infra/codex_fork` calls it at `src/infra/codex_fork.rs:375` and in 1 more place
- `src/infra/opencode` calls it at `src/infra/opencode.rs:992`
- `src/main` calls it at `src/main.rs:426` and in 4 more places

`load_last_assistant_output` (function, `src/infra/session_detail.rs:30`) calls `detect_log_format`, which calls `looks_like_claude_jsonl`, which changed. This module uses it and is not in the pull request:

- `src/main` calls it at `src/main.rs:1093` and in 1 more place

<details>
<summary>More: the picture, 2 modules that use the changed files but not what changed, 1 through a facade or declaration, 6 within 2 hops</summary>

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

Use the changed files but not the items that changed: 2 module(s), by folder: `src/infra/` 2.

Through `src/infra/session_detail.rs` (a module facade or the declaration of the file): 1 module(s), by folder: `src/` 1. They see the change only if it alters an item they use.

Within 2 hops, by folder: `src/infra/` 4, `src/` 1, `src/app/` 1.

</details>

No new boundary finding.

**Not checked**: part `.` (its style has no layers). No finding is reported there, and that is not a pass.

<details>
<summary>Review plan for an agent</summary>

```lisp
(review-plan :schema 1 :result UNKNOWN :execution ok :base 70939f6 :head cf6d8c8 :wingbox "0.11.0" :hops 2)
(reason "not-checked: . (no-layer-style)")
(changed-item "src/infra/session_detail.rs" load_session_timeline :kind fn :line 97 :how calls :via (detect_log_format looks_like_claude_jsonl))
(look "src/cli/mod.rs" 331 :module src/cli :why call :item load_session_timeline :places 2)
(look "src/infra/codex_fork.rs" 375 :module src/infra/codex_fork :why call :item load_session_timeline :places 2)
(look "src/infra/opencode.rs" 992 :module src/infra/opencode :why call :item load_session_timeline :places 1)
(look "src/main.rs" 426 :module src/main :why call :item load_session_timeline :places 5)
(changed-item "src/infra/session_detail.rs" load_last_assistant_output :kind fn :line 30 :how calls :via (detect_log_format looks_like_claude_jsonl))
(look "src/main.rs" 1093 :module src/main :why call :item load_last_assistant_output :places 2)
(not-checked "." :reason no-layer-style)
(changed-file "src/infra/session_detail.rs" :sha256 e0ef5272f7b67ca65e372eb6da0b9ad8aa59054f043c9ee0bd40977bb5b60f26)
```

</details>

<sub>[Wingbox](https://wingbox.dev) 0.11.0, base `70939f6`, head `cf6d8c8`. Built and checked on the runner, without a language model; no code left it.</sub>

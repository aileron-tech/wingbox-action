<!-- Wingbox Pull Request Check on fastapi/fastapi#16746 ("Add dependency concurrency limits with Depends(max_concurrency=...)"), base 5758a9c, head fc07dde, built locally with wingbox 0.10.1 and tools/replay.py; not posted anywhere. -->

## Wingbox: what this pull request touches

**5** module(s) not changed by this pull request use a changed file directly; 1 more through a module facade or declaration; 371 within 2 hops; 279 test module(s). **0** new boundary finding(s). 9 changed file(s), 6 in the code graph.

### Blast radius

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 12, "rankSpacing": 60, "padding": 8}}}%%
flowchart RL
  subgraph pr["Changed files"]
    c0["fastapi/concurrency.py"]
    c1["fastapi/dependencies/models.py"]
    c2["fastapi/dependencies/utils.py"]
    c3["fastapi/param_functions.py"]
    c4["fastapi/params.py"]
  end
  subgraph g0["docs_src/"]
    m0[["366 modules"]]
  end
  subgraph g1["fastapi/"]
    m1("applications")
    m2("routing")
    m3[["6 modules"]]
  end
  subgraph g2["fastapi/_compat/"]
    m4("v2")
  end
  subgraph g3["fastapi/openapi/"]
    m5("utils")
  end
  subgraph g4["fastapi/security/"]
    m6("oauth2")
  end
  subgraph g5["tests/"]
    m7[["9 test modules"]]
    m8[["269 test modules"]]
  end
  subgraph g6["tests/test_request_params/"]
    m9("test_file/test_nullable_and_defaults")
  end
  m0 -.-> m2
  m0 -.-> m3
  m0 -.-> m5
  m1 --> c4
  m2 --> c1
  m2 --> c2
  m2 --> c4
  m3 -.-> c3
  m3 -.-> m2
  m3 -.-> m4
  m3 -.-> m6
  m4 --> c4
  m5 --> c1
  m5 --> c2
  m5 --> c4
  m6 --> c3
  m7 --> c0
  m7 --> c1
  m7 --> c2
  m7 --> c3
  m7 --> c4
  m8 -.-> m2
  m8 -.-> m3
  m8 -.-> m4
  m8 -.-> m5
  m9 --> c4
  classDef changed stroke-width:3px
  classDef far stroke-dasharray:5 4
  class c0,c1,c2,c3,c4 changed
  class m0,m3,m8 far
```

<sub>An arrow points from a module to what it uses. Thick border: changed in this pull request. Dashed: it reaches a changed file through a module facade, a declaration or within 2 hops. A box with a count stands for the modules of one folder.</sub>

### Not changed, but uses what changed

Directly:

- `fastapi/_compat/v2` uses `fastapi/params.py`
- `fastapi/applications` uses `fastapi/params.py`
- `fastapi/openapi/utils` uses `fastapi/dependencies/models.py`, `fastapi/dependencies/utils.py` and 1 more
- `fastapi/routing` uses `fastapi/dependencies/models.py`, `fastapi/dependencies/utils.py` and 1 more
- `fastapi/security/oauth2` uses `fastapi/param_functions.py`

Through `fastapi/param_functions.py` (a module facade or the declaration of the file): 1 module(s), by folder: `fastapi/` 1. They see the change only if it alters an item they use.

Within 2 hops, by folder: `docs_src/query_params_str_validations/` 30, `docs_src/dependencies/` 29, `docs_src/custom_response/` 15, `docs_src/request_form_models/` 14, `docs_src/security/` 14, `docs_src/path_params_numeric_validations/` 12, 75 more folder(s).

Tests that use the changed code (279), by folder: `tests/` 217, `tests/test_request_params/` 31, `tests/test_telemetry/` 12, `tests/test_tutorial/` 11, `tests/test_modules_same_name_body/` 3, `tests/benchmarks/` 2, 2 more folder(s).

Not in the code graph (3: documents, configuration or deleted files): `docs/en/docs/advanced/advanced-dependencies.md`, `fastapi/.agents/skills/fastapi/SKILL.md`, `fastapi/.agents/skills/fastapi/references/dependencies.md`.

### Boundary findings introduced by this pull request

None.

**Not checked**: part `.` (its style has no layers). No finding is reported there, and that is not a pass.

<sub>Wingbox 0.10.1, base `5758a9c`, head `fc07dde`. Built and checked on the runner; no code left it.</sub>

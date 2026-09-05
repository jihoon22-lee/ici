# Rule-only C++ diagnostic category projection — 2026-09-03

## Overview

This workthrough records the local C++ diagnostic category slice on
`feat/cpp-diagnostic-categories`. It covers the isolated taxonomy, regression tests, cache
identity, candidate-runner tool provisioning, and synchronized documentation without changing
version metadata.

The projection is deliberately rule-only: it consumes the normalized `CppDiagnostic.family` and
`CppDiagnostic.tool_rule_id`, never free-form diagnostic messages. Its policy identifier is
`tool-rule-v1`. This is local feature-head evidence only; remote PR/main acceptance, the new Qt
Quality Zoo scenario, a matching candidate acceptance, and a release remain pending. The earlier
sanitizer candidate acceptance is valid only for that exact older scope. ici stays at `v0.10.2`.

## Context

Before this slice, analyzer diagnostics were broadly presented as `CORRECTNESS`, ordinary
clang-tidy checks as `MAINTAINABILITY`, and clazy used broad semantic token groups. That was too
coarse for resource/lifetime, security, compatibility, and selected correctness signals, while
message-based matching would make category output unstable and easy to influence with arbitrary
tool prose.

The parser boundary already normalizes compiler, clang-analyzer, clang-tidy, and clazy output into
family and stable tool-rule identity. Category projection therefore stays after parsing and does
not add a second text parser or a message heuristic.

## Changes Made

### 1. Ordered `tool-rule-v1` policy

The policy lowercases family and rule identity before matching. Its conservative order is:

| Order | Family/rule condition | Category |
|---|---|---|
| 1 | `family = compiler` | `CORRECTNESS` |
| 2 | Analyzer `clang-analyzer-security.*`, `clang-analyzer-alpha.security.*`, or `clang-analyzer-optin.taint.*`; tidy `cert-*`, `android-cloexec-*`, `bugprone-command-processor`, `bugprone-signal-handler`, `bugprone-unsafe-functions`, or `concurrency-mt-unsafe` | `SECURITY` |
| 3 | Analyzer exact resource IDs and `clang-analyzer-alpha.webkit.*`/`clang-analyzer-webkit.*` prefixes; tidy exact resource IDs listed below | `RESOURCE` |
| 4 | Remaining `family = clang-analyzer` | `CORRECTNESS` |
| 5 | Tidy `portability-*` or exact `modernize-deprecated-headers` | `COMPATIBILITY` |
| 6 | Tidy security/resource exceptions 제외한 모든 `bugprone-*` 또는 `concurrency-*` | `CORRECTNESS` |
| 7 | Remaining `family = clang-tidy` | `MAINTAINABILITY` |
| fallback | Unknown/non-tool family (other than recognized clazy) | `CORRECTNESS` |

Analyzer resource exact IDs are `clang-analyzer-alpha.core.danglingptrderef`,
`clang-analyzer-alpha.core.useafterlifetimeend`, `clang-analyzer-alpha.cplusplus.smartptr`,
`clang-analyzer-cplusplus.arraydelete`, `clang-analyzer-cplusplus.innerpointer`,
`clang-analyzer-cplusplus.newdelete`, `clang-analyzer-cplusplus.newdeleteleaks`,
`clang-analyzer-fuchsia.handlechecker`, `clang-analyzer-osx.cocoa.retaincount`,
`clang-analyzer-osx.cocoa.runloopautoreleaseleak`,
`clang-analyzer-osx.corefoundation.cfretainrelease`, `clang-analyzer-unix.malloc`,
`clang-analyzer-unix.mismatcheddeallocator`, and `clang-analyzer-unix.stream`. The analyzer
resource prefixes are `clang-analyzer-alpha.webkit.*` and `clang-analyzer-webkit.*`. Tidy resource
exact IDs are `bugprone-dangling-handle`, `bugprone-dangling-reference`,
`bugprone-multiple-new-in-one-expression`, `bugprone-shared-ptr-array-mismatch`,
`bugprone-suspicious-realloc-usage`, `bugprone-unique-ptr-array-mismatch`,
`bugprone-unused-raii`, `bugprone-use-after-move`, `cppcoreguidelines-owning-memory`, and
`misc-new-delete-overloads`.

### 2. Bounded clazy stems and stable exact rules

Clazy matching accepts only the stem itself or a child separated by `-` or `.`. It does not accept
an arbitrary substring. The ordered groups are:

- `clazy-lifetime`, `clazy-ownership`, `clazy-parent-less`, `clazy-qobject-cast` stems and
  `clazy-connect-3arg-lambda`, `clazy-ctor-missing-parent-argument`, `clazy-lambda-in-connect`,
  `clazy-post-event`, `clazy-returning-data-from-temporary`, `clazy-temporary-iterator` exact
  rules → `RESOURCE`;
- `clazy-qt6`, `clazy-deprecated`, `clazy-qstring-arg`, `clazy-qt-keyword` stems and
  `clazy-modernize-overloaded-connects`, `clazy-no-module-include`, `clazy-old-style-connect`,
  `clazy-qenums`, `clazy-qstring-ref`, `clazy-use-chrono-in-qtimer` exact rules →
  `COMPATIBILITY`;
- `clazy-qobject`, `clazy-connect`, `clazy-signal`, `clazy-slot`, `clazy-qevent-cast` stems and
  `clazy-assert-with-side-effects`, `clazy-base-class-event`, `clazy-child-event-qobject-cast`,
  `clazy-const-signal-or-slot`, `clazy-copyable-polymorphic`, `clazy-ifndef-define-typo`,
  `clazy-incorrect-emit`, `clazy-install-event-filter`, `clazy-jni-signatures`,
  `clazy-lambda-unique-connection`, `clazy-missing-qobject-macro`, `clazy-missing-typeinfo`,
  `clazy-mutable-container-key`, `clazy-overloaded-signal`, `clazy-overridden-signal`,
  `clazy-qhash-with-char-pointer-key`, `clazy-qproperty-type-mismatch`,
  `clazy-qproperty-without-notify`, `clazy-qstring-varargs`, `clazy-rule-of-three`,
  `clazy-rule-of-two-soft`, `clazy-signal-with-return-value`, `clazy-skipped-base-method`,
  `clazy-thread-with-slots`, `clazy-unexpected-flag-enumerator-value`,
  `clazy-virtual-call-ctor`, `clazy-virtual-signal`, `clazy-writing-to-temporary`,
  `clazy-wrong-qevent-cast` exact rules → `CORRECTNESS`;
- unmatched clazy rules → `MAINTAINABILITY`.

Resource is evaluated before correctness, so `clazy-qobject-cast` remains a resource rule even
though it also begins with `clazy-qobject`.

### 3. Stable result metadata and regression coverage

Lint `extra` carries `cpp_diagnostic_category_policy = "tool-rule-v1"` and
`cpp_diagnostic_categories`, a count for every `FindingCategory` value. Counts cover primary C++
diagnostics in the projection; related notes do not become additional primary findings.

The focused tests cover compiler/unknown safety defaults, strict analyzer-versus-tidy family
isolation, analyzer security/resource/fallback rules (including opt-in taint and WebKit prefixes),
tidy CERT/Android/direct security, expanded lifetime/resource, portability/deprecated-header
compatibility, all remaining bugprone/concurrency correctness, tidy maintainability fallback,
clazy exact-rule complements and bounded-stem negative cases, explicit message independence, and
all-category count metadata. The isolated category helper is
declared in `LintEngine.CACHE_IMPLEMENTATION_MODULES`, so policy changes invalidate lint cache
identity through the helper source digest.

The related documentation now links to or summarizes the canonical user-guide policy in:

- `README.md`
- `CHANGELOG.md`
- `docs/user-guide.md`
- `docs/engine-reference.md`
- `docs/architecture.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `docs/superpowers/2026-08-30-handover.md`
- `docs/ci-integration.md`

### 4. Candidate Quality Zoo tool provisioning

The manual `.github/workflows/candidate-quality-zoo.yml` consumer path now has a hosted-runner
provisioning step for `clang`, `clang-tidy`, `clazy`, `cmake`, `g++`, `pkg-config`, and
`qt6-base-dev`, so a future Qt lifetime/C++ static-analysis scenario is not skipped solely because
the runner lacks tools. This CI-only provisioning is separate from candidate execution; candidate
preflight/execution remain unprivileged and credential-free, while authenticated API reads stay in
the separate evidence step. Local `tests/test_purity.py` passes `31` tests and `actionlint` passes.
The earlier sanitizer candidate acceptance predates this feature head, so a new candidate dispatch
and Qt Quality Zoo acceptance remain pending.

## Code Examples

The result metadata has this shape (counts are illustrative):

```json
{
  "cpp_diagnostic_category_policy": "tool-rule-v1",
  "cpp_diagnostic_categories": {
    "correctness": 1,
    "type": 0,
    "security": 1,
    "resource": 1,
    "build": 0,
    "test": 0,
    "maintainability": 1,
    "architecture": 0,
    "compatibility": 1
  }
}
```

Conceptually, a finding is projected from identity rather than prose:

```text
family/rule identity → ordered tool-rule policy → v3 category
diagnostic message   ───────────────────────────→ ignored
```

## Verification Results

| Check | Result |
|---|---|
| Focused C++ lint/category regression | `uv run --python 3.10 pytest tests/test_lint_engine.py tests/test_clang_tidy.py tests/test_clazy.py` — `160 passed` |
| Focused cache identity/store regression | `uv run --python 3.10 pytest tests/test_cache_identity.py tests/test_cache_store.py` — `51 passed` |
| Candidate workflow purity | `uv run --python 3.10 pytest tests/test_purity.py` — `31 passed`; `actionlint .github/workflows/candidate-quality-zoo.yml` — PASS |
| Full Python 3.10 suite | `uv run --python 3.10 pytest -o addopts=''` — `2,138 passed, 7 skipped` |
| Static quality | `uvx ruff check .` and `uvx ruff format --check .` — PASS (`191` files formatted); `uv run --python 3.10 mypy src` — `106` source files, no issues |
| Standalone artifact | `./scripts/build-pyz.sh` — pure-Python dependency/schema audit and build PASS; `./scripts/smoke.sh` — direct execution, Python 3.10, artifact identity, and Zero-CDN PASS |
| Version/release | No version bump, tag, or release; ici remains `v0.10.2` |
| Acceptance boundary | Local feature-head evidence only; the prior exact sanitizer candidate acceptance is not claimed for this taxonomy |

## Next Steps

- Keep the I4-4 resource/lifetime/security mapping checkbox pending until the wider checkpoint
  requirements and remote acceptance are satisfied.
- Add and accept the Qt-lifetime scenario that asserts category/rule/location and clean-path
  absence with released and feature-head candidate executables.
- Complete any remaining TSan/deep safety profile and repository-wide gates before considering a
  release decision.

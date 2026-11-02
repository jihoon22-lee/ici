# R9 cutover — Python 제품 제거 체크리스트 (준비됨, 미적용)

- 상태: **prepared, not applied.** 이 브랜치(`feat/rust-cutover`)는 아무것도 삭제하지 않는다.
  root가 R8 인수 뒤 같은 R9 cutover 변경에서 아래 순서로 적용한다
  ([실행 계획 §8](../rust-replacement-execution-plan.md), [ADR-0008](../ici-next/adr/0008-rust-native-replacement.md)).
- 측정 기준: base `48d82ad4`(이 브랜치의 분기점)의 tracked tree. 적용 직전에 §1의 명령으로 다시
  생성하고 차이가 있으면 이 표를 먼저 갱신한다.
- 이전의 Python 내부 정리 계획 [stable-removal.md](../ici-next/inventory/stable-removal.md)는 Python
  core를 유지하는 전제였다. Rust 단일 제품 전환으로 그 계획은 대체되며 역사 기록으로만 남긴다.

## 0. 적용 전제 (하나라도 아니면 적용하지 않는다)

1. `python3 scripts/rust_migration_inventory.py --check --require-closed`가 exit 0
   (원장 미판정·회귀·누락 0). 2026-11-02 측정은 not_started 94 / implemented 5 /
   verified 163 / difference_review 11, source unreviewed 76으로 **아직 거부**된다.
2. exact-main commit의 `candidate-artifact.yml` run이 success: pyz `build`와 `native` job
   (두 독립 build byte 동일, `check-native-release.sh`, `verify_artifact.py` 전체 통과).
3. 같은 native candidate로 `candidate-quality-zoo.yml candidate_format=native`가
   `contract_verdict: PASS`. 2026-11-02 runtime worker 측정은 16개 중 8개 C++ projection 실패로
   **아직 실패**한다(`closure/runtime.json`).
4. root가 explicit actual-tool suite(`--include-ignored`, opus-env)와 workspace fmt/clippy/test를
   exact-main에서 통과시킴.
5. 공개 승인 범위 확인(2026-10-28 사용자 승인: 태그·공개 포함).

## 1. 목록을 다시 만드는 명령

```bash
git ls-files | awk -F/ '{print $1}' | sort | uniq -c
git ls-files src/ici | wc -l                       # 291
git ls-files tests | grep -v '^tests/rust-parity/\|^tests/next/\|^tests/fixtures/' | wc -l   # 121
git ls-files tests/next | wc -l                    # 79
# 제거 후 깨질 비-Python 소비자: 결과가 §2-A의 schema 이동 항목과 같아야 한다
grep -rn "src/ici" crates --include=*.rs | grep -v '^crates/.*//'
grep -rln "src/ici" scripts tests/rust-parity quality-zoo .github viewer packaging
grep -rln "](\(\.\./\)*src/ici" --include=*.md .   # 문서 링크
```

`src/ici`를 지운 scratch tree에서 `python3 scripts/rust_migration_inventory.py --check`는
그대로 통과했다(원장은 frozen oracle `6fe3e405`를 `git archive`로 읽는다;
`/tmp/ici-rust-replacement-20261028/cutover-inventory-without-src.log`).

## 2. 제거·변경 대상

### A. Python 제품 source

|경로|파일|처리|
|---|---|---|
|`src/ici/**`|291|삭제. **단, 아래 schema 6개를 먼저 옮긴다**|
|`src/ici/schemas/{ici-result-v3,ici-next-run-v1,ici-next-run-v2,ici-next-event-v1,ici-auxiliary-v1,ici-compilation-export-v1}.schema.json`|6|공개 wire 계약. 최상위 `schemas/`(또는 root가 정한 위치)로 `git mv` 후 소비자 갱신: `crates/ici-cli/tests/wire_schemas.rs`의 `include_str!("../../../src/ici/schemas/…")` 5곳, `tests/rust-parity/reporting/validate_wire_schemas.py`의 `ROOT / "src/ici/schemas"`, `README.md`의 schema 링크 2곳. 원장 script는 schema를 frozen oracle에서 읽으므로 영향 없음(§1 scratch 측정)|
|`tools/ici`|1|Python 제품 개발 wrapper. 삭제|

### B. dispatch·packaging

|대상|처리|
|---|---|
|`pyproject.toml` `[build-system]`, `[project]` 배포 metadata·runtime dependencies(typer/rich/tomli/tomli-w/packaging), `[project.scripts] ici`, `[tool.hatch.*]`, dependency group `package`(hatchling, shiv), `dev`의 `shiv`|삭제|
|`pyproject.toml` dev harness|**유지**하되 배포 불가로: CI `rust` job의 `uv sync --frozen --python 3.10`과 Rust CLI 시험의 `.venv/bin/python`(pytest/coverage/mypy), quality-zoo runner, parity capture script가 쓴다. 예: `[project] name = "ici-dev-harness"` + `[tool.uv] package = false`, dev group(pytest, coverage, mypy, ruff) 유지. `uv.lock` 재생성 후 CI rust job 재확인|
|`[tool.ruff]`|유지(harness Python). `src = ["src", "tests"]`와 `extend-exclude`의 `examples/...`는 실제 남는 경로로 갱신. `target-version = "py310"`은 harness가 Python 3.10에서 돌므로 유지 가능(root 결정)|
|`uv.lock`|dev harness만으로 재생성|

### C. 빌드·smoke·bundle script

삭제: `scripts/build-pyz.sh`, `scripts/assemble_pyz.py`, `scripts/run_shiv.py`, `scripts/launcher.sh`,
`scripts/smoke.sh`, `scripts/verify-reproducibility.sh`, `scripts/build-package.sh`,
`scripts/smoke-package.sh`, `scripts/assemble_bundle.py`, `scripts/bundle/{launcher.sh,smoke.sh,summarize_smoke.py}`,
`scripts/spikes/wp01/*`(embedded CPython bundle spike, ADR-0002 대체), `scripts/candidate_bundle.py`
(pyz candidate producer), `scripts/benchmark_report.py`, `scripts/benchmark_browser.py`,
`scripts/benchmark_next.py`(Python 제품 reporter/next benchmark; Rust 측정은
`tests/rust-parity/perf/measure_budget.py`).

유지: `scripts/build-native.sh`, `scripts/install-native.sh`, `scripts/prepare-native-inputs.sh`,
`scripts/check-native-release.sh`, `scripts/candidate_merge_gate.py`(candidate·zoo workflow가 사용),
`scripts/rust_migration_inventory.py`(원장).

### D. Python 제품 시험

|경로|파일|처리|
|---|---|---|
|`tests/*.py`(conftest, toolcontract, cache_fixtures, fixture_manifest 포함)|121|Python 제품 시험. 삭제. 단 아래 3개는 제품이 아니라 저장소 자산을 검사하므로 **이전 또는 결정** 필요|
|`tests/test_candidate_merge_gate.py`|1|`scripts/candidate_merge_gate.py`의 유일한 시험. helper는 유지되므로 이 시험도 cutover 변경 안에서 stdlib `unittest` 등으로 옮겨 CI에서 계속 실행한다(stable pytest가 남아 있는 동안에는 Python helper를 `test_*.py`로 이름 짓지 않는 2026-11-02 규칙이 적용되므로 삭제와 같은 변경에서 옮긴다)|
|`tests/test_fixture_corpus.py`, `tests/test_corpus_hygiene.py`|2|fixture register(`tests/fixtures/manifest.toml`)와 quality-zoo hygiene 계약. register를 유지하면 시험도 이전, 아니면 register와 함께 정리|
|`tests/next/**`|79|Python next 경로 시험. 삭제(`test_ici_next_inventory.py`의 AGENTS·문서 계약 포함 — §2-H 참조)|
|`tests/fixtures/ici-next/*.json`|7|**유지**: Rust 시험이 읽음(`legacy-v3-*`, `run-*`)|
|`tests/fixtures/config/**`, `tests/fixtures/next-self-verify/**`|16|Rust 소비자 없음(grep). Python 시험·pyz next dogfood 전용 → 삭제 또는 native dogfood fixture로 재사용(root 결정)|
|`examples/**`|45|Rust 소비자 없음(grep). Python 시험 fixture → 삭제 또는 보존(root 결정)|

### E. CI gate (`.github/workflows`)

|workflow/job|현재|cutover 처리|
|---|---|---|
|`ci.yml` `verify`|ruff·pytest·C++ fixture·pyz build/smoke/package·`dist/ici.pyz verify` dogfood(root, viewer static CLI, viewer C++), pyz next dogfood, benchmark|Python 단계 삭제. 유지: `quality-zoo` 단위 시험(`python3.10 -m unittest`), harness Ruff(`uvx ruff check .`/`format --check`). dogfood는 같은 job에서 `cargo build --release --locked -p ici-cli`로 만든 native `ici`로 대체: `viewer/`의 `ici verify --report --html …`, `ICI_CONFIG=ici-static-cli.toml ici verify --profile deep --report`, 저장소 루트 분석(남는 harness Python·문서 기준 새 설정)|
|`ci.yml` `rust`|fmt/clippy/test, 원장 `--check`|유지. **`merge-gate`의 `needs`에 `rust`가 없다** → 추가하고 결과를 검사|
|`ci.yml` `report-pr`, `publish-main`|trusted base에서 `build-pyz.sh` 후 `dist/ici.pyz publish`|trusted base checkout에서 native `ici`를 build해 `ici publish --report-dir …`. artifact만 소비, PR head checkout 금지, 권한 분리 유지(`tests/test_purity.py`가 지키던 속성을 새 시험이나 actionlint 규칙으로 재확인)|
|`ci.yml` `viewer-gui`|Qt5/Qt6 build/CTest/headless|유지|
|`release.yml`|tag push 시 pyz/wheel/viewer release|삭제. tag push가 native를 자동 게시하지 않도록 `release-native.yml`(dispatch, candidate bytes 재게시)만 남기거나 이름을 `release.yml`로 바꿈. viewer `icirv`/`icirv-gui` release asset을 계속 낼지 결정(현재는 pyz release job 안에 있음)|
|`release-native.yml`|inert(dispatch only, `src/ici` 존재 시 거부)|cutover 후 활성. guard 유지|
|`candidate-artifact.yml` `build`|pyz 두 번 build, `candidate_bundle.py`|삭제. `validate` + `native` 유지|
|`candidate-quality-zoo.yml` `accept`|pyz ZIP intake|삭제, `candidate_format` 입력 제거 또는 기본값 `native`|
|`bundle-smoke.yml`|python-build-standalone bundle|삭제(ADR-0002 대체)|

### F. 문서·설정·메타데이터

- `AGENTS.md`: 이 브랜치의 초안 commit을 적용(§3/§4/§6 Python 제품 규칙을 역사 기록으로, Rust gate가 gate).
- 루트 `ici.toml`(`[ici] version = "0.13.0"`, Python 제품 dogfood 정책): native dogfood 설정으로 교체.
- `Cargo.toml` `[workspace.package] version = "0.14.0-dev.0"` → 결정한 release 버전(`release-native.yml`은 tag와 같아야 함). `CHANGELOG.md` release 절.
- `docs/architecture.md` §3(ZipApp·Polyglot launcher·재현 pyz build)과 README의 pyz 잔여 설명(§4 cache `entries-v1` 등): 역사 표시 또는 native 설명으로 교체.
- 문서의 `src/ici/...` 상대 링크(대략 spec-04 12, ici-next/architecture 7, 실행 계획 6, traceability 6, compatibility 5, config 문서 6, user-guide/architecture/README 각 2, 그 외 1씩): 삭제 후 깨지므로 `https://github.com/jihoon22-lee/ici/blob/v0.13.0/src/ici/...` 고정 tag 링크로 바꾼다.
- `scripts/build-native.sh`의 host 전용 lock 경로 `/tmp/paseo-products-20261028-heavy.lock`: 공개 script에 남길지(환경변수 override 등) root 결정. 기능 영향은 없음.

## 3. 반드시 보존 (삭제 금지)

|대상|이유|
|---|---|
|Git history, `v0.x` tag, 과거 GitHub Release asset(v0.13.0 `ici.pyz` `c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde`, wheel/sdist, viewer)|과거 공개판 보존, 명시 복귀 대상(계획 §8, ADR-0008)|
|frozen oracle commit `6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`/tag `v0.13.0`|원장(`rust_migration_inventory.py` ORACLE)과 parity capture가 참조|
|`docs/design/rust-replacement/**`(inventory, closure fragments, decisions), 실행 계획|R0–R9 근거|
|`tests/rust-parity/**`(golden, capture/verify driver)|외부 oracle harness는 제품 runtime이 아님(계획 §1). Rust 시험이 `include_str!`로 읽는 golden 포함|
|`parity-fixtures/**`|C++ fixture tree|
|`quality-zoo/**` 전체: pyz digest에 묶인 expectation(released-v0.10.2/v0.12.x, candidate-v0.13.0 등), `runner/candidate_intake.py`(pyz 이력 replay), `bind_candidate.py`, `native_intake.py`, `candidate_overrides.py`, corpus 시험|ici 소유 known-answer corpus와 pyz 이력|
|`packaging/native/**`|고정 입력과 고지 원본|
|`viewer/**`(`tests/data/ici_self_report.json` v3 sample 포함)|별도 C++/Qt 소비자(ADR-0008)|
|`docs/design/ici-next/**`, `workthrough/**`, `docs/baselines/**`, `docs/superpowers/**`, `CHANGELOG.md`|역사 기록|
|`tests/fixtures/ici-next/*.json`|Rust 시험 입력|
|`pyproject.toml` dev harness + `uv.lock`|Rust 시험·CI rust job·harness의 프로젝트 Python(외부 도구)|

## 4. 제거되는 gate와 대체 gate

|제거|대체|
|---|---|
|`uv run --python 3.10 pytest`(Python 제품 4,300여 시험), `ICI_REQUIRE_*` adapter skip 금지|`cargo test --workspace --locked`(CI), root의 explicit actual-tool suite `--include-ignored`, 원장 `--check --require-closed`|
|`ruff check/format`의 제품 범위, mypy dogfood|`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --locked -- -D warnings`(Ruff는 남는 harness Python에 계속 적용)|
|`build-pyz.sh` + `verify-reproducibility.sh`(pyz 두 번 build)|`candidate-artifact.yml` native: exact-source 두 독립 build byte 동일, `build-native.sh`의 snapshot·std tree·고지 catalogue·static ELF 검사|
|`smoke.sh`, `build-package.sh`/`smoke-package.sh`|`check-native-release.sh`(고지·라이선스, Python payload 부재, rootless XDG bootstrap, Python 없는 오프라인 분석, doctor shadowing) + `verify_artifact.py`(update/rollback/위조·손상 거부/사설 CA). 필요 시 `verify_setup_proxy.py`, `verify_publish.py`|
|`tests/test_launcher.py`(launcher ↔ `PYTHON_CANDIDATES`)|해당 없음: native core는 인터프리터를 탐색하지 않음. legacy 명령의 프로젝트 인터프리터 후보 순서는 Rust 시험이 고정|
|`dist/ici.pyz verify` dogfood(root·viewer)|native `ici verify` dogfood(§2-E)|
|pyz candidate → Quality Zoo|native candidate → Quality Zoo(`candidate_format=native`)|
|`bundle-smoke.yml`|없음(ADR-0002 대체)|
|Python 3.10 하한·순수 wheel(`requires-python`, ruff py310 제품 범위)|Rust 1.99.1 pin(`rust-toolchain.toml`), `Cargo.lock --locked`, 고정 native 입력 digest|

## 5. 적용 순서 (root)

1. §0 전제 확인. 각 결과를 PR 근거에 링크.
2. `feat/rust-cutover`를 최신 `main`에 맞춘다(문서·workflow 충돌 해결; ledger fragment는 건드리지 않음).
3. schema 6개 `git mv` + 소비자 갱신(§2-A) → `cargo test -p ici-cli --test wire_schemas --locked`.
4. `git rm -r src/ici tools/ici` 및 §2-C·§2-D 삭제 대상. 저장소 자산 시험 3개는 먼저 이전.
5. `pyproject.toml`/`uv.lock` 정리(§2-B) → `uv sync --frozen --python 3.10` 확인.
6. workflow 변경(§2-E): merge-gate에 `rust` 추가, verify job native dogfood, publish job native, pyz release/candidate/bundle 삭제.
7. 문서·설정·버전(§2-F), AGENTS 초안 적용.
8. 사후 검증(§6) 후 PR. 병합 후 exact-main candidate → zoo native → `release-native.yml`(draft) →
   공개 asset을 새로 내려받아 digest·설치·`ici --version`·오프라인 분석·rollback 확인 → draft 해제.

## 6. 사후 검증

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
python3 scripts/rust_migration_inventory.py --self-test
python3 scripts/rust_migration_inventory.py --check --require-closed
(cd quality-zoo && python3.10 -m unittest discover -s tests -v)
uvx ruff check . && uvx ruff format --check .
actionlint .github/workflows/*.yml
shellcheck scripts/*.sh
git ls-files | grep -E '\.pyz$|^src/ici/' && echo 'Python product still tracked' && exit 1
grep -rn "dist/ici.pyz\|build-pyz.sh\|launcher.sh" .github/workflows && exit 1
```

## 7. 이 브랜치의 로컬 리허설 근거 (2026-11-02, WSL2 x86_64)

최종 인수가 아니다. 새 workflow 단계가 실제로 동작하는지 같은 명령으로 확인한 개발 측정이다.
로그와 작은 결과는 `/tmp/ici-rust-replacement-20261028/cutover-*`에 있다.

|단계|명령|결과|
|---|---|---|
|고정 입력 준비|`scripts/prepare-native-inputs.sh --output … --from <캐시된 고정 파일>` (네트워크 없음)|Ruff/ty archive·binary·license, rust-std musl, musl 1.2.5 digest 일치, std file tree digest 일치, musl SDK build·compile probe 통과 (`cutover-prepare-inputs.log`)|
|exact-source 두 build|clean tree `04b1a921`에서 `build-native.sh --build-id cutover-exact-{a,b}` (각 약 5.5분, `-j2`)|두 archive 모두 SHA-256 `f0234fa6611cace689861b68caaed08898d2987d8c06238bf16c1cd18cd43563`, snapshot 0 (`cutover-build-exact.log`)|
|release 검사|`check-native-release.sh --compare … --expect-revision 04b1a921… --allow-prerelease-version`|전체 PASS, 오프라인 analyze exit 0 (`cutover-check-exact/`)|
|배포 artifact corpus|`python3 -I tests/rust-parity/distribution/verify_artifact.py …`|46 case 통과, `report.json` 생성 (`cutover-rehearsal/cutover-artifact-corpus-exact-report.json`) — 이전 후보처럼 doctor에서 멈추지 않음|
|Quality Zoo native 단계|`runner.native_intake` → `runner.bind_candidate --reference-sha256 c179986f…` (임시 corpus 사본) → `runner.run --scenario python.*`|intake source revision·clean snapshot 확인, 16 scenario 바인딩, Python 5개 scenario 모두 PASS (`cutover-rehearsal/zoo-python-suite.json`). C++ 11개는 여기서 실행하지 않음(runtime worker 측정상 8개 실패가 남아 있음)|
|음성 사례|이전 후보 `023bda38…`에 release 검사|retained notice 불일치와 개발 snapshot을 각각 거부|

같은 host 안의 비교이므로 GitHub runner에서 다시 build한 archive가 같은 digest를 가진다고 주장하지 않는다.

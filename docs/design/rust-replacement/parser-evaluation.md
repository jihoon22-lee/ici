# Rust Python parser 평가와 공유 adapter 계약

2026-10-28 R0 실측. 상태는 **parser 선택 근거 검증 완료 / 제품 adapter와 분석 의미 이식 미완료**다.
37개 fixture나 parser 성공을 R4 또는 전체 제품 parity 완료로 사용하지 않는다.

## 고정 후보와 공급 방식

Ruff `0.16.4`의 commit `11c76bf48fdac06b2f240cba502eda96da4dce77`을 선택한다.
`ruff_python_parser`·`ruff_python_ast`·`ruff_text_size` crate 버전은 `0.0.10`이다.
이들은 internal component이며 안정된 별도 라이브러리 API를 약속하지 않는다.
제품 workspace는 세 의존성을 같은 full git revision으로 고정하고 Cargo.lock을 커밋한다.
업그레이드는 이 corpus와 각 분석 의미의 차이를 검토한 별도 변경으로 처리한다.
[고정 parser manifest](https://github.com/astral-sh/ruff/blob/11c76bf48fdac06b2f240cba502eda96da4dce77/crates/ruff_python_parser/Cargo.toml)

실험은 해당 revision의 checkout을 path dependency로 사용했다. edition 2024, upstream
MSRV 1.95 선언을 확인했고 실제 rustc/cargo 1.99.1에서 빌드했다. Ruff의 선언 license는
MIT이며 upstream LICENSE에 파생 코드의 notice가 함께 있다. 배포 시 그 notice와 실제
의존성 license를 보존해야 한다. 이 실험의 upstream LICENSE SHA-256은
`2597d854122b77ddc71971564ca2350a37608575ce324adc5650a2b2051c8f18`이다.
[고정 workspace](https://github.com/astral-sh/ruff/blob/11c76bf48fdac06b2f240cba502eda96da4dce77/Cargo.toml),
[고정 license](https://github.com/astral-sh/ruff/blob/11c76bf48fdac06b2f240cba502eda96da4dce77/LICENSE)

RustPython/Parser README는 Ruff parser를 후속 구현으로 안내한다. RustPython이라는 이름을
근거로 이전 parser를 새 core에 선택하지 않았다. 이 결정은 Ruff runtime을 subprocess로
호출해 자체 AST 분석을 위임한다는 뜻이 아니다. parser library가 Rust core에 링크된다.
[RustPython/Parser 안내](https://github.com/RustPython/Parser#rustpython-parser-as-a-library)

## 실제 실행 근거

- fixture와 재현 명령: [parser corpus](../../../tests/rust-parity/parser/README.md).
- actual observations: [observations.json](../../../tests/rust-parity/parser/observations.json).
- WSL2 Linux 6.18.33.2, x86_64, host glibc 2.43, rustc 1.99.1, GCC를 사용했다.
- `CARGO_BUILD_JOBS=2`, 공통 `flock /tmp/paseo-products-20261028-heavy.lock` 아래에서
  debug와 `--release --target x86_64-unknown-linux-gnu`를 빌드했다.
- release의 `RUSTFLAGS='-C target-feature=+crt-static'` 결과는 `file`의 static PIE,
  `readelf -l`의 INTERP 없음, `ldd`의 statically linked로 확인했다.
- 최종 probe binary SHA-256:
  `8e317aa0cc6bbcefd9e7afca15863c2d77266d1c4d794593d101f433f5ea0953`.
- `env -i PATH=/nonexistent`에서 native probe를 실행해 37개 기대 판정과 명시적인
  위치·scope AST shape 검증을 통과했다. `strace -f -e trace=execve,network`에는
  env에서 native probe로 가는 exec만 있었고 Python 실행·network syscall은 0개였다.
  네트워크 namespace 차단 시험은 실행하지 않았으므로 이를 별도로 통과했다고 쓰지 않는다.
- CPython 3.10.21 / 3.11.16 / 3.12.14 / 3.13.15 / 3.14.7에서 같은 source bytes를
  각각 `ast.parse`와 `compile`로 비교했다. 이 oracle은 개발 harness이며 제품에 넣지 않는다.
- 한 번의 probe 실행은 0.02초, peak RSS 9,420 KiB였다. fixture 생성·컴파일 비용이
  제외된 작은 corpus 수치이며 전체 ici의 성능 개선·cold/warm budget 근거로 확대하지 않는다.

## source와 위치 경계

제품의 공유 `DecodedPythonSource`는 원본 bytes/digest, encoding, BOM 상태, decoded UTF-8,
원본-byte↔decoded-byte 대응, line index를 함께 소유한다. parser에는 decoded UTF-8 `&str`을
주고 AST·token 범위는 이 문자열의 half-open byte range로 관리한다. 범위의 경계와 UTF-8
문자 경계를 검사한 뒤 domain `SourceSpan`의 **1-based line/Unicode character column**으로
변환한다. CPython AST의 0-based UTF-8 byte column을 display column으로 그대로 쓰지 않는다.

실제 `한국어 = 1; 값 = 한국어`에서 세 이름의 byte column은 0/15/21,
character column은 0/9/13이다. 따라서 domain column은 1/10/14다. combining identifier의
원문 `café`는 6 bytes지만 AST binding 이름은 NFKC-normalized `café`이며 원문 범위를 유지한다.
raw 식별자 text와 semantic binding key를 같은 문자열로 대체하지 않는다.

CRLF와 lone CR 모두 line break로 처리하며 CRLF를 두 줄로 세지 않는다. parser에 넣기 전
newline 문자를 정규화하지 않아서 원본 위치를 유지한다. UTF-8 BOM은 decoder에서 제거하고
원본 prefix 3 bytes를 map에 기록한다. BOM/encoding cookie 충돌·잘못된 UTF-8/ASCII는 명시
실패이며 `errors="replace"`로 조용히 정상 source를 만들어 내지 않는다.

실험 decoder는 UTF-8/BOM, ASCII, Latin-1만 검증했다. CP1252 입력은 CPython이 읽지만 이
prototype는 `unsupported-encoding`으로 보고한다. 이는 **known limitation**이며 전체 codec
지원이나 기존 입력 parity가 완료됐다는 뜻이 아니다. production decoder의 지원 범위를
원장에 등록하고 지원하지 않는 encoding은 해당 파일의 incomplete 근거로 남겨야 한다.
Latin-1의 완전한 raw-byte map은 prototype에 없으므로 adapter 구현·fixture가 추가로 필요하다.

## AST·token·문법 계약

`parse_unchecked(source, ParseOptions::from(Mode::Module).with_target_version(version))`를
한 번 호출하여 AST·tokens·parse errors·unsupported syntax errors를 함께 얻는다.
`has_valid_syntax()`와 `parse_module()`의 Result는 target-version 오류를 포함하지 않는다.
제품의 문법 완결성은 양쪽 오류 목록이 비어 있는지 검사해야 한다.
[고정 parser API](https://github.com/astral-sh/ruff/blob/11c76bf48fdac06b2f240cba502eda96da4dce77/crates/ruff_python_parser/src/lib.rs)

실측으로 f-string PEP 701(3.12), match(3.10), except-star(3.11), type alias(3.12),
type parameter default(3.13), t-string(3.14)의 허용 target과 이전 target 거부를 확인했다.
parser 최신 문법을 읽는 능력과 프로젝트가 선언한 target의 호환 판정은 별개다.
대상 버전은 프로젝트 설정/명시 옵션에서 결정하며 ici의 실행 host Python으로 추정하지 않는다.

복구 사례는 잘못된 함수 앞뒤의 두 assignment를 AST에 유지한다. 이때 parse errors가
있으므로 파일 전체를 완전한 분석으로 표시하거나 사라진 finding의 resolved 근거로 쓰지
않는다. token의 Comment·NonLogicalNewline·원문 range를 보존하므로 semantic duplicate의
기존 token normalization은 별도로 이식·검증할 수 있다. CPython tokenize와 동일하다고
가정해서 token enum이나 f-string 분할 방식을 그대로 fingerprint에 사용하지 않는다.

`Mod`는 AST의 serde feature만 켠다고 Serialize가 되지 않는다. 자체 visitor/adapter를
사용하고 Ruff 내부 구조를 ici result/schema로 노출하지 않는다. `SourceOrderVisitor`와
`Visitor`의 source-order/evaluation-order 차이도 명시적으로 선택한다.

## scope·semantic 경계와 교정 fixture

AST에는 import alias, nested function, global/nonlocal, comprehension, async function/for,
await/yield가 모두 보존되는 것을 확인했다. 그러나 이 shape 확인은 binding resolver가 아니다.
R4의 공유 scope graph는 module/class/function/lambda/comprehension 경계, parameter binding,
alias·shadowing·global/nonlocal·comprehension 평가 순서와 async scope를 구현해야 한다.
특히 `import os; lambda os: os.system(...)`를 imported os 호출로 판정하면 안 된다.

두 oracle 모두 module-level `return`/`nonlocal`을 ast.parse로 읽지만 compile은 거부한다.
Ruff grammar도 이를 읽는다. 또한 250겹 괄호는 Ruff가 받지만 CPython은 ast.parse부터
거부한다. parser 성공만을 CPython runtime 호환 PASS로 사용하지 않는다. 자체 소스 분석의
완결성·semantic syntax diagnostics·runtime compatibility evidence는 구분하고, native
semantic checker와 shared scope context를 연결해 필요한 의미를 검증한다.

Ruff의 `SemanticSyntaxChecker`는 scope context와 visitor를 소비자가 제공해야 한다.
위 37개 fixture는 이 전체 semantic checker를 구현한 것이 아니다. production resolver에서
해당 known differences의 처분과 양성/음성/실패 기대값을 추가하기 전 R4를 완료 처리하지 않는다.
[semantic checker source](https://github.com/astral-sh/ruff/blob/11c76bf48fdac06b2f240cba502eda96da4dce77/crates/ruff_python_parser/src/semantic_errors.rs)

## 자원·지원 한계와 다음 인수

prototype는 source 1 MiB 초과를 parser 진입 전에 `source-limit`으로 거부하는 fixture를
통과했다. 이 숫자는 평가 harness 정책이며 최종 제품의 용량 약속이 아니다. 제품은 source
byte/node/nesting budget과 취소 경계를 명시하고 초과 시 해당 파일의 incomplete·위치·이유를
남겨야 한다. 복잡한 재귀 입력·panic·timeout·취소·대형 repository 예산은 R2/R4/R8에서
실제 실패 주입으로 검증한다. 작은 flat 60KB 입력 통과를 자원 안전성 전체로 주장하지 않는다.

GNU static parser 링크를 실측했지만 musl compiler 부재로 musl 빌드는 실행하지 않았다.
이 binary가 쓰는 host SDK보다 오래된 배포판, CPU ISA 하한, macOS/native Windows,
제품 전체 TLS·tool pack·installer의 정적 링크 및 재현 build는 이번 실험 범위 밖이다.
표준 설치 archive의 지원표는 별도 R8 실측으로 확정한다.

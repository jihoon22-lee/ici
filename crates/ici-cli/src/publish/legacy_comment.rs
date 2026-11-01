//! Frozen `engines/publish.py` sticky PR comment for historical `ici publish`: the suite header
//! with TEM, the stats table, the baseline delta, the engine details and the footer. Built only
//! from the saved legacy suite document; it never re-judges the result.
use ici_execution::redaction::redact_text;
use serde_json::Value;

pub const MARKER: &str = "<!-- ici-report -->";
/// `quote('📊_HTML_리포트_열기')`, as the frozen badge URL spells it.
const BADGE: &str = "%F0%9F%93%8A_HTML_%EB%A6%AC%ED%8F%AC%ED%8A%B8_%EC%97%B4%EA%B8%B0";

/// The frozen `load_suite_from_json` view of a saved document: `None` unless it is a readable
/// v2/v3 suite whose statuses all parse.
pub struct Suite {
    status: String,
    results: Vec<(String, String, String)>,
    tem: Option<f64>,
    max_tem: f64,
    baseline: Option<Baseline>,
}
struct Baseline {
    new: u64,
    regressed: u64,
    gated: u64,
    fail_on_new: bool,
    gate_failed: bool,
    warnings: Vec<String>,
}

fn known(status: &str) -> bool {
    matches!(status, "PASS" | "WARN" | "FAIL" | "ERROR" | "SKIP")
}
fn emoji(status: &str) -> &'static str {
    match status {
        "PASS" => "✅",
        "WARN" => "⚠️",
        "FAIL" => "❌",
        "ERROR" => "⛔",
        "SKIP" => "⏭️",
        _ => "📋",
    }
}
/// Python `str(item.get(key, default))`: an absent key takes the default, a present `null`
/// reads as `None`, and other scalars use their Python spelling.
fn text(value: Option<&Value>, default: &str) -> String {
    match value {
        None => default.into(),
        Some(Value::Null) => "None".into(),
        Some(Value::Bool(true)) => "True".into(),
        Some(Value::Bool(false)) => "False".into(),
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.to_string(),
    }
}
/// Python `isinstance(value, (int, float))`, where a boolean is an integer.
fn number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_bool().map(|flag| f64::from(u8::from(flag))))
}
/// Python `float(value)` for the engine duration: numbers, booleans and numeric strings.
fn duration(value: Option<&Value>) -> bool {
    match value {
        None => true,
        Some(Value::String(text)) => text.trim().parse::<f64>().is_ok(),
        Some(other) => number(other).is_some(),
    }
}

pub fn suite(document: &Value) -> Option<Suite> {
    let results = document.get("results")?.as_array()?;
    if !matches!(
        document.get("schema_version").and_then(Value::as_str),
        Some("ici.result/v2" | "ici.result/v3")
    ) {
        return None;
    }
    let mut rows = vec![];
    for item in results {
        item.as_object()?;
        let status = text(item.get("status"), "SKIP");
        if !known(&status) || !duration(item.get("duration")) {
            return None;
        }
        rows.push((
            redact_text(&text(item.get("engine_name"), "?")),
            status,
            redact_text(&text(item.get("summary"), "")),
        ));
    }
    let status = text(document.get("suite_status"), "WARN");
    if !known(&status) {
        return None;
    }
    Some(Suite {
        status,
        results: rows,
        tem: document.get("tem_score").and_then(number),
        max_tem: document
            .get("max_tem_score")
            .map_or(Some(5.0), number)
            .unwrap_or(5.0),
        baseline: document.get("baseline_comparison").and_then(baseline),
    })
}

/// Frozen `parse_baseline_summary`: every declared count must be a strict non-negative integer
/// equal to what the entries say, and the gate flag must follow from them.
fn baseline(value: &Value) -> Option<Baseline> {
    let object = value.as_object()?;
    let count = |key: &str| object.get(key)?.as_u64();
    for key in [
        "source_path",
        "warnings",
        "baseline_metadata",
        "fail_on_new",
        "gate_failed",
        "entries",
    ] {
        object.get(key)?;
    }
    let source = object["source_path"].as_str()?;
    let warnings: Vec<String> = object["warnings"]
        .as_array()?
        .iter()
        .map(|item| {
            item.as_str()
                .filter(|text| !text.is_empty())
                .map(redact_text)
        })
        .collect::<Option<_>>()?;
    if source.is_empty()
        || !(object["baseline_metadata"].is_null() || object["baseline_metadata"].is_object())
    {
        return None;
    }
    let fail_on_new = object["fail_on_new"].as_bool()?;
    let gate_failed = object["gate_failed"].as_bool()?;
    let declared = [
        count("new_count")?,
        count("unchanged_count")?,
        count("moved_count")?,
        count("resolved_count")?,
        count("regressed_count")?,
        count("gated_count")?,
    ];
    let mut observed = [0_u64; 6];
    for entry in object["entries"].as_array()? {
        let state = match entry.get("state")?.as_str()? {
            "new" => 0,
            "unchanged" => 1,
            "moved" => 2,
            "resolved" => 3,
            _ => return None,
        };
        let regressed = entry.get("regressed")?.as_bool()?;
        let gated = entry.get("gated")?.as_bool()?;
        if regressed && !matches!(state, 1 | 2) || gated && state != 0 && !regressed {
            return None;
        }
        observed[state] += 1;
        observed[4] += u64::from(regressed);
        observed[5] += u64::from(gated);
    }
    if declared != observed || gate_failed != (fail_on_new && declared[5] > 0) {
        return None;
    }
    Some(Baseline {
        new: declared[0],
        regressed: declared[4],
        gated: declared[5],
        fail_on_new,
        gate_failed,
        warnings,
    })
}

fn tem(suite: &Suite) -> Option<String> {
    suite
        .tem
        .map(|score| format!("`{score:.2}/{:.1}`", suite.max_tem))
}
fn header(suite: Option<&Suite>) -> String {
    let Some(suite) = suite else {
        return "## 🛡️ ici Quality Gate Report".into();
    };
    let tem = tem(suite).map_or_else(String::new, |value| format!(" · TEM **{value}**"));
    format!(
        "## {} ici Quality Gate — **`{}`**{tem}",
        emoji(&suite.status),
        suite.status
    )
}
fn links(
    viewer: Option<&str>,
    pages_enabled: bool,
    remote_path: &str,
    uploaded: bool,
) -> Vec<String> {
    if !uploaded {
        return vec![
            "> ❌ **HTML 리포트 업로드 실패** — 토큰 권한(`contents: write`)과 Workflow Run 로그를 확인해 주세요.".into(),
        ];
    }
    if let Some(url) = viewer {
        return vec![
            format!(
                "[![Open Report](https://img.shields.io/badge/{BADGE}-38bdf8?style=for-the-badge)]({url})"
            ),
            String::new(),
            format!("<sub>뷰어 URL: {url}</sub>"),
        ];
    }
    if pages_enabled {
        return vec![format!("📄 리포트가 `{remote_path}`에 게시되었습니다.")];
    }
    vec![format!(
        "> 📦 리포트가 `{remote_path}`(gh-pages)에 푸시되었습니다. **최초 1회** Settings → Pages → Source에서 `gh-pages`를 선택하면 이 댓글에 뷰어 링크가 표시됩니다."
    )]
}
fn stats(suite: Option<&Suite>) -> Vec<String> {
    let Some(suite) = suite else {
        return vec!["> ⚠️ 스위트 요약 JSON이 제공되지 않아 엔진 상세가 생략되었습니다.".into()];
    };
    let count = |status: &str| {
        suite
            .results
            .iter()
            .filter(|(_, value, _)| value == status)
            .count()
    };
    vec![
        "| ✅ Pass | ⚠️ Warn | ❌ Fail | ⛔ Error | ⏭️ Skip | 🎯 TEM |".into(),
        "|:---:|:---:|:---:|:---:|:---:|:---:|".into(),
        format!(
            "| {} | {} | {} | {} | {} | {} |",
            count("PASS"),
            count("WARN"),
            count("FAIL"),
            count("ERROR"),
            count("SKIP"),
            tem(suite).unwrap_or_else(|| "—".into())
        ),
    ]
}
/// Python `str.splitlines` boundaries, joined by one space, then the frozen comment escapes.
fn escape_comment(value: &str) -> String {
    let value = value.replace('\r', "\n");
    let compact = value
        .split([
            '\n', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
        ])
        .collect::<Vec<_>>();
    let mut parts = compact;
    if parts.last() == Some(&"") {
        parts.pop();
    }
    parts
        .join(" ")
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('`', "&#96;")
        .replace('|', "&#124;")
}
fn baseline_lines(suite: Option<&Suite>) -> Vec<String> {
    let Some(baseline) = suite.and_then(|suite| suite.baseline.as_ref()) else {
        return vec![];
    };
    let gate = if baseline.gate_failed {
        "❌ FAILED"
    } else if baseline.fail_on_new {
        "✅ PASSED"
    } else {
        "NOT ENFORCED"
    };
    let mut lines = vec![format!(
        "> 🔎 **Baseline delta**: new **{}** · regressed **{}** · gated **{}** · gate **{gate}**",
        baseline.new, baseline.regressed, baseline.gated
    )];
    if let Some(first) = baseline.warnings.first() {
        let remaining = baseline.warnings.len() - 1;
        let suffix = if remaining > 0 {
            format!(" (+{remaining} more)")
        } else {
            String::new()
        };
        lines.push(format!(
            "> ⚠️ **Baseline warnings ({})**: {}{suffix}",
            baseline.warnings.len(),
            escape_comment(first)
        ));
    }
    lines
}
fn details(suite: Option<&Suite>) -> Vec<String> {
    let Some(suite) = suite.filter(|suite| !suite.results.is_empty()) else {
        return vec![];
    };
    let mut rows: Vec<_> = suite.results.iter().collect();
    // Python sorts by the emoji string (unknown statuses as "z"); UTF-8 byte order is code
    // point order, and the sort is stable.
    rows.sort_by_key(|(_, status, _)| if known(status) { emoji(status) } else { "z" });
    let mut lines = vec![
        "<details>".into(),
        "<summary><b>🔍 엔진 상세 결과</b></summary>".into(),
        String::new(),
        "| Engine | Status | Summary |".into(),
        "|---|:---:|---|".into(),
    ];
    for (name, status, summary) in rows {
        lines.push(format!(
            "| `{name}` | {} `{status}` | {} |",
            emoji(status),
            summary.replace('|', "\\|").replace('\n', " ")
        ));
    }
    lines.extend([String::new(), "</details>".into()]);
    lines
}
fn footer(mode: &str, remote_path: &str, run_url: Option<&str>) -> String {
    let mut parts = vec![format!("모드 `{mode}`"), format!("경로 `{remote_path}`")];
    if let Some(url) = run_url {
        parts.push(format!("[⚙️ Workflow Run]({url})"));
    }
    parts.push("<sub>generated by ici</sub>".into());
    parts.join("<br/>")
}

/// Frozen `_build_comment_body` for one unlabeled report.
pub fn single(
    suite: Option<&Suite>,
    viewer: Option<&str>,
    uploaded: bool,
    pages_enabled: bool,
    mode: &str,
    remote_path: &str,
    run_url: Option<&str>,
) -> String {
    let mut lines = vec![MARKER.to_owned(), header(suite), String::new()];
    lines.extend(links(viewer, pages_enabled, remote_path, uploaded));
    lines.push(String::new());
    lines.extend(stats(suite));
    let baseline = baseline_lines(suite);
    if !baseline.is_empty() {
        lines.push(String::new());
        lines.extend(baseline);
    }
    lines.extend(details(suite));
    lines.push(String::new());
    lines.push(footer(mode, remote_path, run_url));
    lines.join("\n")
}

/// One published report of a multi-project comment.
pub struct Report<'a> {
    pub label: &'a str,
    pub suite: Option<Suite>,
    pub remote_path: String,
    pub viewer: Option<String>,
    pub uploaded: bool,
}

/// Frozen `_multi_report_body`: one sticky comment covering every project of the run.
pub fn multi(
    reports: &[Report<'_>],
    mode: &str,
    run_url: Option<&str>,
    pages_enabled: bool,
) -> String {
    let rank = |status: &str| {
        ["PASS", "SKIP", "WARN", "FAIL", "ERROR"]
            .iter()
            .position(|item| *item == status)
            .unwrap_or(0)
    };
    // Python max(): the first of equal keys wins.
    let mut worst: Option<&str> = None;
    for status in reports
        .iter()
        .filter_map(|report| report.suite.as_ref().map(|s| s.status.as_str()))
    {
        if worst.is_none_or(|current| rank(status) > rank(current)) {
            worst = Some(status);
        }
    }
    let label = |report: &Report<'_>| {
        if report.label.is_empty() {
            ".".to_owned()
        } else {
            report.label.to_owned()
        }
    };
    let mut lines = vec![
        MARKER.to_owned(),
        format!(
            "## {} ici Quality Gate — {}개 프로젝트",
            worst.map_or("📋", emoji),
            reports.len()
        ),
        String::new(),
        "| 프로젝트 | 상태 | TEM | 리포트 |".into(),
        "|---|:---:|:---:|:---:|".into(),
    ];
    for report in reports {
        let status = report.suite.as_ref().map_or_else(
            || "—".to_owned(),
            |suite| format!("{} `{}`", emoji(&suite.status), suite.status),
        );
        let tem = report
            .suite
            .as_ref()
            .and_then(tem)
            .unwrap_or_else(|| "—".into());
        let link = if !report.uploaded {
            "❌ 업로드 실패".to_owned()
        } else {
            report.viewer.as_ref().map_or_else(
                || format!("`{}`", report.remote_path),
                |url| format!("[📊 열기]({url})"),
            )
        };
        lines.push(format!(
            "| `{}` | {status} | {tem} | {link} |",
            label(report)
        ));
    }
    if !pages_enabled {
        lines.extend([
            String::new(),
            "> 📦 리포트가 gh-pages 에 푸시되었습니다. **최초 1회** Settings → Pages → Source 에서 `gh-pages` 를 선택하면 이 표에 뷰어 링크가 표시됩니다.".into(),
        ]);
    }
    for report in reports {
        let details = details(report.suite.as_ref());
        let baseline = baseline_lines(report.suite.as_ref());
        if details.is_empty() && baseline.is_empty() {
            continue;
        }
        lines.push(String::new());
        lines.push(format!("#### `{}`", label(report)));
        lines.extend(stats(report.suite.as_ref()));
        if !baseline.is_empty() {
            lines.push(String::new());
            lines.extend(baseline);
        }
        if !details.is_empty() {
            lines.push(String::new());
            lines.extend(details);
        }
    }
    let paths: Vec<_> = reports
        .iter()
        .map(|report| report.remote_path.as_str())
        .collect();
    lines.extend([
        String::new(),
        "---".into(),
        footer(mode, &paths.join(", "), run_url),
    ]);
    lines.join("\n")
}

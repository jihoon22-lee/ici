//! Bounded GNU readelf facts and policy. Parsing never loads or executes an artifact.
use crate::{
    CppError,
    analysis::{finding, measurement},
};
use ici_domain::{AnalysisOutput, AnalysisRequest, InspectionTarget, TaskState};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    sync::LazyLock,
};

static HEADER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s*(Class|Machine|Type):\s*(.+?)\s*$").unwrap());
static NEEDED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\(NEEDED\).*?\[(.*?)\]").unwrap());
static RPATH: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\((RPATH|RUNPATH)\).*?\[(.*?)\]").unwrap());
static VERSION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(GLIBCXX|GLIBC|CXXABI)_([0-9]+(?:\.[0-9]+)+)\b").unwrap());
static SECTION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\]\s+(\.\S+)\s+").unwrap());
static NO_DYNAMIC: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^\s*There is no dynamic section in this file\.\s*$").unwrap()
});
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ElfFacts {
    pub elf_class: String,
    pub machine: String,
    pub elf_type: String,
    pub needed: Vec<String>,
    pub rpath: Vec<String>,
    pub runpath: Vec<String>,
    pub glibc: Vec<String>,
    pub glibcxx: Vec<String>,
    pub cxxabi: Vec<String>,
    pub stripped: bool,
    pub dynamic: bool,
}
pub fn version_key(value: &str) -> Result<Vec<u32>, CppError> {
    if value.is_empty() || value.len() > 128 {
        return Err(CppError::new(
            "invalid-abi-version",
            "ABI version must be a bounded numeric dotted version",
        ));
    }
    value
        .split('.')
        .map(|part| {
            if part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) {
                return Err(CppError::new(
                    "invalid-abi-version",
                    format!("invalid ABI version {value:?}"),
                ));
            }
            part.parse::<u32>()
                .map_err(|_| CppError::new("invalid-abi-version", "ABI version component overflow"))
        })
        .collect()
}
pub fn maximum_version(values: &[String]) -> Result<String, CppError> {
    let mut rows = values
        .iter()
        .map(|value| Ok((version_key(value)?, value)))
        .collect::<Result<Vec<_>, CppError>>()?;
    rows.sort();
    Ok(rows
        .last()
        .map_or(String::new(), |(_, value)| (*value).clone()))
}
pub fn parse_readelf(text: &str) -> Result<ElfFacts, CppError> {
    if text.len() > 8 * 1024 * 1024 || text.contains('\0') {
        return Err(CppError::new(
            "readelf-bounds",
            "readelf output exceeds bounded text contract",
        ));
    }
    let mut headers = BTreeMap::new();
    for capture in HEADER.captures_iter(text) {
        let name = capture[1].to_ascii_lowercase();
        let value = capture[2].trim().to_owned();
        if let Some(previous) = headers.insert(name, value.clone())
            && previous != value
        {
            return Err(CppError::new(
                "readelf-header-conflict",
                "readelf contains inconsistent repeated headers",
            ));
        }
    }
    for name in ["class", "machine", "type"] {
        if !headers.contains_key(name) {
            return Err(CppError::new(
                "readelf-header-missing",
                format!("readelf lacks ELF {name}"),
            ));
        }
    }
    let mut paths: BTreeMap<&str, Vec<String>> =
        BTreeMap::from([("RPATH", vec![]), ("RUNPATH", vec![])]);
    for capture in RPATH.captures_iter(text) {
        paths.get_mut(&capture[1]).unwrap().extend(
            capture[2]
                .split(':')
                .filter(|part| !part.is_empty())
                .map(str::to_owned),
        );
    }
    let mut versions: BTreeMap<&str, BTreeSet<String>> = BTreeMap::from([
        ("GLIBC", BTreeSet::new()),
        ("GLIBCXX", BTreeSet::new()),
        ("CXXABI", BTreeSet::new()),
    ]);
    for capture in VERSION.captures_iter(text) {
        version_key(&capture[2])?;
        versions
            .get_mut(&capture[1])
            .unwrap()
            .insert(capture[2].to_owned());
    }
    let mut sorted_versions = |name| {
        let mut values: Vec<_> = versions.remove(name).unwrap().into_iter().collect();
        values.sort_by_key(|value| version_key(value).expect("validated version"));
        values
    };
    let sections: BTreeSet<_> = SECTION
        .captures_iter(text)
        .map(|capture| capture[1].to_owned())
        .collect();
    Ok(ElfFacts {
        elf_class: headers["class"]
            .split_whitespace()
            .next()
            .unwrap_or("")
            .into(),
        machine: headers["machine"].clone(),
        elf_type: headers["type"]
            .split_whitespace()
            .next()
            .unwrap_or("")
            .into(),
        needed: NEEDED
            .captures_iter(text)
            .map(|capture| capture[1].to_owned())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
        rpath: paths.remove("RPATH").unwrap(),
        runpath: paths.remove("RUNPATH").unwrap(),
        glibc: sorted_versions("GLIBC"),
        glibcxx: sorted_versions("GLIBCXX"),
        cxxabi: sorted_versions("CXXABI"),
        stripped: !sections.contains(".symtab"),
        dynamic: !NO_DYNAMIC.is_match(text),
    })
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ElfPolicy {
    pub expected_class: String,
    pub expected_machine: String,
    pub max_glibc: String,
    pub max_glibcxx: String,
    pub max_cxxabi: String,
    pub require_static: bool,
    pub forbid_absolute_rpath: bool,
    pub forbid_build_paths: bool,
    pub forbidden_needed: Vec<String>,
    pub allowed_needed: Vec<String>,
}
impl Default for ElfPolicy {
    fn default() -> Self {
        Self {
            expected_class: String::new(),
            expected_machine: String::new(),
            max_glibc: String::new(),
            max_glibcxx: String::new(),
            max_cxxabi: String::new(),
            require_static: false,
            forbid_absolute_rpath: true,
            forbid_build_paths: true,
            forbidden_needed: vec![],
            allowed_needed: vec![],
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AbiViolation {
    pub rule_id: String,
    pub native_rule_id: String,
    pub message: String,
}
pub fn abi_violations(
    facts: &ElfFacts,
    policy: &ElfPolicy,
    build_roots: &[PathBuf],
) -> Result<Vec<AbiViolation>, CppError> {
    let mut violations = vec![];
    let mut add = |rule: &str, native: &str, message: String| {
        violations.push(AbiViolation {
            rule_id: format!("ici.binary.{rule}"),
            native_rule_id: format!("elf.{native}"),
            message,
        })
    };
    for (label, actual, expected) in [
        ("class", &facts.elf_class, &policy.expected_class),
        ("machine", &facts.machine, &policy.expected_machine),
    ] {
        if !expected.is_empty() && actual != expected {
            add(
                &format!("{label}-mismatch"),
                &format!("header.{label}"),
                format!("ELF {label} {actual:?} does not match expected {expected:?}"),
            );
        }
    }
    for (name, versions, maximum) in [
        ("glibc", &facts.glibc, &policy.max_glibc),
        ("glibcxx", &facts.glibcxx, &policy.max_glibcxx),
        ("cxxabi", &facts.cxxabi, &policy.max_cxxabi),
    ] {
        let actual = maximum_version(versions)?;
        if !maximum.is_empty() {
            let bound = version_key(maximum)?;
            if !actual.is_empty() && version_key(&actual)? > bound {
                add(
                    &format!("{name}-floor"),
                    &format!("version.{name}"),
                    format!(
                        "Maximum required {} {actual} exceeds configured {maximum}",
                        name.to_ascii_uppercase()
                    ),
                );
            }
        }
    }
    if policy.require_static && facts.dynamic {
        add(
            "dynamic-linkage",
            "linkage.dynamic",
            "Artifact is dynamically linked but the policy requires static linkage".into(),
        );
    }
    let paths: Vec<_> = facts.rpath.iter().chain(&facts.runpath).collect();
    if policy.forbid_absolute_rpath {
        let absolute: Vec<_> = paths
            .iter()
            .filter(|value| value.starts_with('/'))
            .map(|value| value.as_str())
            .collect();
        if !absolute.is_empty() {
            add(
                "forbidden-rpath",
                "rpath.forbidden",
                format!(
                    "ELF loader path contains absolute entries: {}",
                    absolute.join(", ")
                ),
            );
        }
    }
    if policy.forbid_build_paths {
        let leaked: Vec<_> = paths
            .iter()
            .filter(|value| {
                let path = Path::new(value.as_str());
                path.is_absolute()
                    && build_roots
                        .iter()
                        .any(|root| crate::input::normalize(path).starts_with(root))
            })
            .map(|value| value.as_str())
            .collect();
        if !leaked.is_empty() {
            add(
                "build-path-leak",
                "rpath.build-path",
                format!("ELF loader path exposes build roots: {}", leaked.join(", ")),
            );
        }
    }
    let forbidden: BTreeSet<_> = policy.forbidden_needed.iter().collect();
    let allowed: BTreeSet<_> = policy.allowed_needed.iter().collect();
    let blocked: Vec<_> = facts
        .needed
        .iter()
        .filter(|name| forbidden.contains(name))
        .collect();
    let outside: Vec<_> = facts
        .needed
        .iter()
        .filter(|name| !allowed.is_empty() && !allowed.contains(name))
        .collect();
    let names = if blocked.is_empty() { outside } else { blocked };
    if !names.is_empty() {
        add(
            "forbidden-dependency",
            "dynamic.needed",
            format!(
                "ELF requires disallowed dependencies: {}",
                names
                    .iter()
                    .map(|name| name.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        );
    }
    Ok(violations)
}
pub fn analyze_elf(
    request: &AnalysisRequest,
    path: &str,
    facts: &ElfFacts,
    policy: &ElfPolicy,
    build_roots: &[PathBuf],
) -> Result<AnalysisOutput, CppError> {
    let mut output = AnalysisOutput::new(&request.task_id, "readelf");
    output.observation.state = TaskState::Succeeded;
    let violations = abi_violations(facts, policy, build_roots)?;
    if violations.is_empty() {
        let mut target = InspectionTarget::at(path, 1);
        target.target_name = "BinaryCompatibility".into();
        target.message = "ELF dependency, loader path and ABI facts satisfy policy".into();
        output.targets.push(target);
    }
    for violation in violations {
        let mut target = InspectionTarget::at(path, 1);
        target.target_name = violation.native_rule_id.clone();
        target.message = violation.message;
        target.status = "FAIL".into();
        finding(
            &mut output,
            request,
            &target,
            &violation.rule_id,
            "high",
            false,
        );
        output
            .observation
            .findings
            .last_mut()
            .unwrap()
            .native_rule_id = violation.native_rule_id;
        output.targets.push(target);
    }
    output.status = if output.observation.findings.is_empty() {
        "PASS"
    } else {
        "FAIL"
    }
    .into();
    measurement(&mut output, "artifacts_checked", 1, false);
    output.extra = serde_json::json!({"facts":facts});
    Ok(output)
}

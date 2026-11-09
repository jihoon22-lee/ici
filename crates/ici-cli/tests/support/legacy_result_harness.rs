// Shared full legacy-result comparison of the frozen-replay tests (`legacy_engine_extras.rs`,
// `legacy_cpp_compiler_boundaries.rs`): the difference kinds and allowance classes, the
// normalized-path JSON walk, the per-path allowance lookup with its stale check, and the
// accepted exit-code difference. Each including test file includes
// `support/legacy_result_allowances.rs` after this file and defines nothing of the same name.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    /// Frozen wrote the key, native does not.
    Missing,
    /// Native wrote a key frozen does not.
    NativeOnly,
    /// Different JSON types (`null` vs a value included).
    Type,
    /// Lists of different lengths.
    Len,
    /// Same type, different value.
    Value,
}
use Kind::{Len, Missing, NativeOnly, Type, Value as Differs};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Class {
    Incidental,
    Approved,
}
use Class::{Approved, Incidental};

/// (capture or exact replay label, normalized path, kind, class, reason).
type Allowance = (&'static str, &'static str, Kind, Class, &'static str);
/// (text projection label, side (`Missing` frozen line / `NativeOnly` native line), line prefix,
/// `line$` or `head*tail` pattern after the duration mask, class, reason).
type TextAllowance = (&'static str, Kind, &'static str, Class, &'static str);

thread_local! {
    static HITS: RefCell<BTreeSet<usize>> = const { RefCell::new(BTreeSet::new()) };
}

fn allowed(label: &str, path: &str, kind: Kind) -> bool {
    let found = ALLOWANCES.iter().position(|entry| {
        (entry.0 == label || label.starts_with(&format!("{}/", entry.0)))
            && entry.1 == path
            && entry.2 == kind
            && !entry.4.is_empty()
    });
    if let Some(index) = found {
        HITS.with(|hits| hits.borrow_mut().insert(index));
    }
    found.is_some()
}

/// Positions are normalized (`[3]` -> `[]`) so an allowance names one path of every element.
fn walk(frozen: &Value, native: &Value, path: &str, out: &mut Vec<(String, Kind)>) {
    // The capability inventory is compared in full except its row set: frozen probed every
    // registered tool, native only the policy's (config.json capability:doctor-env-cache (3)).
    // Policy rows (required or optional by an engine) are keyed by tool name and walked field by
    // field; a frozen row no engine uses is one `tools[unused]` path.
    if path.ends_with("capability_inventory.tools")
        && let (Value::Array(left), Value::Array(right)) = (frozen, native)
    {
        let rows: BTreeMap<&str, &Value> = right
            .iter()
            .map(|row| (row["name"].as_str().unwrap_or(""), row))
            .collect();
        let used = |row: &Value| row["required"] == true || row["optional"] == true;
        // A probed executable's location is compared through its symlinks (`/bin` is
        // `/usr/bin` on a merged-/usr host); value differences name the tool.
        let canonical = |row: &Value| {
            let mut row = row.clone();
            if let Some(found) = row["path"].as_str().filter(|path| path.starts_with('/'))
                && let Ok(resolved) = std::fs::canonicalize(found)
            {
                row["path"] = json!(resolved.display().to_string());
            }
            row
        };
        for row in left {
            let name = row["name"].as_str().unwrap_or("");
            match rows.get(name) {
                Some(other) => {
                    let mut found = vec![];
                    walk(
                        &canonical(row),
                        &canonical(other),
                        &format!("{path}[]"),
                        &mut found,
                    );
                    out.extend(found.into_iter().map(|(field, kind)| match kind {
                        Missing | NativeOnly => (field, kind),
                        _ => (field.replacen("[]", &format!("[{name}]"), 1), kind),
                    }));
                }
                None if used(row) => out.push((format!("{path}[{name}]"), Missing)),
                None => out.push((format!("{path}[unused]"), Missing)),
            }
        }
        // The rows both sides list keep frozen's order (its probe registry order).
        let order = |rows: &[Value], other: &BTreeMap<&str, &Value>| -> Vec<String> {
            rows.iter()
                .filter_map(|row| row["name"].as_str())
                .filter(|name| other.contains_key(name))
                .map(str::to_owned)
                .collect()
        };
        let frozen_rows: BTreeMap<&str, &Value> = left
            .iter()
            .map(|row| (row["name"].as_str().unwrap_or(""), row))
            .collect();
        if order(left, &rows) != order(right, &frozen_rows) {
            out.push((format!("{path}[order]"), Differs));
        }
        let names: BTreeSet<&str> = left.iter().filter_map(|row| row["name"].as_str()).collect();
        for row in right {
            if !names.contains(row["name"].as_str().unwrap_or("")) {
                out.push((format!("{path}[native]"), NativeOnly));
            }
        }
        return;
    }
    // Support-matrix rows are keyed by engine and language, never by position.
    if path.ends_with("support_matrix.entries")
        && let (Value::Array(left), Value::Array(right)) = (frozen, native)
    {
        let key = |row: &Value| {
            format!(
                "{}/{}",
                row["engine_name"].as_str().unwrap_or(""),
                row["language"].as_str().unwrap_or("")
            )
        };
        let rows: BTreeMap<String, &Value> = right.iter().map(|row| (key(row), row)).collect();
        if left.len() != right.len() {
            out.push((path.to_owned(), Len));
        }
        for row in left {
            match rows.get(&key(row)) {
                Some(other) => walk(row, other, &format!("{path}[{}]", key(row)), out),
                None => out.push((format!("{path}[{}]", key(row)), Missing)),
            }
        }
        return;
    }
    match (frozen, native) {
        (Value::Object(left), Value::Object(right)) => {
            for (key, value) in left {
                let child = format!("{path}.{key}");
                match right.get(key) {
                    Some(other) => walk(value, other, &child, out),
                    None => out.push((child, Missing)),
                }
            }
            for key in right.keys().filter(|key| !left.contains_key(*key)) {
                out.push((format!("{path}.{key}"), NativeOnly));
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            if left.len() != right.len() {
                out.push((path.to_owned(), Len));
            }
            for (a, b) in left.iter().zip(right) {
                walk(a, b, &format!("{path}[]"), out);
            }
        }
        (Value::Number(left), Value::Number(right)) => {
            // An integer and a float are different JSON types (`0` is not `0.0`).
            if left.is_f64() != right.is_f64() {
                out.push((path.to_owned(), Type));
            } else if left.as_f64() != right.as_f64() {
                out.push((path.to_owned(), Differs));
            }
        }
        (left, right) if std::mem::discriminant(left) != std::mem::discriminant(right) => {
            out.push((path.to_owned(), Type));
        }
        (left, right) => {
            if left != right {
                out.push((path.to_owned(), Differs));
            }
        }
    }
}

/// decisions.md "native C++ shadow build 위치": a `C++Syntax:<12 hex>` shadow suffix moves a
/// lint finding's fingerprint and so its place in the fingerprint-sorted list ("이에 따른 lint
/// finding 순서"). Only the pairing changes, never a value: a native finding keeps frozen's
/// slot with the same fingerprint, then one whose fingerprint frozen lacks takes frozen's
/// unpaired slot at the same path, line, rule and message, and the rest fill the remaining
/// slots in native order (extra findings stay at the end).
fn shadow_ordered_lint(frozen: &Value, native: &mut Value) {
    if native["engine_name"] != "lint" {
        return;
    }
    let (Some(left), Some(right)) = (frozen["findings"].as_array(), native["findings"].as_array())
    else {
        return;
    };
    let key = |item: &Value| {
        let location = &item["primary_location"];
        [
            location["path"].clone(),
            location["start_line"].clone(),
            item["rule_id"].clone(),
            item["message"].clone(),
        ]
    };
    let mut pool: Vec<Option<Value>> = right.iter().cloned().map(Some).collect();
    let mut take = |matches: &dyn Fn(&Value) -> bool| {
        pool.iter_mut()
            .find(|slot| slot.as_ref().is_some_and(matches))
            .and_then(Option::take)
    };
    let mut slots: Vec<Option<Value>> = left
        .iter()
        .map(|item| take(&|other| other["fingerprint"] == item["fingerprint"]))
        .collect();
    for (slot, item) in slots.iter_mut().zip(left) {
        if slot.is_none() {
            *slot = take(&|other| key(other) == key(item));
        }
    }
    let mut rest = pool.into_iter().flatten();
    let mut paired: Vec<Value> = slots
        .into_iter()
        .filter_map(|slot| slot.or_else(|| rest.next()))
        .collect();
    paired.extend(rest);
    native["findings"] = Value::Array(paired);
}

/// Every difference of a saved legacy result (a suite, or one engine report when `suite` is
/// false) that no allowance names.
fn result_differences(label: &str, frozen: &Value, native: &Value) -> Vec<String> {
    let mut out = vec![];
    if frozen.get("results").is_some() {
        let (left, right) = (frozen.as_object().unwrap(), native.as_object().unwrap());
        for (key, value) in left.iter().filter(|(key, _)| *key != "results") {
            match right.get(key) {
                Some(other) => walk(value, other, key, &mut out),
                None => out.push((key.clone(), Missing)),
            }
        }
        for key in right.keys().filter(|key| !left.contains_key(*key)) {
            out.push((key.clone(), NativeOnly));
        }
        let names = |suite: &Value| -> Vec<Value> {
            results(suite)
                .iter()
                .map(|engine| engine["engine_name"].clone())
                .collect()
        };
        assert_eq!(names(native), names(frozen), "{label}: engines");
        for (left, right) in results(frozen).iter().zip(results(native)) {
            let path = format!("results[{}]", left["engine_name"].as_str().unwrap());
            let mut right = right.clone();
            shadow_ordered_lint(left, &mut right);
            walk(left, &right, &path, &mut out);
        }
    } else {
        walk(frozen, native, "", &mut out);
    }
    out.into_iter()
        .filter(|(path, kind)| !allowed(label, path, *kind))
        .map(|(path, kind)| format!("{label}: {path} ({kind:?})"))
        .chain(coverage_source_difference(label, frozen, native))
        .collect()
}

/// What coverage.py measures is part of the verdict: a `--source=` value is compared on its own,
/// so no argv allowance (R8, R12, PYCPP_TEST_TOOLS) can hide a change of it.
fn coverage_source_difference(label: &str, frozen: &Value, native: &Value) -> Option<String> {
    fn sources(value: &Value, out: &mut Vec<String>) {
        match value {
            Value::String(text) => {
                if let Some(source) = text.strip_prefix("--source=") {
                    out.push(source.to_owned());
                }
            }
            Value::Array(items) => items.iter().for_each(|item| sources(item, out)),
            Value::Object(map) => map.values().for_each(|item| sources(item, out)),
            _ => {}
        }
    }
    let collect = |value: &Value| {
        let mut out = vec![];
        for record in value
            .get("results")
            .and_then(Value::as_array)
            .map_or_else(|| vec![value], |results| results.iter().collect())
        {
            sources(&record["tool_evidence"], &mut out);
        }
        out
    };
    let (left, right) = (collect(frozen), collect(native));
    (left != right).then(|| format!("{label}: coverage --source {left:?} (native {right:?})"))
}

fn results(suite: &Value) -> &[Value] {
    suite["results"].as_array().unwrap()
}

/// Frozen exit vs native: equal, or the accepted incomplete exit (reporting.json `command:ici
/// verify` difference_reason): native 3 where frozen exited 1, with a required engine whose
/// evidence both frozen and native report NOT_RUN (ERROR).
fn assert_exit(
    label: &str,
    native: &[i32],
    frozen: &[i32],
    frozen_engines: &[Value],
    native_engines: &[Value],
) {
    if native == frozen {
        return;
    }
    assert_eq!(
        (native.last(), frozen.last()),
        (Some(&3), Some(&1)),
        "{label}: exits {native:?} vs frozen {frozen:?}"
    );
    let not_run = |engines: &[Value]| -> BTreeSet<String> {
        engines
            .iter()
            .filter(|engine| {
                engine["required"] == true
                    && engine["status"] == "ERROR"
                    && engine["evidence"] == "NOT_RUN"
            })
            .map(|engine| engine["engine_name"].as_str().unwrap().to_owned())
            .collect()
    };
    let shared: Vec<String> = not_run(frozen_engines)
        .intersection(&not_run(native_engines))
        .cloned()
        .collect();
    assert!(
        !shared.is_empty(),
        "{label}: exit 3 without a required NOT_RUN engine frozen also reported"
    );
}

fn codes(value: &Value) -> Vec<i32> {
    match value {
        Value::Array(items) => items
            .iter()
            .map(|code| code.as_i64().unwrap() as i32)
            .collect(),
        code => vec![code.as_i64().unwrap() as i32],
    }
}

/// Every allowance in `scope` was observed by the replays of this test.
fn assert_not_stale(scope: impl Fn(&Allowance) -> bool) {
    let stale: Vec<String> = HITS.with(|hits| {
        let hits = hits.borrow();
        ALLOWANCES
            .iter()
            .enumerate()
            .filter(|(index, entry)| scope(entry) && !hits.contains(index))
            .map(|(_, entry)| format!("{} {} ({:?})", entry.0, entry.1, entry.2))
            .collect()
    });
    assert!(stale.is_empty(), "stale allowances: {stale:#?}");
}

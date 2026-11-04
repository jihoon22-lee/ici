//! Frozen brace-heuristic C++ function inventory and lexical cognitive metric.
//!
//! Line-for-line port of the frozen `analysis/cpp_text.py` helpers, the `_CppScanner` /
//! `_cpp_function_inventory` path of `analysis/cpp_complexity.py`, `cpp_cognitive_metric` of
//! `analysis/_cpp_cognitive.py` and `languages/metrics.py::_slice_body`. These are pure source
//! heuristics: they never run a tool and never claim compiler-grade boundaries.
//!
//! Every index and column is a Unicode code point. Python `\s`, `strip()` and `split()` use
//! `str.isspace` (Rust whitespace plus U+001C..U+001F), and Python `\b`/`\w`/`isalnum` use the
//! `[\p{L}\p{N}_]` word class, so every pattern that depends on them is hand-coded; the Rust
//! `regex` crate is used only for ASCII-only patterns whose match semantics are identical.
use crate::py_splitlines;
use regex::Regex;
use std::collections::{BTreeSet, HashMap};
use std::sync::LazyLock;

const MAX_CPP_LAMBDA_BODIES: usize = 100_000;
const MAX_CPP_SCOPE_DELIMITER_PAIRS: usize = 200_000;
const MAX_TOKENS_PER_FUNCTION: usize = 1_000_000;
const MAX_CONTROL_NESTING: usize = 128;

const NON_FUNCTION_HEADS: [&str; 16] = [
    "alignas",
    "catch",
    "decltype",
    "do",
    "else",
    "for",
    "if",
    "noexcept",
    "operator",
    "requires",
    "return",
    "sizeof",
    "static_assert",
    "switch",
    "typeid",
    "while",
];
const CONDITIONAL_DIRECTIVES: [&str; 6] = ["if", "ifdef", "ifndef", "elif", "else", "endif"];

static NAME_AT_END: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"~?[A-Za-z_][A-Za-z0-9_]*(?:::~?[A-Za-z_][A-Za-z0-9_]*)*\z")
        .expect("frozen _CPP_NAME_AT_END_RE")
});
static FUNCTION_LIKE_MACRO: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
        .expect("frozen _CPP_FUNCTION_LIKE_MACRO_RE")
});
static STANDALONE_MACRO: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\A[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(.*\)[ \t]*\z")
        .expect("frozen standalone macro invocation")
});
static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||::|->|\[\[|\]\]|[{}\[\](),;?:!]")
        .expect("frozen _TOKEN_RE")
});

// --- Python string semantics over code points ----------------------------------------------

/// Python `str.isspace` (also what `re`'s `\s` matches for `str` patterns).
fn py_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

/// Python `str.isalnum`: general categories L and N.
fn py_alnum(character: char) -> bool {
    if character.is_ascii() {
        return character.is_ascii_alphanumeric();
    }
    let mut buffer = [0u8; 4];
    ici_analysis::unicode::word(character.encode_utf8(&mut buffer))
}

/// Python `re` `\w` for `str` patterns.
fn py_word(character: char) -> bool {
    character == '_' || py_alnum(character)
}

fn ascii_word(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

fn ascii_word_start(character: char) -> bool {
    character.is_ascii_alphabetic() || character == '_'
}

/// Python `re` `\b` at code point `index` of `text`.
fn boundary(text: &[char], index: usize) -> bool {
    let before = index > 0 && index <= text.len() && py_word(text[index - 1]);
    let after = index < text.len() && py_word(text[index]);
    before != after
}

fn starts(text: &[char], index: usize, pattern: &str) -> bool {
    (index..)
        .zip(pattern.chars())
        .all(|(position, expected)| text.get(position) == Some(&expected))
}

/// `\b<word>\b` at `index`.
fn word_at(text: &[char], index: usize, word: &str) -> bool {
    boundary(text, index) && starts(text, index, word) && boundary(text, index + word.len())
}

fn skip_space(text: &[char], index: usize) -> usize {
    let mut position = index;
    while position < text.len() && py_space(text[position]) {
        position += 1;
    }
    position
}

fn lstrip(text: &[char]) -> &[char] {
    let start = text
        .iter()
        .position(|character| !py_space(*character))
        .unwrap_or(text.len());
    &text[start..]
}

fn rstrip(text: &[char]) -> &[char] {
    let end = text
        .iter()
        .rposition(|character| !py_space(*character))
        .map_or(0, |index| index + 1);
    &text[..end]
}

fn strip(text: &[char]) -> &[char] {
    lstrip(rstrip(text))
}

/// Python `str.split()` without arguments.
fn split_space(text: &[char]) -> Vec<&[char]> {
    text.split(|character| py_space(*character))
        .filter(|part| !part.is_empty())
        .collect()
}

fn find(text: &[char], pattern: &[char], from: usize) -> Option<usize> {
    let mut index = from;
    while index + pattern.len() <= text.len() {
        if text[index..index + pattern.len()] == *pattern {
            return Some(index);
        }
        index += 1;
    }
    None
}

fn string(text: &[char]) -> String {
    text.iter().collect()
}

/// Python `str.splitlines(keepends=True)` as half-open code-point spans.
fn line_spans_keepends(text: &[char]) -> Vec<(usize, usize)> {
    let mut spans = vec![];
    let (mut start, mut index) = (0, 0);
    while index < text.len() {
        if matches!(
            text[index],
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        ) {
            index += if text[index] == '\r' && text.get(index + 1) == Some(&'\n') {
                2
            } else {
                1
            };
            spans.push((start, index));
            start = index;
        } else {
            index += 1;
        }
    }
    if start < text.len() {
        spans.push((start, text.len()));
    }
    spans
}

/// Python `text[start:end]` for non-negative bounds.
fn py_slice(text: &[char], start: usize, end: usize) -> Vec<char> {
    let start = start.min(text.len());
    let end = end.min(text.len());
    if end <= start {
        vec![]
    } else {
        text[start..end].to_vec()
    }
}

fn replace_digraph_braces(text: &str) -> String {
    text.replace("<%", "{ ").replace("%>", "} ")
}

// --- cpp_text.py -----------------------------------------------------------------------------

/// Frozen `blank_cpp_region`: only `\r` and `\n` survive.
fn blank_cpp_region(chars: &mut [char], start: usize, end: usize) {
    for character in &mut chars[start..end] {
        if *character != '\r' && *character != '\n' {
            *character = ' ';
        }
    }
}

/// Frozen `cpp_line_comment_end`.
fn cpp_line_comment_end(text: &[char], start: usize) -> usize {
    let mut index = start + 2;
    while index < text.len() {
        if text[index] == '\n' && (index == start || text[index - 1] != '\\') {
            return index;
        }
        index += 1;
    }
    text.len()
}

/// Frozen `cpp_quoted_end`.
fn cpp_quoted_end(text: &[char], start: usize) -> usize {
    let quote = text[start];
    let mut index = start + 1;
    while index < text.len() {
        if text[index] == '\\' {
            index += 2;
            continue;
        }
        if text[index] == quote {
            return index + 1;
        }
        if matches!(text[index], '\r' | '\n') {
            return index + 1;
        }
        index += 1;
    }
    text.len()
}

/// Frozen `CPP_RAW_START_RE.match(text, index)`: `(?:u8|u|U|L)?R"(?P<delimiter>[^\s()\\]{0,16})\(`.
/// Returns the match end and the delimiter.
fn cpp_raw_start(text: &[char], index: usize) -> Option<(usize, Vec<char>)> {
    let rest = |start: usize| -> Option<(usize, Vec<char>)> {
        if !starts(text, start, "R\"") {
            return None;
        }
        let mut position = start + 2;
        let mut count = 0;
        while count < 16
            && position < text.len()
            && !(py_space(text[position]) || matches!(text[position], '(' | ')' | '\\'))
        {
            position += 1;
            count += 1;
        }
        (text.get(position) == Some(&'('))
            .then(|| (position + 1, text[start + 2..position].to_vec()))
    };
    for prefix in ["u8", "u", "U", "L"] {
        if starts(text, index, prefix)
            && let Some(found) = rest(index + prefix.len())
        {
            return Some(found);
        }
    }
    rest(index)
}

/// Frozen `_literal_operator_quotes`: `""` right after `\boperator\s*$` in a 64-char window.
fn literal_operator_quotes(text: &[char], start: usize) -> bool {
    if !starts(text, start, "\"\"") {
        return false;
    }
    let window = &text[start.saturating_sub(64)..start];
    let trimmed = rstrip(window);
    trimmed.len() >= 8
        && starts(trimmed, trimmed.len() - 8, "operator")
        && boundary(trimmed, trimmed.len() - 8)
}

/// Frozen `mask_cpp_literals`: blank comments and quoted C++ literals, keeping line geometry.
fn mask_cpp_literals(text: &[char]) -> Vec<char> {
    let mut chars = text.to_vec();
    let mut index = 0;
    while index < text.len() {
        if let Some((raw_end, delimiter)) = cpp_raw_start(text, index) {
            let mut closing = vec![')'];
            closing.extend_from_slice(&delimiter);
            closing.push('"');
            let end = find(text, &closing, raw_end).map_or(text.len(), |at| at + closing.len());
            blank_cpp_region(&mut chars, index, end);
            index = end;
            continue;
        }
        if starts(text, index, "//") {
            let end = cpp_line_comment_end(text, index);
            blank_cpp_region(&mut chars, index, end);
            index = end;
            continue;
        }
        if starts(text, index, "/*") {
            let end = find(text, &['*', '/'], index + 2).map_or(text.len(), |at| at + 2);
            blank_cpp_region(&mut chars, index, end);
            index = end;
            continue;
        }
        if text[index] == '"' && literal_operator_quotes(text, index) {
            index += 2;
            continue;
        }
        if matches!(text[index], '"' | '\'') {
            let end = cpp_quoted_end(text, index);
            blank_cpp_region(&mut chars, index, end);
            index = end;
            continue;
        }
        index += 1;
    }
    chars
}

/// Frozen `cpp_is_operator_name`: whether `name` spells the `operator` keyword, not a prefix.
fn cpp_is_operator_name(name: &str) -> bool {
    let candidate = match name.rfind("::operator") {
        Some(qualified) => &name[qualified + 2..],
        None => name,
    };
    if !candidate.starts_with("operator") || candidate.len() == "operator".len() {
        return false;
    }
    let following = candidate["operator".len()..]
        .chars()
        .next()
        .expect("longer than operator");
    !(py_alnum(following) || following == '_')
}

/// Frozen `_cpp_operator_name_at_end`.
fn cpp_operator_name_at_end(prefix: &[char]) -> Option<String> {
    let mut last_end = None;
    let mut index = 0;
    while index + 8 <= prefix.len() {
        if word_at(prefix, index, "operator") {
            last_end = Some(index + 8);
            index += 8;
        } else {
            index += 1;
        }
    }
    let spelling = strip(&prefix[last_end?..]);
    if spelling.is_empty() {
        return None;
    }
    let text = string(spelling);
    if spelling
        .iter()
        .any(|character| "(){};".contains(*character))
        && text != "()"
    {
        return None;
    }
    if text == "()" {
        return Some("operator()".into());
    }
    let last = spelling.len() - 1;
    if spelling.len() >= 2
        && spelling[0] == '['
        && spelling[last] == ']'
        && spelling[1..last]
            .iter()
            .all(|character| py_space(*character))
    {
        return Some("operator[]".into());
    }
    if starts(spelling, 0, "\"\"") {
        let suffix = &spelling[skip_space(spelling, 2)..];
        if suffix.first().is_some_and(|first| ascii_word_start(*first))
            && suffix.iter().all(|character| ascii_word(*character))
        {
            return Some(format!("operator\"\"{}", string(suffix)));
        }
    }
    for kind in ["new", "delete"] {
        if !starts(spelling, 0, kind) {
            continue;
        }
        let opening = skip_space(spelling, kind.len());
        if spelling.get(opening) == Some(&'[') {
            let closing = skip_space(spelling, opening + 1);
            if closing + 1 == spelling.len() && spelling[closing] == ']' {
                return Some(format!("operator {kind}[]"));
            }
        }
    }
    let parts = split_space(spelling);
    let joined: Vec<char> = parts.concat();
    if !joined.is_empty()
        && joined
            .iter()
            .all(|character| !(ascii_word(*character) || py_space(*character)))
    {
        return Some(format!("operator{}", string(&joined)));
    }
    let words: Vec<String> = parts.iter().map(|part| string(part)).collect();
    Some(format!("operator {}", words.join(" ")))
}

/// Frozen `_top_level_parameter_openings`.
fn top_level_parameter_openings(signature: &[char]) -> Vec<usize> {
    let (mut parentheses, mut brackets, mut braces) = (0usize, 0usize, 0usize);
    let mut openings = vec![];
    for (index, character) in signature.iter().enumerate() {
        match character {
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            '{' => braces += 1,
            '}' => braces = braces.saturating_sub(1),
            '(' => {
                if parentheses == 0 && brackets == 0 && braces == 0 {
                    openings.push(index);
                }
                parentheses += 1;
            }
            ')' => parentheses = parentheses.saturating_sub(1),
            _ => {}
        }
    }
    openings
}

/// Frozen `cpp_definition_name`: a source-spelled definition name from a header fragment.
fn cpp_definition_name(signature: &[char]) -> Option<String> {
    for opening in top_level_parameter_openings(signature) {
        let prefix = rstrip(&signature[..opening]);
        if let Some(operator) = cpp_operator_name_at_end(prefix) {
            return Some(operator);
        }
        let prefix = string(prefix);
        let Some(found) = NAME_AT_END.find(&prefix) else {
            continue;
        };
        let name = found.as_str();
        let unqualified = name.rsplit("::").next().unwrap_or(name);
        let unqualified = unqualified.strip_prefix('~').unwrap_or(unqualified);
        if NON_FUNCTION_HEADS.contains(&unqualified) {
            continue;
        }
        return Some(name.to_string());
    }
    None
}

/// Frozen `cpp_has_conditional_directive`:
/// `^[ \t]*\#[ \t]*(?:if|ifdef|ifndef|elif|else|endif)\b` with `re.MULTILINE`.
fn cpp_has_conditional_directive(text: &[char]) -> bool {
    let mut line_start = 0;
    loop {
        let mut index = line_start;
        while index < text.len() && matches!(text[index], ' ' | '\t') {
            index += 1;
        }
        if text.get(index) == Some(&'#') {
            let start = {
                let mut position = index + 1;
                while position < text.len() && matches!(text[position], ' ' | '\t') {
                    position += 1;
                }
                position
            };
            let mut end = start;
            while end < text.len() && py_word(text[end]) {
                end += 1;
            }
            if CONDITIONAL_DIRECTIVES.contains(&string(&text[start..end]).as_str()) {
                return true;
            }
        }
        match text[line_start..]
            .iter()
            .position(|character| *character == '\n')
        {
            Some(offset) => line_start += offset + 1,
            None => return false,
        }
    }
}

/// Frozen `cpp_function_like_macro_names` over masked text.
fn cpp_function_like_macro_names(masked: &str) -> BTreeSet<String> {
    FUNCTION_LIKE_MACRO
        .captures_iter(masked)
        .map(|captures| captures[1].to_string())
        .collect()
}

/// Frozen `mask_cpp_preprocessor_directives`: blank directives and their continuations.
fn mask_cpp_preprocessor_directives(masked: &[char]) -> Vec<char> {
    let mut chars = masked.to_vec();
    let mut continued = false;
    for (start, end) in line_spans_keepends(masked) {
        let mut body_end = end;
        while body_end > start && matches!(masked[body_end - 1], '\r' | '\n') {
            body_end -= 1;
        }
        let body = &masked[start..body_end];
        let directive = continued || lstrip(body).first() == Some(&'#');
        if directive {
            blank_cpp_region(&mut chars, start, end);
            continued = rstrip(body).last() == Some(&'\\');
        } else {
            continued = false;
        }
    }
    chars
}

/// Frozen `_matching_cpp_delimiter` (scans to the end of `text`, not to a caller's stop).
fn matching_cpp_delimiter(
    text: &[char],
    start: usize,
    opening: char,
    closing: char,
) -> Option<usize> {
    let mut depth = 0i64;
    for (index, character) in text.iter().enumerate().skip(start) {
        if *character == opening {
            depth += 1;
        } else if *character == closing {
            depth -= 1;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

/// Frozen `_previous_cpp_token`: the previous non-space character and its word, if any.
fn previous_cpp_token(text: &[char], start: usize) -> (Option<char>, &[char]) {
    let mut index = start;
    while index > 0 && py_space(text[index - 1]) {
        index -= 1;
    }
    if index == 0 {
        return (None, &[]);
    }
    let previous = text[index - 1];
    if !py_word(previous) {
        return (Some(previous), &[]);
    }
    let end = index;
    while index > 0 && py_word(text[index - 1]) {
        index -= 1;
    }
    (Some(previous), &text[index..end])
}

/// Frozen `_can_start_lambda_capture`.
fn can_start_lambda_capture(text: &[char], start: usize) -> bool {
    if starts(text, start, "[[") || (start > 0 && text[start - 1] == '[') {
        return false;
    }
    let (previous, token) = previous_cpp_token(text, start);
    if !token.is_empty() {
        return matches!(string(token).as_str(), "return" | "co_return" | "throw");
    }
    match previous {
        None => true,
        Some(character) => matches!(
            character,
            '=' | '(' | '{' | ',' | ';' | ':' | '?' | '!' | '+' | '-' | '*' | '&' | '|'
        ),
    }
}

/// Frozen `_lambda_body_open` (only the body position is consumed by callers).
fn lambda_body_open(text: &[char], capture_end: usize, stop: usize) -> Option<usize> {
    let (mut parentheses, mut brackets, mut angles) = (0usize, 0usize, 0usize);
    let mut index = capture_end + 1;
    while index < stop {
        let flat = parentheses == 0 && brackets == 0;
        match text[index] {
            '(' => parentheses += 1,
            ')' => parentheses = parentheses.saturating_sub(1),
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            '<' if flat => angles += 1,
            '>' if flat => angles = angles.saturating_sub(1),
            ';' if flat && angles == 0 => return None,
            '{' if flat && angles == 0 => {
                let prefix = &text[capture_end + 1..index];
                if cpp_requires_expression_before_brace(prefix) {
                    index = matching_cpp_delimiter(text, index, '{', '}')?;
                } else {
                    return Some(index);
                }
            }
            _ => {}
        }
        index += 1;
    }
    None
}

/// Frozen `_cpp_delimiter_closings` with both bounded-limit `ValueError`s.
fn cpp_delimiter_closings(
    text: &[char],
    opening: char,
    closing: char,
) -> Result<HashMap<usize, usize>, String> {
    let mut stack = vec![];
    let mut pairs = HashMap::new();
    for (index, character) in text.iter().enumerate() {
        if *character == opening {
            stack.push(index);
            if stack.len() > MAX_CPP_SCOPE_DELIMITER_PAIRS {
                return Err("C++ scope delimiter depth exceeds the bounded limit".into());
            }
        } else if *character == closing
            && let Some(open) = stack.pop()
        {
            pairs.insert(open, index);
            if pairs.len() > MAX_CPP_SCOPE_DELIMITER_PAIRS {
                return Err("C++ scope delimiter count exceeds the bounded limit".into());
            }
        }
    }
    Ok(pairs)
}

/// Masked text plus the half-open lambda body ranges, in discovery order.
type MaskedLambdas = (Vec<char>, Vec<(usize, usize)>);

/// Frozen `mask_cpp_lambda_bodies`: blank nested lambda bodies; ranges are half-open input
/// offsets in discovery order.
fn mask_cpp_lambda_bodies(masked: &[char]) -> Result<MaskedLambdas, String> {
    let mut chars = masked.to_vec();
    let square_closings = cpp_delimiter_closings(masked, '[', ']')?;
    let brace_closings = cpp_delimiter_closings(masked, '{', '}')?;
    let mut ranges: Vec<(usize, usize)> = vec![];
    let mut intervals = vec![(0, masked.len())];
    while let Some((interval_start, interval_end)) = intervals.pop() {
        let mut index = interval_start;
        while index < interval_end {
            if masked[index] != '[' || !can_start_lambda_capture(masked, index) {
                index += 1;
                continue;
            }
            let Some(&capture_end) = square_closings.get(&index) else {
                index += 1;
                continue;
            };
            if capture_end >= interval_end {
                index += 1;
                continue;
            }
            let body_open = lambda_body_open(masked, capture_end, interval_end);
            let body_close = body_open.and_then(|open| brace_closings.get(&open).copied());
            let (Some(body_open), Some(body_close)) = (body_open, body_close) else {
                index += 1;
                continue;
            };
            if body_close >= interval_end {
                index += 1;
                continue;
            }
            ranges.push((body_open, body_close + 1));
            if ranges.len() > MAX_CPP_LAMBDA_BODIES {
                return Err("C++ lambda body count exceeds the bounded limit".into());
            }
            if index + 1 < capture_end {
                intervals.push((index + 1, capture_end));
            }
            if capture_end + 1 < body_open {
                intervals.push((capture_end + 1, body_open));
            }
            if body_open + 1 < body_close {
                intervals.push((body_open + 1, body_close));
            }
            index = body_close + 1;
        }
    }
    let mut sorted = ranges.clone();
    sorted.sort_unstable();
    let mut merged: Vec<(usize, usize)> = vec![];
    for (start, end) in sorted {
        match merged.last_mut() {
            Some(last) if start <= last.1 => last.1 = last.1.max(end),
            _ => merged.push((start, end)),
        }
    }
    for (start, end) in merged {
        blank_cpp_region(&mut chars, start, end);
    }
    Ok((chars, ranges))
}

/// Frozen `cpp_requires_expression_before_brace`.
fn cpp_requires_expression_before_brace(prefix: &[char]) -> bool {
    let mut count = 0;
    let mut last_end = 0;
    let mut index = 0;
    while index + 8 <= prefix.len() {
        if word_at(prefix, index, "requires") {
            count += 1;
            last_end = index + 8;
            index += 8;
        } else {
            index += 1;
        }
    }
    if count < 2 {
        return false;
    }
    let suffix = strip(&prefix[last_end..]);
    if suffix.is_empty() {
        return true;
    }
    if suffix[0] != '(' {
        return false;
    }
    let mut depth = 0i64;
    for (index, character) in suffix.iter().enumerate() {
        if *character == '(' {
            depth += 1;
        } else if *character == ')' {
            depth -= 1;
            if depth < 0 {
                return false;
            }
            if depth == 0 {
                return strip(&suffix[index + 1..]).is_empty();
            }
        }
    }
    false
}

// --- cpp_complexity.py ---------------------------------------------------------------------

/// Frozen `_CPP_DECISION_HEAD_RE.findall` count:
/// `\b(?:for|while|catch)\s*\(|\bif\s+(?:constexpr\s*\(|!?consteval\b)|\bif\s*\(`.
fn decision_head_end(text: &[char], index: usize) -> Option<usize> {
    if !boundary(text, index) {
        return None;
    }
    for keyword in ["for", "while", "catch"] {
        if starts(text, index, keyword) {
            let opening = skip_space(text, index + keyword.len());
            if text.get(opening) == Some(&'(') {
                return Some(opening + 1);
            }
        }
    }
    if !starts(text, index, "if") {
        return None;
    }
    let after = skip_space(text, index + 2);
    if after > index + 2 {
        if starts(text, after, "constexpr") {
            let opening = skip_space(text, after + 9);
            if text.get(opening) == Some(&'(') {
                return Some(opening + 1);
            }
        }
        if text.get(after) == Some(&'!')
            && starts(text, after + 1, "consteval")
            && boundary(text, after + 10)
        {
            return Some(after + 10);
        }
        if starts(text, after, "consteval") && boundary(text, after + 9) {
            return Some(after + 9);
        }
    }
    (text.get(after) == Some(&'(')).then_some(after + 1)
}

/// Frozen `_CPP_CASE_RE.findall` match end at `index`: `\bcase\b[^:\n]*:`.
fn case_end(text: &[char], index: usize) -> Option<usize> {
    if !word_at(text, index, "case") {
        return None;
    }
    let mut position = index + 4;
    while position < text.len() && !matches!(text[position], ':' | '\n') {
        position += 1;
    }
    (text.get(position) == Some(&':')).then_some(position + 1)
}

fn findall_count(text: &[char], matcher: fn(&[char], usize) -> Option<usize>) -> i64 {
    let mut count = 0;
    let mut index = 0;
    while index < text.len() {
        match matcher(text, index) {
            Some(end) => {
                count += 1;
                index = end;
            }
            None => index += 1,
        }
    }
    count
}

/// Python `str.count` (non-overlapping).
fn count_substring(text: &[char], pattern: &str) -> i64 {
    let pattern: Vec<char> = pattern.chars().collect();
    let mut count = 0;
    let mut index = 0;
    while index + pattern.len() <= text.len() {
        if text[index..index + pattern.len()] == *pattern {
            count += 1;
            index += pattern.len();
        } else {
            index += 1;
        }
    }
    count
}

/// Frozen `_cpp_decision_count`: decision points on one line.
fn cpp_decision_count(text: &[char]) -> i64 {
    let tokens =
        count_substring(text, "&&") + count_substring(text, "||") + count_substring(text, "?");
    tokens + findall_count(text, decision_head_end) + findall_count(text, case_end)
}

/// Frozen `_CPP_LAMBDA_INITIALIZER_RE.search`: `(?<![=!<>])=\s*(?:\+\s*)?\[`.
fn lambda_initializer(text: &[char]) -> bool {
    (0..text.len()).any(|index| {
        if text[index] != '=' || (index > 0 && matches!(text[index - 1], '=' | '!' | '<' | '>')) {
            return false;
        }
        let next = skip_space(text, index + 1);
        match text.get(next) {
            Some('[') => true,
            Some('+') => text.get(skip_space(text, next + 1)) == Some(&'['),
            _ => false,
        }
    })
}

/// `re.search(r"\btemplate\s*<", text)`.
fn template_head(text: &[char]) -> bool {
    (0..text.len()).any(|index| {
        boundary(text, index)
            && starts(text, index, "template")
            && text.get(skip_space(text, index + 8)) == Some(&'<')
    })
}

/// `re.search(r"\btry\b", text)`.
fn try_keyword(text: &[char]) -> bool {
    (0..text.len()).any(|index| word_at(text, index, "try"))
}

/// Frozen `_cpp_delimiter_depth`.
fn cpp_delimiter_depth(text: &[char]) -> (usize, usize) {
    let (mut parentheses, mut brackets) = (0usize, 0usize);
    for character in text {
        match character {
            '(' => parentheses += 1,
            ')' => parentheses = parentheses.saturating_sub(1),
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            _ => {}
        }
    }
    (parentheses, brackets)
}

/// Frozen `_cpp_constructor_initializer_candidate`.
fn cpp_constructor_initializer_candidate(text: &[char]) -> bool {
    let (mut parentheses, mut brackets) = (0usize, 0usize);
    let mut has_colon = false;
    for (index, character) in text.iter().enumerate() {
        match character {
            '(' => parentheses += 1,
            ')' => parentheses = parentheses.saturating_sub(1),
            '[' => brackets += 1,
            ']' => brackets = brackets.saturating_sub(1),
            ':' if parentheses == 0
                && brackets == 0
                && (index == 0 || text[index - 1] != ':')
                && (index + 1 == text.len() || text[index + 1] != ':') =>
            {
                has_colon = true;
            }
            _ => {}
        }
    }
    if !has_colon {
        return false;
    }
    // `(?:[A-Za-z_][A-Za-z0-9_]*|[>\]])$` on the right-stripped text.
    let tail = rstrip(text);
    if matches!(tail.last(), Some('>' | ']')) {
        return true;
    }
    tail.iter()
        .rev()
        .take_while(|character| ascii_word(**character))
        .any(|character| ascii_word_start(*character))
}

/// One frozen `_CppFunctionSpan`: a brace-heuristic C++ function definition and its measures.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HeuristicSpan {
    /// Source-spelled definition name (`cpp_definition_name`).
    pub name: String,
    /// 1-based line of the first signature character.
    pub start_line: usize,
    /// 1-based code-point column of the first signature character.
    pub start_column: usize,
    /// 1-based line of the body's opening brace.
    pub body_start_line: usize,
    /// 1-based code-point column of the body's opening brace.
    pub body_start_column: usize,
    /// Closing line, or the input line count for an unterminated body.
    pub end_line: usize,
    /// Closing brace column; `None` when the body never closed.
    pub end_column: Option<usize>,
    /// Cyclomatic estimate (recomputed from the metric lines for terminated spans).
    pub complexity: i64,
    /// Block nesting inside the body (the function's own brace is depth 0).
    pub max_nesting: i64,
    /// Lambda bodies excluded from the measured body.
    pub excluded_lambdas: usize,
    /// `"operator"` or `"function"`.
    pub function_kind: &'static str,
    /// Whether the signature spells `template <`.
    pub is_template: bool,
    /// Whether the measured body contains a conditional preprocessing directive.
    pub preprocessor_conditional: bool,
}

/// Frozen `_CppScanner`: a brace-depth state machine over one file.
struct CppScanner {
    spans: Vec<HeuristicSpan>,
    depth: i64,
    current: Option<HeuristicSpan>,
    base_depth: i64,
    pending: Vec<char>,
    pending_line: usize,
    pending_column: usize,
    expression_depth: i64,
    function_try: bool,
    awaiting_catch: bool,
    catch_header: bool,
    macro_names: BTreeSet<String>,
}

impl CppScanner {
    fn new(macro_names: BTreeSet<String>) -> Self {
        Self {
            spans: vec![],
            depth: 0,
            current: None,
            base_depth: 0,
            pending: vec![],
            pending_line: 0,
            pending_column: 0,
            expression_depth: 0,
            function_try: false,
            awaiting_catch: false,
            catch_header: false,
            macro_names,
        }
    }

    fn feed(&mut self, number: usize, text: &[char]) {
        let mut start = 0;
        let mut column = 1;
        while start < text.len() {
            let remaining = &text[start..];
            let consumed = if self.current.is_some() {
                self.feed_body(number, remaining, column)
            } else if self.expression_depth != 0 {
                self.feed_expression(remaining)
            } else {
                self.feed_outside(number, remaining, column)
            };
            if consumed == 0 {
                if self.current.is_none() {
                    continue;
                }
                return;
            }
            start += consumed;
            column += consumed;
        }
    }

    fn finish(&mut self, last_line: usize) {
        if let Some(mut current) = self.current.take() {
            current.end_line = last_line;
            self.spans.push(current);
        }
    }

    fn feed_body(&mut self, number: usize, text: &[char], column: usize) -> usize {
        if self.current.is_none() {
            return text.len();
        }
        if self.awaiting_catch {
            return self.feed_catch(text);
        }
        let current = self.current.as_mut().expect("current span");
        let mut close_at = None;
        for (index, character) in text.iter().enumerate() {
            if *character == '{' {
                self.depth += 1;
                current.max_nesting = current.max_nesting.max(self.depth - self.base_depth - 1);
            } else if *character == '}' {
                self.depth -= 1;
                if self.depth <= self.base_depth {
                    close_at = Some(index);
                    break;
                }
            }
        }
        let body = match close_at {
            None => text,
            Some(close) => &text[..=close],
        };
        current.complexity += cpp_decision_count(body);
        let Some(close) = close_at else {
            return text.len();
        };
        current.end_line = number;
        current.end_column = Some(column + close);
        self.depth = self.depth.max(self.base_depth);
        if self.function_try {
            self.awaiting_catch = true;
            return close + 1;
        }
        let finished = self.current.take().expect("current span");
        self.spans.push(finished);
        close + 1
    }

    fn feed_catch(&mut self, text: &[char]) -> usize {
        if self.current.is_none() {
            return 0;
        }
        let stripped = lstrip(text);
        let leading = text.len() - stripped.len();
        if stripped.is_empty() {
            return text.len();
        }
        if !self.catch_header {
            if !(starts(stripped, 0, "catch") && boundary(stripped, 5)) {
                let finished = self.current.take().expect("current span");
                self.spans.push(finished);
                self.function_try = false;
                self.awaiting_catch = false;
                return 0;
            }
            self.current.as_mut().expect("current span").complexity += 1;
            self.catch_header = true;
        }
        let Some(opening) = text.iter().position(|character| *character == '{') else {
            return text.len();
        };
        self.catch_header = false;
        self.awaiting_catch = false;
        self.depth = self.base_depth + 1;
        let current = self.current.as_mut().expect("current span");
        current.max_nesting = current.max_nesting.max(0);
        (opening + 1).max(leading + 1)
    }

    fn append_pending(&mut self, number: usize, column: usize, text: &[char]) {
        let stripped = strip(text);
        if stripped.is_empty() {
            return;
        }
        if self.pending.is_empty() {
            self.pending_line = number;
            self.pending_column = column + text.len() - lstrip(text).len();
            // f"{''} {stripped}".strip() == stripped
            self.pending = stripped.to_vec();
        } else {
            // The pending text and `stripped` carry no outer whitespace, so the frozen
            // `.strip()` of the joined text is the plain join.
            self.pending.push(' ');
            self.pending.extend_from_slice(stripped);
        }
    }

    fn clear_pending(&mut self) {
        self.pending.clear();
        self.pending_line = 0;
        self.pending_column = 0;
    }

    fn feed_expression(&mut self, text: &[char]) -> usize {
        for (index, character) in text.iter().enumerate() {
            if *character == '{' {
                self.expression_depth += 1;
            } else if *character == '}' {
                self.expression_depth -= 1;
                if self.expression_depth == 0 {
                    if !self.pending.is_empty() {
                        self.pending.push(' ');
                    }
                    self.pending.extend(['{', '}']);
                    return index + 1;
                }
            }
        }
        text.len()
    }

    fn feed_outside(&mut self, number: usize, text: &[char], column: usize) -> usize {
        if lstrip(text).first() == Some(&'#') {
            self.clear_pending();
            return text.len();
        }
        if self.pending.is_empty() && self.standalone_macro_invocation(text) {
            self.clear_pending();
            return text.len();
        }
        let Some(index) = text
            .iter()
            .position(|character| matches!(character, '{' | '}' | ';'))
        else {
            self.append_pending(number, column, text);
            return text.len();
        };
        let delimiter = text[index];
        self.append_pending(number, column, &text[..index]);
        if delimiter == ';' {
            self.clear_pending();
            return index + 1;
        }
        if delimiter == '}' {
            self.clear_pending();
            self.depth = (self.depth - 1).max(0);
            return index + 1;
        }
        let signature = self.pending.clone();
        let (parentheses, brackets) = cpp_delimiter_depth(&signature);
        if parentheses != 0
            || brackets != 0
            || lambda_initializer(&signature)
            || cpp_requires_expression_before_brace(&signature)
            || cpp_constructor_initializer_candidate(&signature)
        {
            self.expression_depth = 1;
            return index + 1;
        }
        let Some(name) = cpp_definition_name(&signature) else {
            self.clear_pending();
            self.depth += 1;
            return index + 1;
        };
        let function_kind = if cpp_is_operator_name(&name) {
            "operator"
        } else {
            "function"
        };
        let mut span = HeuristicSpan {
            name,
            start_line: self.pending_line,
            start_column: self.pending_column,
            body_start_line: number,
            body_start_column: column + index,
            end_line: self.pending_line,
            end_column: None,
            complexity: 1,
            excluded_lambdas: 0,
            function_kind,
            is_template: template_head(&signature),
            preprocessor_conditional: false,
            max_nesting: 0,
        };
        self.clear_pending();
        self.base_depth = self.depth;
        self.depth += 1;
        self.function_try = try_keyword(&signature);
        span.complexity += cpp_decision_count(&signature);
        self.current = Some(span);
        index + 1
    }

    fn standalone_macro_invocation(&self, text: &[char]) -> bool {
        let text = string(text);
        let Some(captures) = STANDALONE_MACRO.captures(&text) else {
            return false;
        };
        let name = &captures[1];
        self.macro_names.contains(name)
            || (self.depth == 0
                && !name.chars().any(|character| character.is_ascii_lowercase())
                && name
                    .chars()
                    .any(|character| character.is_ascii_alphabetic()))
    }
}

/// Frozen `_cpp_metric_details_from_lines`: (complexity, max nesting, lambdas, conditional).
fn cpp_metric_details_from_lines(
    lines: &[String],
    body_start_line: usize,
    body_start_column: usize,
    end_line: usize,
    end_column: usize,
) -> Result<(i64, i64, usize, bool), String> {
    let Some(selected) = selected_body(
        lines,
        body_start_line,
        body_start_column,
        end_line,
        end_column,
    ) else {
        return Ok((1, 0, 0, false));
    };
    let body: Vec<char> = selected.join(&'\n');
    let (metric_body, lambda_ranges) = mask_cpp_lambda_bodies(&body)?;
    let metric_text = string(&metric_body);
    let complexity = 1 + py_splitlines(&metric_text)
        .iter()
        .map(|line| cpp_decision_count(&line.chars().collect::<Vec<_>>()))
        .sum::<i64>();
    let mut depth = 0i64;
    let mut max_nesting = 0i64;
    for character in &metric_body {
        if *character == '{' {
            depth += 1;
            max_nesting = max_nesting.max((depth - 1).max(0));
        } else if *character == '}' {
            depth = (depth - 1).max(0);
        }
    }
    Ok((
        complexity,
        max_nesting,
        lambda_ranges.len(),
        cpp_has_conditional_directive(&metric_body),
    ))
}

/// The shared column-precise selection of `_slice_body` and `_cpp_metric_details_from_lines`;
/// `None` when the line selection is empty. Slices apply in sequence, exactly like Python's
/// `selected[0] = ...; selected[-1] = ...` on a one-element selection.
fn selected_body(
    lines: &[String],
    body_start_line: usize,
    body_start_column: usize,
    end_line: usize,
    end_column: usize,
) -> Option<Vec<Vec<char>>> {
    let first = body_start_line.saturating_sub(1).min(lines.len());
    let last = end_line.min(lines.len());
    if last <= first {
        return None;
    }
    let mut selected: Vec<Vec<char>> = lines[first..last]
        .iter()
        .map(|line| line.chars().collect())
        .collect();
    let start = body_start_column.saturating_sub(1);
    if body_start_line == end_line {
        selected[0] = py_slice(&selected[0], start, end_column);
    } else {
        selected[0] = py_slice(&selected[0], start, usize::MAX);
        let tail = selected.len() - 1;
        selected[tail] = py_slice(&selected[tail], 0, end_column);
    }
    Some(selected)
}

/// Frozen `cpp_complexity._cpp_function_inventory`: scan a translation unit's lines and
/// measure every brace-heuristic function definition.
///
/// Returns the spans and the literal-masked metric lines. `Err` carries the frozen
/// `ValueError` message of a bounded lambda/delimiter limit.
pub fn function_inventory(lines: &[String]) -> Result<(Vec<HeuristicSpan>, Vec<String>), String> {
    let joined: Vec<char> = lines.join("\n").chars().collect();
    let masked_source = replace_digraph_braces(&string(&mask_cpp_literals(&joined)));
    let mut scanner = CppScanner::new(cpp_function_like_macro_names(&masked_source));
    let metric_lines = py_splitlines(&masked_source);
    let masked_chars: Vec<char> = masked_source.chars().collect();
    let scanner_lines = py_splitlines(&string(&mask_cpp_preprocessor_directives(&masked_chars)));
    for (index, text) in scanner_lines.iter().enumerate() {
        scanner.feed(index + 1, &text.chars().collect::<Vec<_>>());
    }
    scanner.finish(lines.len());
    for span in &mut scanner.spans {
        let Some(end_column) = span.end_column else {
            continue;
        };
        let (complexity, max_nesting, lambdas, conditional) = cpp_metric_details_from_lines(
            &metric_lines,
            span.body_start_line,
            span.body_start_column,
            span.end_line,
            end_column,
        )?;
        span.complexity = complexity;
        span.max_nesting = max_nesting;
        span.excluded_lambdas = lambdas;
        span.preprocessor_conditional = conditional;
    }
    Ok((scanner.spans, metric_lines))
}

/// Frozen `languages/metrics.py::_slice_body`: the column-precise body slice (1-based lines and
/// code-point columns) that the cognitive parser reads. `None` where frozen raises `IndexError`
/// because the line selection is empty.
pub fn slice_body(
    lines: &[String],
    body_start_line: usize,
    body_start_column: usize,
    end_line: usize,
    end_column: usize,
) -> Option<String> {
    let selected = selected_body(
        lines,
        body_start_line,
        body_start_column,
        end_line,
        end_column,
    )?;
    Some(
        selected
            .iter()
            .map(|line| string(line))
            .collect::<Vec<_>>()
            .join("\n"),
    )
}

// --- _cpp_cognitive.py ---------------------------------------------------------------------

/// Frozen `CppCognitiveMetric`: one lexical metric for one function body.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CognitiveMetric {
    /// Sonar-inspired cognitive complexity (control flow plus expression increments).
    pub cognitive: i64,
    /// Deepest braced control nesting.
    pub max_nesting: i64,
    /// Controls whose body is a single unbraced statement.
    pub unbraced_controls: i64,
    /// Sequences of like logical operators.
    pub logical_sequences: i64,
    /// Lambda bodies excluded before measuring.
    pub excluded_lambdas: usize,
    /// Whether a conditional preprocessing directive remains in the measured text.
    pub preprocessor_conditional: bool,
}

const CONTROL_WORDS: [&str; 5] = ["if", "for", "while", "switch", "do"];

fn control_word(token: Option<&str>) -> bool {
    token.is_some_and(|token| CONTROL_WORDS.contains(&token))
}

/// Frozen `cpp_cognitive_metric`: a bounded lexical metric over one function body. `Err`
/// carries the frozen `ValueError` message.
pub fn cognitive_metric(body: &str) -> Result<CognitiveMetric, String> {
    let chars: Vec<char> = body.chars().collect();
    let masked = string(&mask_cpp_literals(&chars));
    let structural: Vec<char> = replace_digraph_braces(&masked).chars().collect();
    let (without_lambdas, lambda_ranges) = mask_cpp_lambda_bodies(&structural)?;
    let conditional = cpp_has_conditional_directive(&without_lambdas);
    let directive_free = string(&mask_cpp_preprocessor_directives(&without_lambdas));
    let tokens: Vec<&str> = TOKEN
        .find_iter(&directive_free)
        .map(|found| found.as_str())
        .collect();
    if tokens.len() > MAX_TOKENS_PER_FUNCTION {
        return Err("function token count exceeds the bounded limit".into());
    }
    validate_delimiters(&tokens)?;

    let mut expression_cognitive = 0i64;
    let mut logical_operator: Option<&str> = None;
    let mut logical_sequences = 0i64;
    for token in &tokens {
        let normalized = match *token {
            "&&" | "and" => Some("and"),
            "||" | "or" => Some("or"),
            _ => None,
        };
        if let Some(normalized) = normalized {
            if logical_operator != Some(normalized) {
                expression_cognitive += 1;
                logical_sequences += 1;
                logical_operator = Some(normalized);
            }
            continue;
        }
        if matches!(*token, "break" | "continue" | "goto") {
            expression_cognitive += 1;
            continue;
        }
        if *token == "?" {
            expression_cognitive += 1;
            logical_operator = None;
            continue;
        }
        if matches!(*token, "," | ";" | "{" | "}" | ":" | "catch" | "else")
            || control_word(Some(token))
        {
            logical_operator = None;
        }
    }

    let mut parser = CppControlParser::new(&tokens);
    parser.parse()?;
    Ok(CognitiveMetric {
        cognitive: parser.cognitive + expression_cognitive,
        max_nesting: parser.max_nesting,
        unbraced_controls: parser.unbraced_controls,
        logical_sequences,
        excluded_lambdas: lambda_ranges.len(),
        preprocessor_conditional: conditional,
    })
}

/// Frozen `_validate_delimiters`.
fn validate_delimiters(tokens: &[&str]) -> Result<(), String> {
    let mut stack: Vec<&str> = vec![];
    for token in tokens {
        let opening = match *token {
            ")" => "(",
            "]" => "[",
            "}" => "{",
            "]]" => "[[",
            "(" | "[" | "{" | "[[" => {
                stack.push(token);
                continue;
            }
            _ => continue,
        };
        if stack.pop() != Some(opening) {
            return Err("mismatched delimiter in function body".into());
        }
    }
    if !stack.is_empty() {
        return Err("unclosed delimiter in function body".into());
    }
    Ok(())
}

/// Frozen `_CppControlParser`: a small statement parser used only for nesting weights.
struct CppControlParser<'t> {
    tokens: &'t [&'t str],
    position: usize,
    cognitive: i64,
    max_nesting: i64,
    unbraced_controls: i64,
    recursion_depth: usize,
    loop_depth: usize,
    switch_depth: usize,
}

enum Terminal {
    Label,
    Terminated,
}

impl<'t> CppControlParser<'t> {
    fn new(tokens: &'t [&'t str]) -> Self {
        Self {
            tokens,
            position: 0,
            cognitive: 0,
            max_nesting: 0,
            unbraced_controls: 0,
            recursion_depth: 0,
            loop_depth: 0,
            switch_depth: 0,
        }
    }

    fn parse(&mut self) -> Result<(), String> {
        if self.peek() == Some("{") {
            self.compound(0)?;
            while self.peek() == Some("catch") {
                self.catch(0)?;
            }
        }
        while self.position < self.tokens.len() {
            self.statement(0)?;
        }
        Ok(())
    }

    fn peek(&self) -> Option<&'t str> {
        self.tokens.get(self.position).copied()
    }

    fn take(&mut self, expected: Option<&str>) -> Result<&'t str, String> {
        let Some(token) = self.peek() else {
            return Err("function token stream ended unexpectedly".into());
        };
        if let Some(expected) = expected
            && token != expected
        {
            // Tokens are ASCII identifiers or punctuation, so Python's repr is single-quoted.
            return Err(format!("expected '{expected}', found '{token}'"));
        }
        self.position += 1;
        Ok(token)
    }

    fn balanced(&mut self, opening: &str, closing: &str) -> Result<(), String> {
        self.take(Some(opening))?;
        let mut depth = 1;
        while depth != 0 {
            let token = self.take(None)?;
            if token == opening {
                depth += 1;
            } else if token == closing {
                depth -= 1;
            }
        }
        Ok(())
    }

    fn compound(&mut self, nesting: i64) -> Result<(), String> {
        self.take(Some("{"))?;
        while !matches!(self.peek(), None | Some("}")) {
            self.statement(nesting)?;
        }
        self.take(Some("}"))?;
        Ok(())
    }

    fn controlled_body(&mut self, nesting: i64) -> Result<(), String> {
        if self.peek() == Some("{") {
            self.max_nesting = self.max_nesting.max(nesting);
            return self.compound(nesting);
        }
        self.unbraced_controls += 1;
        self.statement(nesting)
    }

    fn header(&mut self) -> Result<(), String> {
        if self.peek() != Some("(") {
            return Err("control statement is missing its parenthesized header".into());
        }
        self.balanced("(", ")")
    }

    fn attributes(&mut self) -> Result<(), String> {
        while self.peek() == Some("[[") {
            self.balanced("[[", "]]")?;
        }
        Ok(())
    }

    fn if_statement(&mut self, nesting: i64) -> Result<(), String> {
        let mut score_if = true;
        let mut chain_length = 0;
        loop {
            chain_length += 1;
            if chain_length > MAX_CONTROL_NESTING {
                return Err("else-if chain exceeds the bounded limit".into());
            }
            self.take(Some("if"))?;
            if score_if {
                self.cognitive += 1 + nesting;
            }
            if self.peek() == Some("constexpr") {
                self.take(Some("constexpr"))?;
            }
            let mut is_consteval = false;
            if self.peek() == Some("!") {
                self.take(Some("!"))?;
                if self.peek() != Some("consteval") {
                    return Err("if ! must be followed by consteval".into());
                }
            }
            if self.peek() == Some("consteval") {
                self.take(Some("consteval"))?;
                is_consteval = true;
            } else {
                self.header()?;
            }
            self.attributes()?;
            if is_consteval && self.peek() != Some("{") {
                return Err("if consteval requires a compound statement".into());
            }
            self.controlled_body(nesting + 1)?;
            if self.peek() != Some("else") {
                return Ok(());
            }
            self.take(Some("else"))?;
            self.cognitive += 1 + nesting;
            self.attributes()?;
            if self.peek() != Some("if") {
                if is_consteval && self.peek() != Some("{") {
                    return Err("if consteval else requires a compound statement".into());
                }
                return self.controlled_body(nesting + 1);
            }
            score_if = false;
        }
    }

    fn do_statement(&mut self, nesting: i64) -> Result<(), String> {
        self.take(Some("do"))?;
        self.cognitive += 1 + nesting;
        self.attributes()?;
        self.loop_depth += 1;
        let body = self.controlled_body(nesting + 1);
        self.loop_depth -= 1;
        body?;
        self.take(Some("while"))?;
        self.header()?;
        self.take(Some(";"))?;
        Ok(())
    }

    fn catch(&mut self, nesting: i64) -> Result<(), String> {
        self.take(Some("catch"))?;
        self.cognitive += 1 + nesting;
        self.header()?;
        self.attributes()?;
        self.controlled_body(nesting + 1)
    }

    fn try_statement(&mut self, nesting: i64) -> Result<(), String> {
        self.take(Some("try"))?;
        if self.peek() != Some("{") {
            return Err("try must be followed by a compound statement".into());
        }
        self.compound(nesting)?;
        let mut catches = 0;
        while self.peek() == Some("catch") {
            catches += 1;
            self.catch(nesting)?;
        }
        if catches == 0 {
            return Err("try statement is missing a catch handler".into());
        }
        Ok(())
    }

    fn control(&mut self, nesting: i64) -> Result<(), String> {
        let token = self.peek();
        if token == Some("if") {
            return self.if_statement(nesting);
        }
        if token == Some("do") {
            return self.do_statement(nesting);
        }
        if !control_word(token) {
            return Err("internal control parser mismatch".into());
        }
        let control = self.take(None)?;
        self.cognitive += 1 + nesting;
        self.header()?;
        self.attributes()?;
        let looping = matches!(control, "for" | "while");
        if looping {
            self.loop_depth += 1;
        } else if control == "switch" {
            self.switch_depth += 1;
        }
        let body = self.controlled_body(nesting + 1);
        if looping {
            self.loop_depth -= 1;
        } else if control == "switch" {
            self.switch_depth -= 1;
        }
        body
    }

    fn validate_simple_start(&self, first: Option<&str>) -> Result<(), String> {
        match first {
            Some(label @ ("case" | "default")) if self.switch_depth == 0 => {
                Err(format!("{label} label is outside a switch statement"))
            }
            Some("break") if self.loop_depth == 0 && self.switch_depth == 0 => {
                Err("break statement is outside a loop or switch".into())
            }
            Some("continue") if self.loop_depth == 0 => {
                Err("continue statement is outside a loop".into())
            }
            _ => Ok(()),
        }
    }

    fn starts_statement_after_label(
        &self,
        start: usize,
        token: Option<&str>,
        parentheses: usize,
        initializer_braces: usize,
    ) -> bool {
        self.position > start
            && parentheses == 0
            && initializer_braces == 0
            && (control_word(token) || matches!(token, Some("catch" | "else" | "try")))
    }

    fn consume_simple_token(
        &mut self,
        token: &str,
        mut parentheses: usize,
        mut initializer_braces: usize,
    ) -> Result<(usize, usize, Option<Terminal>, bool), String> {
        match token {
            "(" => parentheses += 1,
            ")" => {
                if parentheses == 0 {
                    return Err("unmatched closing parenthesis in function body".into());
                }
                parentheses -= 1;
            }
            "{" => initializer_braces += 1,
            "}" => {
                if initializer_braces != 0 {
                    initializer_braces -= 1;
                } else if parentheses == 0 {
                    return Ok((parentheses, initializer_braces, None, true));
                }
            }
            _ => {}
        }
        self.position += 1;
        if token == ":" && parentheses == 0 && initializer_braces == 0 {
            return Ok((
                parentheses,
                initializer_braces,
                Some(Terminal::Label),
                false,
            ));
        }
        if token == ";" && parentheses == 0 && initializer_braces == 0 {
            return Ok((
                parentheses,
                initializer_braces,
                Some(Terminal::Terminated),
                false,
            ));
        }
        Ok((parentheses, initializer_braces, None, false))
    }

    fn simple(&mut self) -> Result<(), String> {
        let start = self.position;
        self.validate_simple_start(self.peek())?;
        let mut parentheses = 0;
        let mut initializer_braces = 0;
        let mut terminal = None;
        while self.position < self.tokens.len() {
            let token = self.peek();
            if self.starts_statement_after_label(start, token, parentheses, initializer_braces) {
                break;
            }
            let Some(token) = token else {
                break;
            };
            let stop;
            (parentheses, initializer_braces, terminal, stop) =
                self.consume_simple_token(token, parentheses, initializer_braces)?;
            if terminal.is_some() || stop {
                break;
            }
        }
        if parentheses != 0 || initializer_braces != 0 {
            return Err("unclosed expression delimiter in function body".into());
        }
        if terminal.is_none() {
            return Err("simple statement is missing its terminating semicolon".into());
        }
        Ok(())
    }

    fn statement(&mut self, nesting: i64) -> Result<(), String> {
        self.recursion_depth += 1;
        let result = self.statement_inner(nesting);
        self.recursion_depth -= 1;
        result
    }

    fn statement_inner(&mut self, nesting: i64) -> Result<(), String> {
        if self.recursion_depth > MAX_CONTROL_NESTING {
            return Err("control nesting exceeds the bounded limit".into());
        }
        self.attributes()?;
        match self.peek() {
            None => Ok(()),
            Some("{") => self.compound(nesting),
            Some("try") => self.try_statement(nesting),
            Some(token) if CONTROL_WORDS.contains(&token) => self.control(nesting),
            Some("catch") => Err("catch handler is missing its matching try".into()),
            Some("else") => Err("else statement is missing its matching if".into()),
            Some("}") => Err("unmatched closing brace in function body".into()),
            Some(_) => self.simple(),
        }
    }
}

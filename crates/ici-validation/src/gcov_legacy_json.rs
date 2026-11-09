//! Frozen v0.13.0 `analysis/gcov_json.py` per-report refusals for the legacy C++ coverage path.
//!
//! Native `gcov::parse_gzip` keeps its own strict parser. On the legacy path this module first
//! replays frozen's parse in frozen's order (gzip, UTF-8, the CPython 3.10 `json` scanner with
//! frozen's hooks, the generic tree limits, then the schema walk), so a report frozen refuses is
//! refused with frozen's `<code>: <message>`. It accepts nothing native would otherwise refuse:
//! a report that passes here is still parsed by `gcov::parse_gzip`.
//!
//! Python's `RecursionError` threshold depends on frozen's call stack; nesting deeper than
//! [`RECURSION_LIMIT`] containers is reported with CPython's recursion wording, shallower
//! nesting beyond 64 levels with frozen's `json_depth_limit`.
use flate2::{Decompress, FlushDecompress, Status};
use std::fmt::Write as _;

pub(crate) type Rejection = (&'static str, String);
type Checked<T> = Result<T, Rejection>;

pub(crate) const MAX_COMPRESSED_BYTES: usize = 8 * 1024 * 1024;
pub(crate) const MAX_DECOMPRESSED_BYTES: usize = 64 * 1024 * 1024;
const MAX_FILES: usize = 4_096;
const MAX_FUNCTIONS_PER_FILE: usize = 100_000;
const MAX_LINES_PER_FILE: usize = 1_000_000;
/// MAX_BRANCHES/CALLS/CONDITIONS/BLOCK_IDS_PER_LINE, MAX_CONDITION_TERMS and the prime-path
/// bounds are all 1,000,000 in frozen.
const MAX_ITEMS: usize = 1_000_000;
const MAX_STRING_BYTES: usize = 1024 * 1024;
const MAX_JSON_DEPTH: usize = 64;
const MAX_JSON_ARRAY_ITEMS: usize = 2_000_000;
const MAX_JSON_OBJECT_ITEMS: usize = 128;
const MAX_COUNTER: i128 = (1 << 64) - 1;
const MAX_SOURCE_POSITION: i128 = (1 << 32) - 1;
const RECURSION_LIMIT: usize = 1_000;

fn fail<T>(code: &'static str, message: impl Into<String>) -> Checked<T> {
    Err((code, message.into()))
}

/// Frozen `parse_gcov_json_gz` over one captured report (`_read_compressed_file`'s size check,
/// `_decompress_gzip`, `parse_gcov_json_bytes`).
/// `RECURSION_LIMIT` nested containers need more stack than a worker thread's default, and an
/// overflow would abort the whole run where frozen refuses the one report.
const SCAN_STACK_BYTES: usize = 64 * 1024 * 1024;

pub(crate) fn check_report(
    payload: &[u8],
    max_compressed: usize,
    max_decompressed: usize,
) -> Checked<()> {
    std::thread::scope(|scope| {
        let scan = std::thread::Builder::new()
            .stack_size(SCAN_STACK_BYTES)
            .spawn_scoped(scope, || {
                check_report_on_this_stack(payload, max_compressed, max_decompressed)
            });
        match scan {
            Ok(scan) => scan
                .join()
                .unwrap_or_else(|panic| std::panic::resume_unwind(panic)),
            Err(_) => check_report_on_this_stack(payload, max_compressed, max_decompressed),
        }
    })
}

fn check_report_on_this_stack(
    payload: &[u8],
    max_compressed: usize,
    max_decompressed: usize,
) -> Checked<()> {
    if payload.len() > max_compressed {
        return fail(
            "compressed_limit",
            format!("compressed gcov JSON is larger than {max_compressed} bytes"),
        );
    }
    let document = decompress(payload, max_decompressed)?;
    let value = decode(&document, max_decompressed)?;
    tree(&value, 0)?;
    parse_document(&value)
}

/// Frozen `_decompress_gzip`: `zlib.decompressobj(16 + MAX_WBITS).decompress(payload, max + 1)`.
fn decompress(payload: &[u8], maximum: usize) -> Checked<Vec<u8>> {
    if !payload.starts_with(&[0x1f, 0x8b]) {
        return fail("invalid_gzip", "gcov JSON evidence is not gzip-compressed");
    }
    let limit = maximum + 1;
    let mut inflater = Decompress::new_gzip(15);
    let mut output = vec![];
    let mut eof = false;
    let mut buffer = vec![0u8; 64 * 1024];
    while output.len() < limit {
        let consumed = inflater.total_in() as usize;
        let produced_before = inflater.total_out();
        let room = (limit - output.len()).min(buffer.len());
        let status = inflater
            .decompress(
                &payload[consumed..],
                &mut buffer[..room],
                FlushDecompress::Sync,
            )
            .map_err(|failure| {
                // CPython `zlib_error`: "Error %d while decompressing data: %.200s" (Z_DATA_ERROR).
                let reason: String = failure
                    .message()
                    .unwrap_or("invalid input data")
                    .chars()
                    .take(200)
                    .collect();
                (
                    "invalid_gzip",
                    format!("malformed gzip stream: Error -3 while decompressing data: {reason}"),
                )
            })?;
        let produced = (inflater.total_out() - produced_before) as usize;
        output.extend_from_slice(&buffer[..produced]);
        if status == Status::StreamEnd {
            eof = true;
            break;
        }
        let progressed = produced > 0 || inflater.total_in() as usize > consumed;
        if !progressed || inflater.total_in() as usize >= payload.len() && produced < room {
            break;
        }
    }
    let unconsumed = payload.len() - inflater.total_in() as usize;
    if output.len() > maximum || (!eof && unconsumed > 0) {
        return fail(
            "decompressed_limit",
            format!("decompressed JSON is larger than {maximum} bytes"),
        );
    }
    if !eof {
        return fail("invalid_gzip", "gzip stream is truncated");
    }
    if unconsumed > 0 {
        return fail("invalid_gzip", "gzip stream contains trailing data");
    }
    Ok(output)
}

/// A Python `str`: lossy text plus the lone surrogates `\uD800`-style escapes produce (their
/// code-point index and value), so equality, ordering and messages follow Python's.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Text {
    text: String,
    lone: Vec<(usize, u16)>,
}
impl Text {
    fn is(&self, name: &str) -> bool {
        self.lone.is_empty() && self.text == name
    }
    fn code_points(&self) -> Vec<u32> {
        let mut lone = self.lone.iter().peekable();
        self.text
            .chars()
            .enumerate()
            .map(|(index, character)| match lone.peek() {
                Some((at, unit)) if *at == index => {
                    let unit = *unit;
                    lone.next();
                    u32::from(unit)
                }
                _ => character as u32,
            })
            .collect()
    }
    /// Python `repr(str)`.
    fn repr(&self) -> String {
        let points = self.code_points();
        let has = |wanted: char| points.contains(&(wanted as u32));
        let quote = if has('\'') && !has('"') { '"' } else { '\'' };
        let mut output = String::new();
        output.push(quote);
        for point in points {
            match char::from_u32(point) {
                Some(character) if character == quote => {
                    output.push('\\');
                    output.push(character);
                }
                Some(character @ ('\'' | '"')) => output.push(character),
                Some(character) => {
                    let single = ici_analysis::unicode::python_repr(&character.to_string());
                    output.push_str(&single[1..single.len() - 1]);
                }
                None => {
                    write!(output, "\\u{point:04x}").unwrap();
                }
            }
        }
        output.push(quote);
        output
    }
}
#[derive(Clone, Debug)]
enum Value {
    Null,
    Bool,
    Int(i128),
    Str(Text),
    Array(Vec<Value>),
    Object(Vec<(Text, Value)>),
}

enum ScanError {
    /// `StopIteration(idx)`: "Expecting value" at the outermost `raw_decode`.
    Stop(usize),
    Decode(&'static str, usize),
    Hook(Rejection),
}
impl From<Rejection> for ScanError {
    fn from(rejection: Rejection) -> Self {
        Self::Hook(rejection)
    }
}

/// Frozen `_decode_json` (CPython 3.10 `json.loads(text, object_pairs_hook=..., parse_constant=...,
/// parse_float=..., parse_int=...)`).
fn decode(payload: &[u8], maximum: usize) -> Checked<Value> {
    if payload.len() > maximum {
        return fail(
            "decompressed_limit",
            format!("decompressed JSON is larger than {maximum} bytes"),
        );
    }
    let text = std::str::from_utf8(payload).map_err(|failure| {
        (
            "invalid_utf8",
            format!(
                "gcov JSON is not valid UTF-8: {}",
                utf8_error(payload, &failure)
            ),
        )
    })?;
    let malformed = |message: &str, position: usize| {
        let before = &text[..position];
        let index = before.chars().count();
        let line = before.matches('\n').count() + 1;
        let column = match before.rfind('\n') {
            Some(newline) => index - before[..newline].chars().count(),
            None => index + 1,
        };
        (
            "invalid_json",
            format!("malformed gcov JSON: {message}: line {line} column {column} (char {index})"),
        )
    };
    if text.starts_with('\u{feff}') {
        return Err(malformed(
            "Unexpected UTF-8 BOM (decode using utf-8-sig)",
            0,
        ));
    }
    let mut scanner = Scanner {
        bytes: text.as_bytes(),
        depth: 0,
    };
    let start = scanner.whitespace(0);
    match scanner.scan(start) {
        Ok((value, end)) => {
            let end = scanner.whitespace(end);
            if end != payload.len() {
                return Err(malformed("Extra data", end));
            }
            Ok(value)
        }
        Err(ScanError::Stop(position)) => Err(malformed("Expecting value", position)),
        Err(ScanError::Decode(message, position)) => Err(malformed(message, position)),
        Err(ScanError::Hook(rejection)) => Err(rejection),
    }
}

/// CPython's `UnicodeDecodeError` text for the strict UTF-8 codec.
fn utf8_error(payload: &[u8], failure: &std::str::Utf8Error) -> String {
    let start = failure.valid_up_to();
    let (length, reason) = match failure.error_len() {
        None => (payload.len() - start, "unexpected end of data"),
        Some(length) => (
            length,
            if length == 1 && !(0xc2..=0xf4).contains(&payload[start]) {
                "invalid start byte"
            } else {
                "invalid continuation byte"
            },
        ),
    };
    if length == 1 {
        format!(
            "'utf-8' codec can't decode byte 0x{:02x} in position {start}: {reason}",
            payload[start]
        )
    } else {
        format!(
            "'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
            start + length - 1
        )
    }
}

struct Scanner<'a> {
    bytes: &'a [u8],
    depth: usize,
}
impl Scanner<'_> {
    fn whitespace(&self, mut index: usize) -> usize {
        while index < self.bytes.len() && matches!(self.bytes[index], b' ' | b'\t' | b'\n' | b'\r')
        {
            index += 1;
        }
        index
    }
    fn starts(&self, index: usize, word: &str) -> bool {
        self.bytes[index..].starts_with(word.as_bytes())
    }
    /// `scan_once_unicode`.
    fn scan(&mut self, index: usize) -> Result<(Value, usize), ScanError> {
        let Some(&byte) = self.bytes.get(index) else {
            return Err(ScanError::Stop(index));
        };
        match byte {
            b'"' => {
                let (text, end) = self.string(index + 1)?;
                return Ok((Value::Str(text), end));
            }
            b'{' | b'[' => {
                self.depth += 1;
                if self.depth > RECURSION_LIMIT {
                    return Err(ScanError::Hook((
                        "invalid_json",
                        format!(
                            "malformed gcov JSON: maximum recursion depth exceeded while decoding a JSON {} from a unicode string",
                            if byte == b'{' { "object" } else { "array" }
                        ),
                    )));
                }
                let result = if byte == b'{' {
                    self.object(index + 1)
                } else {
                    self.array(index + 1)
                };
                self.depth -= 1;
                return result;
            }
            b'n' if self.starts(index, "null") => return Ok((Value::Null, index + 4)),
            b't' if self.starts(index, "true") => return Ok((Value::Bool, index + 4)),
            b'f' if self.starts(index, "false") => return Ok((Value::Bool, index + 5)),
            b'N' if self.starts(index, "NaN") => return Err(nonfinite("NaN")),
            b'I' if self.starts(index, "Infinity") => return Err(nonfinite("Infinity")),
            b'-' if self.starts(index, "-Infinity") => return Err(nonfinite("-Infinity")),
            _ => {}
        }
        self.number(index)
    }
    /// `_match_number_unicode` with frozen's `parse_int`/`parse_float` hooks.
    fn number(&self, start: usize) -> Result<(Value, usize), ScanError> {
        let bytes = self.bytes;
        let digit = |index: usize| bytes.get(index).is_some_and(u8::is_ascii_digit);
        let mut index = start;
        if bytes[index] == b'-' {
            index += 1;
            if index >= bytes.len() {
                return Err(ScanError::Stop(start));
            }
        }
        match bytes[index] {
            b'1'..=b'9' => {
                index += 1;
                while digit(index) {
                    index += 1;
                }
            }
            b'0' => index += 1,
            _ => return Err(ScanError::Stop(start)),
        }
        let mut float = false;
        if index + 1 < bytes.len() && bytes[index] == b'.' && digit(index + 1) {
            float = true;
            index += 2;
            while digit(index) {
                index += 1;
            }
        }
        if index + 1 < bytes.len() && matches!(bytes[index], b'e' | b'E') {
            let exponent = index;
            index += 1;
            if index + 1 < bytes.len() && matches!(bytes[index], b'-' | b'+') {
                index += 1;
            }
            while digit(index) {
                index += 1;
            }
            if digit(index - 1) {
                float = true;
            } else {
                index = exponent;
            }
        }
        let literal = std::str::from_utf8(&bytes[start..index]).expect("ASCII number");
        if float {
            return Err(ScanError::Hook((
                "number_type",
                format!(
                    "floating-point JSON number {} is not allowed",
                    python_str_repr(literal)
                ),
            )));
        }
        if literal.trim_start_matches('-').len() > 20 {
            return Err(ScanError::Hook((
                "number_bound",
                "JSON integer exceeds the supported 64-bit range".into(),
            )));
        }
        let value = literal.parse::<i128>().expect("bounded decimal integer");
        Ok((Value::Int(value), index))
    }
    /// `scanstring_unicode` (strict), `index` after the opening quote.
    fn string(&self, mut end: usize) -> Result<(Text, usize), ScanError> {
        let bytes = self.bytes;
        let begin = end - 1;
        let mut units: Vec<u32> = vec![];
        loop {
            let mut next = end;
            let mut found = 0u8;
            while next < bytes.len() {
                let byte = bytes[next];
                if byte == b'"' || byte == b'\\' {
                    found = byte;
                    break;
                }
                if byte <= 0x1f {
                    return Err(ScanError::Decode("Invalid control character at", next));
                }
                next += 1;
            }
            if found == 0 {
                return Err(ScanError::Decode("Unterminated string starting at", begin));
            }
            let chunk = std::str::from_utf8(&bytes[end..next]).expect("UTF-8 text");
            units.extend(chunk.chars().map(|character| character as u32));
            next += 1;
            if found == b'"' {
                end = next;
                break;
            }
            if next == bytes.len() {
                return Err(ScanError::Decode("Unterminated string starting at", begin));
            }
            let escape = bytes[next];
            if escape != b'u' {
                end = next + 1;
                let decoded = match escape {
                    b'"' => '"',
                    b'\\' => '\\',
                    b'/' => '/',
                    b'b' => '\u{8}',
                    b'f' => '\u{c}',
                    b'n' => '\n',
                    b'r' => '\r',
                    b't' => '\t',
                    _ => return Err(ScanError::Decode("Invalid \\escape", end - 2)),
                };
                units.push(decoded as u32);
                continue;
            }
            next += 1;
            end = next + 4;
            if end >= bytes.len() {
                return Err(ScanError::Decode("Invalid \\uXXXX escape", next - 1));
            }
            let hex = |at: usize| -> Option<u32> {
                std::str::from_utf8(&bytes[at..at + 4])
                    .ok()
                    .filter(|digits| digits.bytes().all(|byte| byte.is_ascii_hexdigit()))
                    .and_then(|digits| u32::from_str_radix(digits, 16).ok())
            };
            let Some(mut unit) = hex(next) else {
                return Err(ScanError::Decode("Invalid \\uXXXX escape", end - 5));
            };
            next = end;
            if (0xd800..=0xdbff).contains(&unit)
                && end + 6 < bytes.len()
                && bytes[next] == b'\\'
                && bytes[next + 1] == b'u'
            {
                let pair = next + 2;
                end += 6;
                let Some(low) = hex(pair) else {
                    return Err(ScanError::Decode("Invalid \\uXXXX escape", end - 5));
                };
                if (0xdc00..=0xdfff).contains(&low) {
                    unit = 0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00);
                } else {
                    end -= 6;
                }
            }
            units.push(unit);
        }
        let mut text = String::with_capacity(units.len());
        let mut lone = vec![];
        for (index, unit) in units.into_iter().enumerate() {
            match char::from_u32(unit) {
                Some(character) => text.push(character),
                None => {
                    text.push('\u{fffd}');
                    lone.push((index, unit as u16));
                }
            }
        }
        Ok((Text { text, lone }, end))
    }
    /// `_parse_object_unicode`, then frozen's `_reject_duplicate_keys` pairs hook.
    fn object(&mut self, index: usize) -> Result<(Value, usize), ScanError> {
        let mut index = self.whitespace(index);
        let mut pairs: Vec<(Text, Value)> = vec![];
        if self.bytes.get(index) != Some(&b'}') {
            loop {
                if self.bytes.get(index) != Some(&b'"') {
                    return Err(ScanError::Decode(
                        "Expecting property name enclosed in double quotes",
                        index,
                    ));
                }
                let (key, next) = self.string(index + 1)?;
                index = self.whitespace(next);
                if self.bytes.get(index) != Some(&b':') {
                    return Err(ScanError::Decode("Expecting ':' delimiter", index));
                }
                index = self.whitespace(index + 1);
                let (value, next) = self.scan(index)?;
                pairs.push((key, value));
                index = self.whitespace(next);
                if self.bytes.get(index) == Some(&b'}') {
                    break;
                }
                if self.bytes.get(index) != Some(&b',') {
                    return Err(ScanError::Decode("Expecting ',' delimiter", index));
                }
                index = self.whitespace(index + 1);
            }
        }
        let mut seen: Vec<&Text> = vec![];
        for (key, _) in &pairs {
            if seen.contains(&key) {
                return Err(ScanError::Hook((
                    "duplicate_key",
                    format!("duplicate object key {}", key.repr()),
                )));
            }
            seen.push(key);
        }
        Ok((Value::Object(pairs), index + 1))
    }
    /// `_parse_array_unicode`.
    fn array(&mut self, index: usize) -> Result<(Value, usize), ScanError> {
        let mut index = self.whitespace(index);
        let mut items = vec![];
        if self.bytes.get(index) != Some(&b']') {
            loop {
                let (value, next) = self.scan(index)?;
                items.push(value);
                index = self.whitespace(next);
                if self.bytes.get(index) == Some(&b']') {
                    break;
                }
                if self.bytes.get(index) != Some(&b',') {
                    return Err(ScanError::Decode("Expecting ',' delimiter", index));
                }
                index = self.whitespace(index + 1);
            }
        }
        Ok((Value::Array(items), index + 1))
    }
}

fn nonfinite(literal: &str) -> ScanError {
    ScanError::Hook((
        "nonfinite_number",
        format!("JSON number {} is not finite", python_str_repr(literal)),
    ))
}
fn python_str_repr(literal: &str) -> String {
    format!("'{literal}'")
}

/// Frozen `_validate_json_tree`.
fn tree(value: &Value, depth: usize) -> Checked<()> {
    if depth > MAX_JSON_DEPTH {
        return fail(
            "json_depth_limit",
            format!("JSON nesting exceeds {MAX_JSON_DEPTH} levels"),
        );
    }
    match value {
        Value::Object(pairs) => {
            if pairs.len() > MAX_JSON_OBJECT_ITEMS {
                return fail(
                    "object_count_limit",
                    "JSON object contains too many members",
                );
            }
            for (_, child) in pairs {
                tree(child, depth + 1)?;
            }
        }
        Value::Array(items) => {
            if items.len() > MAX_JSON_ARRAY_ITEMS {
                return fail("array_count_limit", "JSON array contains too many items");
            }
            for child in items {
                tree(child, depth + 1)?;
            }
        }
        _ => {}
    }
    Ok(())
}

type Object<'a> = &'a [(Text, Value)];
fn get<'a>(object: Object<'a>, name: &str) -> Option<&'a Value> {
    object
        .iter()
        .find(|(key, _)| key.is(name))
        .map(|(_, value)| value)
}
fn field<'a>(object: Object<'a>, name: &str) -> &'a Value {
    get(object, name).expect("required field checked by object()")
}
fn has(object: Object<'_>, name: &str) -> bool {
    get(object, name).is_some()
}

/// Frozen `_object`.
fn object<'a>(
    value: &'a Value,
    context: &str,
    required: &[&str],
    allowed: &[&str],
) -> Checked<Object<'a>> {
    let Value::Object(pairs) = value else {
        return fail("type", format!("{context} must be an object"));
    };
    let mut missing: Vec<&str> = required
        .iter()
        .copied()
        .filter(|name| !has(pairs, name))
        .collect();
    if !missing.is_empty() {
        missing.sort_unstable();
        return fail(
            "missing_field",
            format!("{context} is missing {}", missing.join(", ")),
        );
    }
    let mut unexpected: Vec<&Text> = pairs
        .iter()
        .map(|(key, _)| key)
        .filter(|key| !allowed.iter().any(|name| key.is(name)))
        .collect();
    if !unexpected.is_empty() {
        unexpected.sort_by_key(|key| key.code_points());
        let names: Vec<&str> = unexpected.iter().map(|key| key.text.as_str()).collect();
        return fail(
            "unknown_field",
            format!("{context} has unknown field(s): {}", names.join(", ")),
        );
    }
    Ok(pairs)
}

/// Frozen `_string`.
fn string<'a>(value: &'a Value, context: &str, allow_empty: bool) -> Checked<&'a Text> {
    let Value::Str(text) = value else {
        return fail("type", format!("{context} must be a string"));
    };
    if !allow_empty && text.text.is_empty() {
        return fail("empty_string", format!("{context} must not be empty"));
    }
    if let Some((first, _)) = text.lone.first() {
        // CPython's strict UTF-8 encoder reports the run of consecutive surrogates.
        let mut last = *first;
        let points = text.code_points();
        while points
            .get(last + 1)
            .is_some_and(|point| (0xd800..=0xdfff).contains(point))
        {
            last += 1;
        }
        let detail = if last == *first {
            format!(
                "'utf-8' codec can't encode character '\\u{:04x}' in position {first}: surrogates not allowed",
                points[*first]
            )
        } else {
            format!(
                "'utf-8' codec can't encode characters in position {first}-{last}: surrogates not allowed"
            )
        };
        return fail(
            "invalid_utf8",
            format!("{context} cannot be encoded as UTF-8: {detail}"),
        );
    }
    if text.text.len() > MAX_STRING_BYTES {
        return fail(
            "string_limit",
            format!("{context} is larger than {MAX_STRING_BYTES} bytes"),
        );
    }
    if text.text.chars().any(|character| (character as u32) < 0x20) {
        return fail(
            "control_character",
            format!("{context} contains a control character"),
        );
    }
    Ok(text)
}

/// Frozen `_integer`.
fn integer(value: &Value, context: &str, minimum: i128, maximum: i128) -> Checked<i128> {
    let Value::Int(number) = value else {
        return fail("type", format!("{context} must be an integer"));
    };
    if *number < minimum || *number > maximum {
        return fail(
            "count_bound",
            format!("{context} must be between {minimum} and {maximum}"),
        );
    }
    Ok(*number)
}
fn counter(value: &Value, context: &str) -> Checked<i128> {
    integer(value, context, 0, MAX_COUNTER)
}
fn position(value: &Value, context: &str, minimum: i128) -> Checked<i128> {
    integer(value, context, minimum, MAX_SOURCE_POSITION)
}

/// Frozen `_boolean`.
fn boolean(value: &Value, context: &str) -> Checked<()> {
    match value {
        Value::Bool => Ok(()),
        _ => fail("type", format!("{context} must be a boolean")),
    }
}

/// Frozen `_array`.
fn array<'a>(value: &'a Value, context: &str, maximum: usize) -> Checked<&'a [Value]> {
    let Value::Array(items) = value else {
        return fail("type", format!("{context} must be an array"));
    };
    if items.len() > maximum {
        return fail(
            "count_bound",
            format!("{context} contains more than {maximum} items"),
        );
    }
    Ok(items)
}
fn optional_array<'a>(
    object: Object<'a>,
    name: &str,
    context: &str,
    maximum: usize,
) -> Checked<&'a [Value]> {
    match get(object, name) {
        Some(value) => array(value, context, maximum),
        None => Ok(&[]),
    }
}

fn positions(values: &[Value], context: &str) -> Checked<()> {
    for (index, item) in values.iter().enumerate() {
        position(item, &format!("{context}[{index}]"), 0)?;
    }
    Ok(())
}

/// Frozen `_parse_document`.
fn parse_document(value: &Value) -> Checked<()> {
    let required = ["data_file", "format_version", "gcc_version", "files"];
    let root = object(
        value,
        "root",
        &required,
        &[
            "data_file",
            "format_version",
            "gcc_version",
            "files",
            "current_working_directory",
        ],
    )?;
    let version = match field(root, "format_version") {
        Value::Str(text) if text.is("1") => 1,
        Value::Str(text) if text.is("2") => 2,
        Value::Str(_) => {
            return fail(
                "unsupported_version",
                "format_version must be \"1\" or \"2\"",
            );
        }
        _ => return fail("type", "format_version must be a string"),
    };
    let files = array(field(root, "files"), "root.files", MAX_FILES)?;
    let mut paths: Vec<&Text> = vec![];
    for (index, item) in files.iter().enumerate() {
        paths.push(parse_file(item, index, version)?);
    }
    for (index, path) in paths.iter().enumerate() {
        if paths[..index].contains(path) {
            return fail(
                "duplicate_file",
                "root.files contains duplicate source paths",
            );
        }
    }
    let empty = Value::Str(Text {
        text: String::new(),
        lone: vec![],
    });
    string(
        get(root, "current_working_directory").unwrap_or(&empty),
        "root.current_working_directory",
        true,
    )?;
    string(field(root, "data_file"), "root.data_file", false)?;
    let gcc = string(field(root, "gcc_version"), "gcc_version", false)?;
    let pattern = regex::Regex::new(r"^\d+(?:\.\d+)*(?:[ \t]+[^ \t].*)?$").expect("constant regex");
    if !pattern.is_match(&gcc.text) {
        return fail(
            "invalid_gcc_version",
            "gcc_version must start with a numeric GCC version",
        );
    }
    Ok(())
}

/// Frozen `_parse_file`; returns the validated `file` path.
fn parse_file(value: &Value, index: usize, version: u8) -> Checked<&Text> {
    let context = format!("file[{index}]");
    let fields = ["file", "functions", "lines"];
    let item = object(value, &context, &fields, &fields)?;
    let functions = array(
        field(item, "functions"),
        &format!("{context}.functions"),
        MAX_FUNCTIONS_PER_FILE,
    )?;
    let lines = array(
        field(item, "lines"),
        &format!("{context}.lines"),
        MAX_LINES_PER_FILE,
    )?;
    let mut identities = vec![];
    for (function_index, function) in functions.iter().enumerate() {
        identities.push(parse_function(function, function_index)?);
    }
    for (line_index, line) in lines.iter().enumerate() {
        parse_line(line, line_index, version)?;
    }
    for (position, identity) in identities.iter().enumerate() {
        if identities[..position].contains(identity) {
            return fail(
                "duplicate_function",
                format!("{context}.functions contains duplicate function identities"),
            );
        }
    }
    string(field(item, "file"), &format!("{context}.file"), false)
}

type FunctionIdentity<'a> = (&'a Text, i128, i128, i128, i128);

/// Frozen `_parse_function`; returns its identity tuple.
fn parse_function(value: &Value, index: usize) -> Checked<FunctionIdentity<'_>> {
    let context = format!("function[{index}]");
    let fields = [
        "blocks",
        "blocks_executed",
        "demangled_name",
        "end_column",
        "end_line",
        "execution_count",
        "name",
        "start_column",
        "start_line",
    ];
    let prime = [
        "total_prime_paths",
        "covered_prime_paths",
        "prime_path_coverage",
    ];
    let allowed: Vec<&str> = fields.iter().chain(&prime).copied().collect();
    let item = object(value, &context, &fields, &allowed)?;
    let blocks = counter(field(item, "blocks"), &format!("{context}.blocks"))?;
    let executed = counter(
        field(item, "blocks_executed"),
        &format!("{context}.blocks_executed"),
    )?;
    if executed > blocks {
        return fail(
            "count_bound",
            format!("{context}.blocks_executed cannot exceed blocks"),
        );
    }
    let start_line = position(
        field(item, "start_line"),
        &format!("{context}.start_line"),
        1,
    )?;
    let end_line = position(field(item, "end_line"), &format!("{context}.end_line"), 1)?;
    let start_column = position(
        field(item, "start_column"),
        &format!("{context}.start_column"),
        1,
    )?;
    let end_column = position(
        field(item, "end_column"),
        &format!("{context}.end_column"),
        1,
    )?;
    if (end_line, end_column) < (start_line, start_column) {
        return fail("position_order", format!("{context} ends before it starts"));
    }
    let present = prime.iter().filter(|name| has(item, name)).count();
    if present != 0 && present != prime.len() {
        return fail(
            "missing_field",
            format!("{context} must provide all prime-path fields when any are present"),
        );
    }
    if present != 0 {
        let total = counter(
            field(item, "total_prime_paths"),
            &format!("{context}.total_prime_paths"),
        )?;
        let covered = counter(
            field(item, "covered_prime_paths"),
            &format!("{context}.covered_prime_paths"),
        )?;
        if covered > total {
            return fail(
                "count_bound",
                format!("{context}.covered_prime_paths cannot exceed total_prime_paths"),
            );
        }
        let paths = array(
            field(item, "prime_path_coverage"),
            &format!("{context}.prime_path_coverage"),
            MAX_ITEMS,
        )?;
        if paths.len() as i128 > total {
            return fail(
                "count_bound",
                format!("{context}.prime_path_coverage cannot exceed total_prime_paths"),
            );
        }
        let mut ids = vec![];
        for (path_index, path) in paths.iter().enumerate() {
            ids.push(parse_prime_path(path, path_index)?);
        }
        for (position, id) in ids.iter().enumerate() {
            if ids[..position].contains(id) {
                return fail(
                    "duplicate_prime_path",
                    format!("{context} contains duplicate prime-path ids"),
                );
            }
        }
    }
    string(
        field(item, "demangled_name"),
        &format!("{context}.demangled_name"),
        true,
    )?;
    counter(
        field(item, "execution_count"),
        &format!("{context}.execution_count"),
    )?;
    let name = string(field(item, "name"), &format!("{context}.name"), true)?;
    Ok((name, start_line, start_column, end_line, end_column))
}

/// Frozen `_parse_prime_path`; returns its id.
fn parse_prime_path(value: &Value, index: usize) -> Checked<i128> {
    let context = format!("prime_path[{index}]");
    let item = object(value, &context, &["id", "sequence"], &["id", "sequence"])?;
    let sequence = array(
        field(item, "sequence"),
        &format!("{context}.sequence"),
        MAX_ITEMS,
    )?;
    if sequence.is_empty() {
        return fail(
            "missing_data",
            format!("{context}.sequence must not be empty"),
        );
    }
    let id = position(field(item, "id"), &format!("{context}.id"), 0)?;
    for (step_index, step) in sequence.iter().enumerate() {
        let step_context = format!("{context}.sequence[{step_index}]");
        let fields = ["block_id", "locations", "edge_kind"];
        let step = object(step, &step_context, &fields, &fields)?;
        let locations = array(
            field(step, "locations"),
            &format!("{step_context}.locations"),
            MAX_ITEMS,
        )?;
        position(
            field(step, "block_id"),
            &format!("{step_context}.block_id"),
            0,
        )?;
        for (location_index, location) in locations.iter().enumerate() {
            let location_context = format!("{step_context}.locations[{location_index}]");
            let fields = ["file", "line_numbers"];
            let location = object(location, &location_context, &fields, &fields)?;
            let numbers = array(
                field(location, "line_numbers"),
                &format!("{location_context}.line_numbers"),
                MAX_LINES_PER_FILE,
            )?;
            string(
                field(location, "file"),
                &format!("{location_context}.file"),
                false,
            )?;
            for (number_index, number) in numbers.iter().enumerate() {
                position(
                    number,
                    &format!("{location_context}.line_numbers[{number_index}]"),
                    1,
                )?;
            }
        }
        string(
            field(step, "edge_kind"),
            &format!("{step_context}.edge_kind"),
            true,
        )?;
    }
    Ok(id)
}

/// Frozen `_parse_line`.
fn parse_line(value: &Value, index: usize, version: u8) -> Checked<()> {
    let context = format!("line[{index}]");
    let mut required = vec!["count", "line_number", "unexecuted_block"];
    if version >= 2 {
        required.push("block_ids");
    }
    let allowed = [
        "block_ids",
        "branches",
        "calls",
        "conditions",
        "count",
        "line_number",
        "unexecuted_block",
        "function_name",
    ];
    let item = object(value, &context, &required, &allowed)?;
    if version < 2 && has(item, "calls") {
        return fail(
            "version_field",
            format!("{context}.calls requires format_version 2"),
        );
    }
    let branches = optional_array(item, "branches", &format!("{context}.branches"), MAX_ITEMS)?;
    let calls = optional_array(item, "calls", &format!("{context}.calls"), MAX_ITEMS)?;
    let conditions = optional_array(
        item,
        "conditions",
        &format!("{context}.conditions"),
        MAX_ITEMS,
    )?;
    position(
        field(item, "line_number"),
        &format!("{context}.line_number"),
        1,
    )?;
    if let Some(ids) = get(item, "block_ids") {
        let block_context = format!("{context}.block_ids");
        positions(array(ids, &block_context, MAX_ITEMS)?, &block_context)?;
    }
    for (branch_index, branch) in branches.iter().enumerate() {
        parse_branch(branch, branch_index, version)?;
    }
    for (call_index, call) in calls.iter().enumerate() {
        let call_context = format!("call[{call_index}]");
        let fields = ["destination_block_id", "returned", "source_block_id"];
        let call = object(call, &call_context, &fields, &fields)?;
        position(
            field(call, "destination_block_id"),
            &format!("{call_context}.destination_block_id"),
            0,
        )?;
        counter(field(call, "returned"), &format!("{call_context}.returned"))?;
        position(
            field(call, "source_block_id"),
            &format!("{call_context}.source_block_id"),
            0,
        )?;
    }
    for (condition_index, condition) in conditions.iter().enumerate() {
        let condition_context = format!("condition[{condition_index}]");
        let fields = ["count", "covered", "not_covered_false", "not_covered_true"];
        let condition = object(condition, &condition_context, &fields, &fields)?;
        let count = counter(
            field(condition, "count"),
            &format!("{condition_context}.count"),
        )?;
        let covered = counter(
            field(condition, "covered"),
            &format!("{condition_context}.covered"),
        )?;
        if covered > count {
            return fail(
                "count_bound",
                format!("{condition_context}.covered cannot exceed count"),
            );
        }
        for name in ["not_covered_false", "not_covered_true"] {
            let terms_context = format!("{condition_context}.{name}");
            positions(
                array(field(condition, name), &terms_context, MAX_ITEMS)?,
                &terms_context,
            )?;
        }
    }
    counter(field(item, "count"), &format!("{context}.count"))?;
    boolean(
        field(item, "unexecuted_block"),
        &format!("{context}.unexecuted_block"),
    )?;
    let empty = Value::Str(Text {
        text: String::new(),
        lone: vec![],
    });
    string(
        get(item, "function_name").unwrap_or(&empty),
        &format!("{context}.function_name"),
        true,
    )?;
    Ok(())
}

/// Frozen `_parse_branch`.
fn parse_branch(value: &Value, index: usize, version: u8) -> Checked<()> {
    let context = format!("branch[{index}]");
    let common = ["count", "fallthrough", "throw"];
    let blocks = ["destination_block_id", "source_block_id"];
    let mut required = common.to_vec();
    if version >= 2 {
        required.extend(blocks);
    }
    let allowed: Vec<&str> = common.iter().chain(&blocks).copied().collect();
    let item = object(value, &context, &required, &allowed)?;
    let present = blocks.iter().filter(|name| has(item, name)).count();
    if present == 1 {
        return fail(
            "missing_field",
            format!("{context} must provide both block IDs when either is present"),
        );
    }
    counter(field(item, "count"), &format!("{context}.count"))?;
    if present == 2 {
        position(
            field(item, "destination_block_id"),
            &format!("{context}.destination_block_id"),
            0,
        )?;
    }
    boolean(
        field(item, "fallthrough"),
        &format!("{context}.fallthrough"),
    )?;
    if present == 2 {
        position(
            field(item, "source_block_id"),
            &format!("{context}.source_block_id"),
            0,
        )?;
    }
    boolean(field(item, "throw"), &format!("{context}.throw"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::{Compression, write::GzEncoder};
    use std::io::Write;

    fn gzip(bytes: &[u8]) -> Vec<u8> {
        let mut encoder = GzEncoder::new(vec![], Compression::fast());
        encoder.write_all(bytes).unwrap();
        encoder.finish().unwrap()
    }
    const GOOD: &str = r#"{"format_version":"2","gcc_version":"15.2.0","data_file":"a.gcda","files":[{"file":"a.cpp","functions":[{"blocks":2,"blocks_executed":1,"demangled_name":"f()","name":"_Z1fv","start_line":1,"end_line":3,"start_column":1,"end_column":2,"execution_count":1}],"lines":[{"line_number":2,"count":1,"unexecuted_block":false,"block_ids":[0],"branches":[{"count":1,"fallthrough":true,"throw":false,"source_block_id":0,"destination_block_id":1}]}]}]}"#;

    /// Every case of tests/rust-parity/cpp/gcov-json-errors-oracle.json (frozen
    /// `parse_gcov_json_gz` under CPython 3.10, written by capture_gcov_json_errors.py): the
    /// same payload and budgets give frozen's code and `str(GcovJsonError)`, or acceptance.
    #[test]
    fn frozen_capture_tests() {
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/rust-parity/cpp/gcov-json-errors-oracle.json"
        ))
        .unwrap();
        let cases = golden["cases"].as_array().unwrap();
        assert_eq!(cases.len(), 94);
        let mut mismatches = vec![];
        for case in cases {
            let payload = decode_base64(case["payload"].as_str().unwrap());
            let native = check_report(
                &payload,
                case["max_compressed"].as_u64().unwrap() as usize,
                case["max_decompressed"].as_u64().unwrap() as usize,
            )
            .err()
            .map(|(code, message)| serde_json::json!({"code": code, "message": format!("{code}: {message}")}));
            let frozen = (!case["result"].is_null()).then(|| case["result"].clone());
            if native != frozen {
                mismatches.push(format!(
                    "{}: native {native:?} frozen {frozen:?}",
                    case["name"]
                ));
            }
        }
        assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
    }
    fn decode_base64(text: &str) -> Vec<u8> {
        let value = |byte: u8| -> u32 {
            match byte {
                b'A'..=b'Z' => u32::from(byte - b'A'),
                b'a'..=b'z' => u32::from(byte - b'a') + 26,
                b'0'..=b'9' => u32::from(byte - b'0') + 52,
                b'+' => 62,
                b'/' => 63,
                _ => panic!("base64"),
            }
        };
        let mut output = vec![];
        for chunk in text.as_bytes().chunks(4) {
            let digits: Vec<u8> = chunk.iter().copied().filter(|byte| *byte != b'=').collect();
            let mut bits = 0u32;
            for byte in &digits {
                bits = bits << 6 | value(*byte);
            }
            bits <<= 6 * (4 - digits.len() as u32);
            let bytes = bits.to_be_bytes();
            output.extend_from_slice(&bytes[1..digits.len()]);
        }
        output
    }
    /// Frozen gcov_json.py:284 `except (json.JSONDecodeError, RecursionError, UnicodeDecodeError,
    /// ValueError) as exc:` makes CPython's recursion error an `invalid_json` refusal. Where it
    /// fires depends on frozen's interpreter stack, so no capture fixes the exact depth: native
    /// fixes it at `RECURSION_LIMIT`, and any shallower nesting past 64 levels is frozen's
    /// :292-293 `json_depth_limit`.
    #[test]
    fn nesting_past_the_recursion_limit_is_invalid_json_and_below_it_the_depth_limit() {
        // The root is an object: frozen's type check on the root comes first. `depth` counts the
        // arrays under it, so the deepest container is at `depth + 1`.
        let nested = |depth: usize| {
            let text = format!(r#"{{"x":{}{}}}"#, "[".repeat(depth), "]".repeat(depth));
            gzip(text.as_bytes())
        };
        let check = |depth: usize| {
            check_report(&nested(depth), MAX_COMPRESSED_BYTES, MAX_DECOMPRESSED_BYTES).unwrap_err()
        };
        assert_eq!(check(100).0, "json_depth_limit");
        assert_eq!(check(RECURSION_LIMIT - 1).0, "json_depth_limit");
        assert_eq!(
            check(RECURSION_LIMIT - 1).1,
            "JSON nesting exceeds 64 levels"
        );
        let (code, message) = check(RECURSION_LIMIT);
        assert_eq!(code, "invalid_json");
        assert!(
            message.starts_with("malformed gcov JSON: maximum recursion depth exceeded"),
            "{message}"
        );
    }
    #[test]
    fn a_frozen_valid_report_passes() {
        check_report(
            &gzip(GOOD.as_bytes()),
            MAX_COMPRESSED_BYTES,
            MAX_DECOMPRESSED_BYTES,
        )
        .unwrap();
    }
}

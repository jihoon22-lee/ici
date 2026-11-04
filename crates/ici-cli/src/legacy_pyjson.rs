//! Frozen `json.loads` and `str()` of the values `parse_cmake_capabilities` reads: objects keep
//! their key order (a repeated key keeps its first position and its last value), `NaN` and
//! `±Infinity` are accepted, integers keep every digit, and a non-string value prints as
//! Python's `repr`. A lone surrogate escape cannot live in a Rust string and decodes to U+FFFD.

/// One decoded Python value.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Py {
    None,
    Bool(bool),
    /// An integer in its canonical decimal digits (`int` is unbounded).
    Int(String),
    Float(f64),
    Str(String),
    List(Vec<Py>),
    Dict(Vec<(String, Py)>),
}

impl Py {
    /// Python truthiness.
    pub(crate) fn truthy(&self) -> bool {
        match self {
            Py::None => false,
            Py::Bool(flag) => *flag,
            Py::Int(digits) => digits != "0",
            Py::Float(number) => *number != 0.0,
            Py::Str(text) => !text.is_empty(),
            Py::List(items) => !items.is_empty(),
            Py::Dict(items) => !items.is_empty(),
        }
    }
    /// `dict.get(key)`.
    pub(crate) fn get(&self, key: &str) -> Option<&Py> {
        match self {
            Py::Dict(items) => items
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value),
            _ => None,
        }
    }
    /// Python `str(value)`.
    pub(crate) fn str(&self) -> String {
        match self {
            Py::Str(text) => text.clone(),
            other => other.repr(),
        }
    }
    /// Python `repr(value)`.
    pub(crate) fn repr(&self) -> String {
        match self {
            Py::None => "None".into(),
            Py::Bool(true) => "True".into(),
            Py::Bool(false) => "False".into(),
            Py::Int(digits) => digits.clone(),
            Py::Float(number) => float_repr(*number),
            Py::Str(text) => str_repr(text),
            Py::List(items) => format!(
                "[{}]",
                items.iter().map(Py::repr).collect::<Vec<_>>().join(", ")
            ),
            Py::Dict(items) => format!(
                "{{{}}}",
                items
                    .iter()
                    .map(|(key, value)| format!("{}: {}", str_repr(key), value.repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
}

/// Python `json.loads(text)`; `None` where it raises `JSONDecodeError`.
pub(crate) fn loads(text: &str) -> Option<Py> {
    let mut parser = Parser {
        text: text.as_bytes(),
        at: 0,
    };
    parser.space();
    let value = parser.value(0)?;
    parser.space();
    (parser.at == parser.text.len()).then_some(value)
}

struct Parser<'a> {
    text: &'a [u8],
    at: usize,
}

impl Parser<'_> {
    fn space(&mut self) {
        while matches!(self.text.get(self.at), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }
    fn eat(&mut self, literal: &str) -> bool {
        if self.text[self.at..].starts_with(literal.as_bytes()) {
            self.at += literal.len();
            true
        } else {
            false
        }
    }
    fn value(&mut self, depth: usize) -> Option<Py> {
        // Python's decoder recurses until RecursionError; a bound keeps native's stack safe.
        if depth > 512 {
            return None;
        }
        match *self.text.get(self.at)? {
            b'{' => {
                self.at += 1;
                let mut items: Vec<(String, Py)> = vec![];
                self.space();
                if self.eat("}") {
                    return Some(Py::Dict(items));
                }
                loop {
                    self.space();
                    if self.text.get(self.at) != Some(&b'"') {
                        return None;
                    }
                    let key = self.string()?;
                    self.space();
                    if !self.eat(":") {
                        return None;
                    }
                    self.space();
                    let value = self.value(depth + 1)?;
                    match items.iter_mut().find(|(name, _)| *name == key) {
                        Some(slot) => slot.1 = value,
                        None => items.push((key, value)),
                    }
                    self.space();
                    if self.eat("}") {
                        return Some(Py::Dict(items));
                    }
                    if !self.eat(",") {
                        return None;
                    }
                }
            }
            b'[' => {
                self.at += 1;
                let mut items = vec![];
                self.space();
                if self.eat("]") {
                    return Some(Py::List(items));
                }
                loop {
                    self.space();
                    items.push(self.value(depth + 1)?);
                    self.space();
                    if self.eat("]") {
                        return Some(Py::List(items));
                    }
                    if !self.eat(",") {
                        return None;
                    }
                }
            }
            b'"' => self.string().map(Py::Str),
            _ if self.eat("null") => Some(Py::None),
            _ if self.eat("true") => Some(Py::Bool(true)),
            _ if self.eat("false") => Some(Py::Bool(false)),
            _ if self.eat("NaN") => Some(Py::Float(f64::NAN)),
            _ if self.eat("Infinity") => Some(Py::Float(f64::INFINITY)),
            _ if self.eat("-Infinity") => Some(Py::Float(f64::NEG_INFINITY)),
            _ => self.number(),
        }
    }
    /// `json.scanner` NUMBER_RE: `(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?`.
    fn number(&mut self) -> Option<Py> {
        let start = self.at;
        let digits = |parser: &mut Self| {
            let from = parser.at;
            while parser.text.get(parser.at).is_some_and(u8::is_ascii_digit) {
                parser.at += 1;
            }
            parser.at - from
        };
        self.eat("-");
        if self.eat("0") {
        } else if digits(self) == 0 {
            return None;
        }
        let integral = self.at;
        let mut float = false;
        if self.text.get(self.at) == Some(&b'.') {
            let mark = self.at;
            self.at += 1;
            if digits(self) == 0 {
                self.at = mark;
            } else {
                float = true;
            }
        }
        if matches!(self.text.get(self.at), Some(b'e' | b'E')) {
            let mark = self.at;
            self.at += 1;
            if matches!(self.text.get(self.at), Some(b'+' | b'-')) {
                self.at += 1;
            }
            if digits(self) == 0 {
                self.at = mark;
            } else {
                float = true;
            }
        }
        let token = std::str::from_utf8(&self.text[start..self.at]).ok()?;
        if float {
            return token.parse().ok().map(Py::Float);
        }
        debug_assert_eq!(integral, self.at);
        let negative = token.starts_with('-');
        let magnitude = token.trim_start_matches('-');
        Some(Py::Int(if magnitude == "0" || !negative {
            magnitude.to_owned()
        } else {
            format!("-{magnitude}")
        }))
    }
    fn hex4(&mut self) -> Option<u32> {
        let digits = self.text.get(self.at..self.at + 4)?;
        let text = std::str::from_utf8(digits).ok()?;
        if !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return None;
        }
        self.at += 4;
        u32::from_str_radix(text, 16).ok()
    }
    /// A JSON string (`strict=True`: control characters must be escaped).
    fn string(&mut self) -> Option<String> {
        self.at += 1;
        let mut text = String::new();
        loop {
            let rest = std::str::from_utf8(&self.text[self.at..]).ok()?;
            let ch = rest.chars().next()?;
            self.at += ch.len_utf8();
            match ch {
                '"' => return Some(text),
                '\\' => {
                    let escape = *self.text.get(self.at)?;
                    self.at += 1;
                    match escape {
                        b'"' => text.push('"'),
                        b'\\' => text.push('\\'),
                        b'/' => text.push('/'),
                        b'b' => text.push('\x08'),
                        b'f' => text.push('\x0c'),
                        b'n' => text.push('\n'),
                        b'r' => text.push('\r'),
                        b't' => text.push('\t'),
                        b'u' => {
                            let unit = self.hex4()?;
                            let code = if (0xd800..0xdc00).contains(&unit)
                                && self.text[self.at..].starts_with(b"\\u")
                            {
                                let mark = self.at;
                                self.at += 2;
                                match self.hex4() {
                                    Some(low) if (0xdc00..0xe000).contains(&low) => {
                                        0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00)
                                    }
                                    _ => {
                                        self.at = mark;
                                        unit
                                    }
                                }
                            } else {
                                unit
                            };
                            text.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                        }
                        _ => return None,
                    }
                }
                ch if (ch as u32) < 0x20 => return None,
                ch => text.push(ch),
            }
        }
    }
}

/// Python `float.__repr__`: the shortest round-trip digits, positional for decimal exponents
/// -4..16 and otherwise `d.ddde±XX`.
fn float_repr(number: f64) -> String {
    if number.is_nan() {
        return "nan".into();
    }
    if number.is_infinite() {
        return if number > 0.0 { "inf" } else { "-inf" }.into();
    }
    let scientific = format!("{number:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("exponent form");
    let exponent: i32 = exponent.parse().expect("exponent digits");
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    if (-4..16).contains(&exponent) {
        let point = exponent + 1;
        let body = if point <= 0 {
            format!("0.{}{digits}", "0".repeat((-point) as usize))
        } else if (point as usize) >= digits.len() {
            format!("{digits}{}.0", "0".repeat(point as usize - digits.len()))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        format!("{sign}{body}")
    } else {
        let fraction = if digits.len() > 1 {
            format!("{}.{}", &digits[..1], &digits[1..])
        } else {
            digits
        };
        format!(
            "{sign}{fraction}e{}{:02}",
            if exponent < 0 { '-' } else { '+' },
            exponent.abs()
        )
    }
}

/// Python `str.isprintable` for one character: not a control, format, separator (other than
/// the ASCII space), surrogate, private-use or noncharacter code point.
fn printable(ch: char) -> bool {
    let code = ch as u32;
    if ch == ' ' {
        return true;
    }
    if ch.is_control() || ch.is_whitespace() {
        return false;
    }
    !matches!(code,
        0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x890..=0x891 | 0x8e2 | 0x180e
        | 0x200b..=0x200f | 0x202a..=0x202e | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff
        | 0xfff9..=0xfffb | 0x110bd | 0x110cd | 0x13430..=0x1343f | 0x1bca0..=0x1bca3
        | 0x1d173..=0x1d17a | 0xe0001 | 0xe0020..=0xe007f
        | 0xe000..=0xf8ff | 0xf0000..=0xffffd | 0x100000..=0x10fffd
        | 0xfdd0..=0xfdef)
        && code & 0xfffe != 0xfffe
}

/// Python `repr(str)`.
fn str_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            ch if ch == quote => {
                out.push('\\');
                out.push(ch);
            }
            ch if printable(ch) => out.push(ch),
            ch if (ch as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", ch as u32)),
            ch if (ch as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => out.push_str(&format!("\\U{:08x}", ch as u32)),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn values_print_like_python() {
        let value = loads(
            r#"{"b": [1, 2.5, "it's", null, true], "a": {"x": NaN, "y": -Infinity}, "b": 1e16}"#,
        )
        .expect("python accepts NaN and repeated keys");
        assert_eq!(value.repr(), "{'b': 1e+16, 'a': {'x': nan, 'y': -inf}}");
        assert_eq!(
            loads(r#"[123456789012345678901234567890, -0, 0.0001, 1e-05, 100.0, "a\"b'c\u0001"]"#)
                .expect("list")
                .repr(),
            r#"[123456789012345678901234567890, 0, 0.0001, 1e-05, 100.0, 'a"b\'c\x01']"#
        );
        assert_eq!(loads("1.5e300").expect("float").repr(), "1.5e+300");
        assert_eq!(loads("1e999").expect("overflow").repr(), "inf");
        assert!(loads("{\"a\":1,}").is_none());
        assert!(loads("[01]").is_none());
        assert!(loads("\"tab\there\"").is_none());
        assert!(loads(" {} x").is_none());
        assert!(Py::Float(f64::NAN).truthy());
        assert!(!Py::Int("0".into()).truthy());
    }
}

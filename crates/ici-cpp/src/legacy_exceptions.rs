//! Frozen `_exception_rules` C++ line heuristics for the legacy `ici exception` projection.
//!
//! The legacy CLI reports one target per source line exactly like the frozen pyz: literals and
//! comments are blanked, then destructor bodies are tracked by brace depth and every line inside
//! one that spells `throw` is reported (including lambdas and nested local classes), and every
//! `catch (...)` whose text up to the first `}` is blank is a swallowed catch-all. `\s` also
//! covers the ASCII separators U+001C..U+001F that Python treats as whitespace.
use regex::Regex;
use std::sync::LazyLock;

static DESTRUCTOR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"~[\s\x1c-\x1f]*[A-Za-z_]\w*[\s\x1c-\x1f]*\([^)]*\)[^{;]*\{").unwrap()
});
static DECLARATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"~[\s\x1c-\x1f]*[A-Za-z_]\w*[\s\x1c-\x1f]*\([^)]*\)[^;{]*$").unwrap()
});
static THROW: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bthrow\b").unwrap());
static CATCH_ALL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)catch[\s\x1c-\x1f]*\([\s\x1c-\x1f]*\.\.\.[\s\x1c-\x1f]*\)[\s\x1c-\x1f]*\{(?P<body>.*?)\}").unwrap()
});

/// Python `str.isspace`, which `str.strip()` uses.
fn python_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

/// Python `str.splitlines()` without line ends.
fn python_lines(text: &str) -> Vec<&str> {
    let mut lines = vec![];
    let mut start = 0;
    let mut characters = text.char_indices().peekable();
    while let Some((index, character)) = characters.next() {
        if matches!(
            character,
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
            lines.push(&text[start..index]);
            let mut end = index + character.len_utf8();
            if character == '\r' && characters.peek().is_some_and(|(_, next)| *next == '\n') {
                characters.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

#[derive(Clone, Copy, PartialEq)]
enum State {
    Code,
    Line,
    LineSplice,
    Block,
    Quote(char),
}

fn raw_string_end(content: &[char], index: usize) -> Option<usize> {
    if content.get(index..index + 2) != Some(&['R', '"']) {
        return None;
    }
    let open = (index + 2..content.len()).find(|position| content[*position] == '(')?;
    let delimiter = &content[index + 2..open];
    if delimiter.len() > 16
        || delimiter
            .iter()
            .any(|character| python_space(*character) || matches!(character, '\\' | '(' | ')'))
    {
        return None;
    }
    let mut marker = vec![')'];
    marker.extend_from_slice(delimiter);
    marker.push('"');
    Some(
        (open + 1..content.len())
            .find(|start| content[*start..].starts_with(&marker))
            .map_or(content.len(), |start| start + marker.len()),
    )
}

fn blank_escape(characters: &mut [char], index: usize) -> usize {
    characters[index] = ' ';
    if index + 1 < characters.len() && characters[index + 1] != '\n' {
        characters[index + 1] = ' ';
        return index + 2;
    }
    index + 1
}

/// Frozen `_mask_cpp_literals`: blank comments, string/char literals and raw strings.
pub(crate) fn mask_literals(content: &str) -> String {
    let original: Vec<char> = content.chars().collect();
    let mut characters = original.clone();
    let mut index = 0;
    let mut state = State::Code;
    while index < characters.len() {
        (index, state) = match state {
            State::Code => {
                let following = characters.get(index + 1).copied();
                if let Some(end) = raw_string_end(&original, index) {
                    for character in &mut characters[index..end] {
                        if *character != '\n' {
                            *character = ' ';
                        }
                    }
                    (end, State::Code)
                } else if characters[index] == '/' && following == Some('/') {
                    characters[index] = ' ';
                    characters[index + 1] = ' ';
                    (index + 2, State::Line)
                } else if characters[index] == '/' && following == Some('*') {
                    characters[index] = ' ';
                    characters[index + 1] = ' ';
                    (index + 2, State::Block)
                } else if matches!(characters[index], '"' | '\'') {
                    let quote = characters[index];
                    characters[index] = ' ';
                    (index + 1, State::Quote(quote))
                } else if characters[index] == '\\' {
                    (blank_escape(&mut characters, index), State::Code)
                } else {
                    (index + 1, State::Code)
                }
            }
            State::Line | State::LineSplice
                if state == State::LineSplice
                    && (characters[index] == '\n'
                        || (characters[index] == '\r'
                            && characters.get(index + 1) == Some(&'\n'))) =>
            {
                let step = if characters[index] == '\r' { 2 } else { 1 };
                (index + step, State::Line)
            }
            State::Line | State::LineSplice => {
                let following = characters.get(index + 1).copied();
                if characters[index] == '\\'
                    && (following == Some('\n')
                        || (following == Some('\r') && characters.get(index + 2) == Some(&'\n')))
                {
                    characters[index] = ' ';
                    (index + 1, State::LineSplice)
                } else if characters[index] == '\n' {
                    (index + 1, State::Code)
                } else {
                    characters[index] = ' ';
                    (index + 1, State::Line)
                }
            }
            State::Block => {
                if characters[index] == '*' && characters.get(index + 1) == Some(&'/') {
                    characters[index] = ' ';
                    characters[index + 1] = ' ';
                    (index + 2, State::Code)
                } else {
                    if characters[index] != '\n' {
                        characters[index] = ' ';
                    }
                    (index + 1, State::Block)
                }
            }
            State::Quote(quote) => {
                if characters[index] == '\\' {
                    (blank_escape(&mut characters, index), state)
                } else if characters[index] == quote {
                    (index + 1, State::Code)
                } else {
                    if characters[index] != '\n' {
                        characters[index] = ' ';
                    }
                    (index + 1, state)
                }
            }
        };
    }
    characters.into_iter().collect()
}

fn count(line: &str, character: char) -> isize {
    line.chars().filter(|value| *value == character).count() as isize
}

/// Frozen `_destructor_throw_lines` over masked text (1-based line numbers).
pub(crate) fn destructor_throw_lines(masked: &str) -> Vec<usize> {
    let mut depth = 0isize;
    let mut active: Vec<isize> = vec![];
    let mut found = vec![];
    let mut pending = false;
    for (index, line) in python_lines(masked).into_iter().enumerate() {
        if DESTRUCTOR.is_match(line) {
            active.push(depth);
            pending = false;
        } else if pending && line.contains(';') {
            pending = false;
        } else if pending && line.contains('{') {
            active.push(depth);
            pending = false;
        } else if DECLARATION.is_match(line.trim_matches(python_space)) {
            pending = true;
        }
        if !active.is_empty() && THROW.is_match(line) {
            found.push(index + 1);
        }
        depth += count(line, '{') - count(line, '}');
        active.retain(|start| depth > *start);
    }
    found
}

/// Frozen `_empty_catch_all_lines` over masked text (1-based line numbers).
pub(crate) fn empty_catch_all_lines(masked: &str) -> Vec<usize> {
    CATCH_ALL
        .captures_iter(masked)
        .filter(|captures| captures["body"].trim_matches(python_space).is_empty())
        .map(|captures| {
            masked[..captures.get(0).unwrap().start()]
                .matches('\n')
                .count()
                + 1
        })
        .collect()
}

//! Frozen console of a legacy single-engine command (`__main__._print_engine_result`, the line
//! distribution chart and `_save_single_report`), printed through rich 15.0.0 at width 80 when
//! stdout is not a terminal. The saved result stays complete; only the console is this short.
use serde_json::Value;
use unicode_width::UnicodeWidthChar;

const WIDTH: usize = 80;

fn cells(text: &str) -> usize {
    text.chars()
        .map(|character| UnicodeWidthChar::width(character).unwrap_or(0))
        .sum()
}

/// rich `_wrap.words`: each word with its leading and trailing whitespace (`\s*\S+\s*`).
fn words(text: &str) -> Vec<(usize, &str)> {
    let mut out = vec![];
    let bytes: Vec<(usize, char)> = text.char_indices().collect();
    let mut index = 0;
    while index < bytes.len() {
        let start = index;
        while index < bytes.len() && bytes[index].1.is_whitespace() {
            index += 1;
        }
        if index == bytes.len() {
            break;
        }
        while index < bytes.len() && !bytes[index].1.is_whitespace() {
            index += 1;
        }
        while index < bytes.len() && bytes[index].1.is_whitespace() {
            index += 1;
        }
        let begin = bytes[start].0;
        let end = bytes.get(index).map_or(text.len(), |(offset, _)| *offset);
        out.push((begin, &text[begin..end]));
    }
    out
}

/// rich `cells.chop_cells`.
fn chop_cells(text: &str, width: usize) -> Vec<String> {
    let mut lines = vec![String::new()];
    let mut total = 0;
    for character in text.chars() {
        let size = UnicodeWidthChar::width(character).unwrap_or(0);
        if total + size > width {
            lines.push(character.to_string());
            total = size;
        } else {
            lines.last_mut().expect("line").push(character);
            total += size;
        }
    }
    lines
}

/// rich `_wrap.divide_line` (fold) as byte offsets.
fn divide_line(text: &str, width: usize) -> Vec<usize> {
    let mut breaks = vec![];
    let mut offset = 0;
    for (start, word) in words(text) {
        let length = cells(word.trim_end());
        if width.saturating_sub(offset) >= length && width >= offset {
            offset += cells(word);
        } else if length > width {
            let chunks = chop_cells(word, width);
            let mut start = start;
            let last = chunks.len() - 1;
            for (index, chunk) in chunks.iter().enumerate() {
                if start > 0 {
                    breaks.push(start);
                }
                if index == last {
                    offset = cells(chunk);
                } else {
                    start += chunk.len();
                }
            }
        } else if offset > 0 && start > 0 {
            breaks.push(start);
            offset = cells(word);
        }
    }
    breaks
}

/// rich `Text.wrap` at the non-terminal console width: divide, then `rstrip_end` (only the
/// trailing whitespace beyond the width is removed). Every printed line ends with a newline.
pub(crate) fn rich_print(text: &str) -> String {
    let mut out = String::new();
    for line in text.split('\n') {
        let mut pieces = vec![];
        let mut previous = 0;
        for at in divide_line(line, WIDTH) {
            pieces.push(&line[previous..at]);
            previous = at;
        }
        pieces.push(&line[previous..]);
        for piece in pieces {
            let length = piece.chars().count();
            let mut piece = piece.to_owned();
            if length > WIDTH {
                let trailing = piece
                    .chars()
                    .rev()
                    .take_while(|c| c.is_whitespace())
                    .count();
                for _ in 0..trailing.min(length - WIDTH) {
                    piece.pop();
                }
            }
            out.push_str(&piece);
            out.push('\n');
        }
    }
    out
}

fn thousands(value: u64) -> String {
    let digits = value.to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

/// Frozen `print_line_distribution_chart`.
fn line_chart(extra: &Value) -> String {
    let count = |key: &str| extra[key].as_u64().unwrap_or(0);
    let (code, comment, blank, total) = (
        count("code"),
        count("comment"),
        count("blank"),
        count("total"),
    );
    let scale = 50.0 / (if total > 0 { total } else { 1 }) as f64;
    let share = |value: u64| value as f64 / (if total > 0 { total } else { 1 }) as f64 * 100.0;
    let bar = |symbol: &str, value: u64| symbol.repeat((value as f64 * scale) as usize);
    let row = |label: &str, symbol: &str, value: u64| {
        let bar = bar(symbol, value);
        let padding = 50usize.saturating_sub(bar.chars().count());
        format!(
            " {label}[{bar}{}] {value:>6} ({:.1}%)",
            " ".repeat(padding),
            share(value)
        )
    };
    let mut out = rich_print(&row("Code Lines    ", "█", code));
    out.push_str(&rich_print(&row("Comment Lines ", "░", comment)));
    out.push_str(&rich_print(&(row("Blank Lines   ", "▒", blank) + "\n")));
    out.push_str(&rich_print(&format!(
        " Total Volume: {} Lines",
        thousands(total)
    )));
    out
}

/// The frozen console of one legacy engine command. `report` is the report file name printed
/// after `--report` saved it.
pub fn legacy_engine_console(engine: &Value, report: Option<&str>) -> String {
    let icon = match engine["status"].as_str() {
        Some("PASS") => "✔",
        Some("WARN") => "⚠",
        Some("FAIL" | "ERROR") => "✘",
        _ => "↷",
    };
    let summary = engine["summary"].as_str().unwrap_or("");
    let mut out = rich_print(&format!("{icon} {summary}"));
    if engine["engine_name"] == "line" {
        out.push_str(&line_chart(&engine["extra"]));
    }
    if let Some(report) = report {
        out.push_str(&rich_print(&format!("Report saved to: {report}")));
    }
    out
}

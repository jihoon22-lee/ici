//! Tool replacements retain native exclusive bounds; conversion requires the immutable source.
use crate::{ProviderError, ProviderPlan, coordinates::ColumnKind, plan::relative_location};
use serde_json::{Value, json};

pub(super) fn replacement(
    plan: &ProviderPlan,
    path: &str,
    start: (u32, u32),
    end: (u32, u32),
    text: &str,
    exclusive: bool,
) -> Result<Value, ProviderError> {
    if start.0 == 0
        || start.1 == 0
        || end.0 == 0
        || end.1 == 0
        || end < start
        || text.chars().count() > 8192
        || text.contains('\0')
    {
        return Err(ProviderError(
            "invalid or oversized C++ fix-it range/replacement".into(),
        ));
    }
    let path = relative_location(path, &plan.context).unwrap_or_else(|_| "[external]".into());
    let mut value = json!({"path":path,"start_line":start.0,"start_column":start.1,"end_line":end.0,"end_column":end.1,"replacement":text,"column_kind":"unknown","end_exclusive":if exclusive{Value::Bool(true)}else{Value::Null}});
    let kind = match plan.context.compiler_columns.as_deref() {
        Some("gnu-byte" | "clang-byte") => ColumnKind::Utf8,
        _ => ColumnKind::Unknown,
    };
    if kind == ColumnKind::Unknown || path == "[external]" {
        return Ok(value);
    }
    let bom = plan.context.compiler_columns.as_deref() == Some("clang-byte");
    let columns = [start, end].map(|(line, column)| {
        plan.context
            .source_coordinates
            .boundary(&path, line, column - 1, kind, bom)
    });
    match columns {
        [Ok(Some(start)), Ok(Some(end))] => {
            value["start_column"] = json!(start + 1);
            value["end_column"] = json!(end + 1);
            value["column_kind"] = json!("unicodeCodePoints");
        }
        [Err(error), _] | [_, Err(error)] => return Err(error),
        _ => {}
    }
    Ok(value)
}

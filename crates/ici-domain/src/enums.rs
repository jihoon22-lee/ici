use serde::{Deserialize, Serialize};

macro_rules! axis {
    ($name:ident, $case:literal, $default:ident, $($variant:ident),+ $(,)?) => {
        #[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Ord, PartialOrd, Serialize, Deserialize)]
        #[serde(rename_all = $case)]
        pub enum $name { #[default] $default, $($variant),+ }
    };
}
axis!(ScopeKind, "lowercase", Full, Partial, Standalone);
axis!(
    GateVerdict,
    "SCREAMING_SNAKE_CASE",
    NotEvaluated,
    Pass,
    Fail,
    Incomplete
);
axis!(
    CheckExecution,
    "SCREAMING_SNAKE_CASE",
    NotSelected,
    Completed,
    Error,
    Blocked,
    Cancelled,
    NotApplicable
);
axis!(
    EvidenceLevel,
    "SCREAMING_SNAKE_CASE",
    NotRun,
    Measured,
    Estimated,
    NotApplicable
);
axis!(Profile, "lowercase", Standard, Fast, Deep);
axis!(
    TaskKind,
    "lowercase",
    Analyze,
    Probe,
    Prepare,
    Test,
    Collect
);
axis!(
    TaskState,
    "SCREAMING_SNAKE_CASE",
    Ready,
    Running,
    Succeeded,
    Failed,
    Blocked,
    Cancelled
);
axis!(
    PublicationState,
    "SCREAMING_SNAKE_CASE",
    NotConfigured,
    Success,
    Failed
);
axis!(BaselineState, "lowercase", Incompatible, Comparable);

impl GateVerdict {
    pub fn exit_code(self) -> i32 {
        match self {
            Self::Incomplete => 3,
            Self::Fail => 1,
            _ => 0,
        }
    }
}

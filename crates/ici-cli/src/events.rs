use chrono::{SecondsFormat, Utc};
use ici_domain::{EVENT_SCHEMA_ID, EVENT_SCHEMA_VERSION, RunEvent};
use ici_execution::storage::{WriteMode, append_events, atomic_write};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

#[derive(Clone, Default)]
pub struct EventLog(Arc<Mutex<State>>);
#[derive(Default)]
struct State {
    sink: Option<Sink>,
    failure: Option<String>,
}
struct Sink {
    path: PathBuf,
    run_id: String,
    sequence: u64,
}
impl EventLog {
    pub fn open(path: Option<&Path>, run_id: &str) -> std::io::Result<Self> {
        let sink = if let Some(path) = path {
            atomic_write(path, b"", WriteMode::CreateNew)?;
            Some(Sink {
                path: path.to_owned(),
                run_id: run_id.into(),
                sequence: 0,
            })
        } else {
            None
        };
        Ok(Self(Arc::new(Mutex::new(State {
            sink,
            failure: None,
        }))))
    }
    /// A failed stream keeps its valid prefix. Later emissions do not invent a completion record.
    pub fn emit(&self, kind: &str, task_id: Option<&str>, component: Option<&str>, message: &str) {
        let Ok(mut state) = self.0.lock() else {
            return;
        };
        if state.failure.is_some() {
            return;
        }
        let Some(sink) = &mut state.sink else {
            return;
        };
        let Some(sequence) = sink.sequence.checked_add(1) else {
            state.failure = Some("event sequence exhausted".into());
            return;
        };
        let event = RunEvent {
            schema_id: EVENT_SCHEMA_ID.into(),
            schema_version: EVENT_SCHEMA_VERSION,
            run_id: sink.run_id.clone(),
            seq: sequence,
            event_type: kind.into(),
            timestamp: Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
            task_id: task_id.map(str::to_owned),
            // Workspace-level tasks carry no component; an empty id is absence, not identity.
            component_id: component.filter(|id| !id.is_empty()).map(str::to_owned),
            message: message.chars().take(4096).collect(),
        };
        match append_events(&sink.path, &[event]) {
            Ok(()) => sink.sequence = sequence,
            Err(error) => state.failure = Some(format!("event output failed: {error}")),
        }
    }
    pub fn failure(&self) -> Option<String> {
        self.0.lock().ok().and_then(|state| state.failure.clone())
    }
}
pub struct SignalGuard {
    ids: Vec<signal_hook::SigId>,
}
impl SignalGuard {
    pub fn install(cancellation: &ici_execution::Cancellation) -> std::io::Result<Self> {
        let mut guard = Self { ids: vec![] };
        for signal in [signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM] {
            guard
                .ids
                .push(signal_hook::flag::register(signal, cancellation.flag())?);
        }
        Ok(guard)
    }
}
impl Drop for SignalGuard {
    fn drop(&mut self) {
        for id in self.ids.drain(..) {
            signal_hook::low_level::unregister(id);
        }
    }
}

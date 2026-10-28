//! Development failure-injection measurement; no product dependency on this executable.
use ici_analysis::AnalysisSession;
use ici_domain::{AnalysisRequest, SourceInput, TaskState};
use serde_json::json;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};
fn main() {
    let mut large = vec![b'x'; 16 * 1024 * 1024];
    large[0] = b'#';
    let request = AnalysisRequest {
        task_id: "cancel-probe".into(),
        check_id: "python.line".into(),
        component_id: "project".into(),
        language: "python".into(),
        inputs: vec![
            SourceInput {
                path: "first.py".into(),
                bytes: b"x=1\n".to_vec(),
            },
            SourceInput {
                path: "large.py".into(),
                bytes: large,
            },
            SourceInput {
                path: "last.py".into(),
                bytes: b"x=2\n".to_vec(),
            },
        ],
        options: json!({}),
        target_version: "3.14".into(),
    };
    let flag = Arc::new(AtomicBool::new(false));
    let sender = Arc::clone(&flag);
    let thread = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(20));
        sender.store(true, Ordering::Relaxed);
    });
    let start = Instant::now();
    let output = AnalysisSession::new().analyze_cancellable(&request, &flag);
    let elapsed = start.elapsed();
    thread.join().unwrap();
    assert_eq!(output.observation.state, TaskState::Cancelled);
    assert!(
        output
            .targets
            .iter()
            .any(|target| target.file_path == "first.py" && target.status == "PASS")
    );
    assert!(
        !output
            .targets
            .iter()
            .any(|target| target.file_path == "last.py")
    );
    assert!(elapsed < Duration::from_secs(2));
    println!(
        "{}",
        json!({"state":output.observation.state,"elapsed_ms":elapsed.as_millis(),"retained_targets":output.targets.len(),"retained_files":output.targets.iter().map(|target|target.file_path.clone()).collect::<Vec<_>>(),"source_bytes":16*1024*1024,"profile":"debug","scope":"line decoder cancellation; parser/rule stress is separate"})
    );
}

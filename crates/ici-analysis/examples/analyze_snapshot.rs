//! Development replay harness, never a Python backend.
use ici_analysis::AnalysisSession;
use ici_domain::AnalysisRequest;
use std::io::{self, Read};
fn main() {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("snapshot JSON input");
    let requests: Vec<AnalysisRequest> = serde_json::from_str(&input).expect("requests");
    let mut session = AnalysisSession::new();
    let outputs: Vec<_> = requests
        .iter()
        .map(|request| session.analyze(request))
        .collect();
    println!(
        "{}",
        serde_json::to_string_pretty(&outputs).expect("output JSON")
    );
}

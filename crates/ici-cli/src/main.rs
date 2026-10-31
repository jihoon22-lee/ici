fn main() {
    let arguments: Vec<_> = std::env::args_os().collect();
    if let Some(mode) = arguments.get(1).and_then(|value| value.to_str()) {
        let code = match mode {
            "__qmake-record" => Some(ici_cpp::recorder_main(&arguments[2..])),
            "__qmake-configure-capture" => {
                Some(ici_cpp::qmake_configure_capture_main(&arguments[2..]))
            }
            _ => None,
        };
        if let Some(code) = code {
            std::process::exit(code);
        }
    }
    let invocation = match ici_cli::registry::parse(arguments) {
        Ok(invocation) => invocation,
        Err(error) => error.exit(),
    };
    let cwd = match std::env::current_dir() {
        Ok(cwd) => cwd,
        Err(error) => {
            eprintln!("ici: {error}");
            std::process::exit(2);
        }
    };
    let environment = std::env::vars().collect();
    match ici_cli::commands::dispatch(&invocation, &cwd, &environment) {
        Ok(output) => {
            print!("{}", output.stdout);
            eprint!("{}", output.stderr);
            std::process::exit(output.exit_code);
        }
        Err(error) => {
            eprintln!(
                "ici: {}",
                ici_execution::redaction::redact_text(&error.to_string())
            );
            std::process::exit(2);
        }
    }
}

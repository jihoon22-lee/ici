fn main() {
    let invocation = match ici_cli::registry::parse(std::env::args_os()) {
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

fn main() {
    let is_release = std::env::var("PROFILE").as_deref() == Ok("release");
    if is_release {
        let python_runtime = if cfg!(windows) {
            "resources/python/python.exe"
        } else {
            "resources/python/bin/python"
        };
        let required = [python_runtime, "resources/backend/app/main.py"];
        let missing: Vec<&str> = required
            .into_iter()
            .filter(|path| !std::path::Path::new(path).is_file())
            .collect();
        if !missing.is_empty() {
            panic!(
                "desktop release resources are incomplete: {}. Stage the standalone Python runtime and backend before packaging",
                missing.join(", ")
            );
        }
    } else {
        // A clean source clone intentionally does not contain the bundled
        // Python runtime or staged backend. Tauri still validates every
        // configured resource path while compiling tests and debug builds, so
        // create the ignored directory skeleton before tauri_build reads the
        // bundle configuration. Release builds remain strict above.
        for directory in ["resources/python", "resources/backend"] {
            std::fs::create_dir_all(directory).unwrap_or_else(|error| {
                panic!("failed to create debug resource directory {directory}: {error}")
            });
        }
    }
    tauri_build::build()
}

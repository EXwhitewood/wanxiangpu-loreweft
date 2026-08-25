mod network_proxy;
mod storage;

use storage::{DesktopStorageSettings, DesktopStorageUpdate};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    // The updater is intentionally registered even when no endpoint is
    // configured in a development build. The frontend then degrades to a
    // clear "update source not configured" state instead of failing to boot.
    builder = builder.plugin(tauri_plugin_updater::Builder::new().build());

    // Register this first. Otherwise rapid double-clicks can start several
    // embedded backends at once and whichever process loses the port race
    // appears to the user as a random application crash.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }

    builder = builder.invoke_handler(tauri::generate_handler![
        backend_process_status,
        backend_port,
        desktop_app_version,
        desktop_system_proxy,
        get_desktop_storage_settings,
        select_desktop_storage_directory,
        update_desktop_storage_settings,
        open_desktop_storage_directory,
        restart_desktop_app,
        prepare_desktop_update,
        record_desktop_update_event,
    ]);

    let app = builder
        .setup(|app| {
            start_backend(app)?;
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
            }
            Ok(())
        })
        .build(tauri::generate_context!());

    let app = match app {
        Ok(app) => app,
        Err(error) => {
            // The release binary has no console. start_backend records the
            // actionable detail in startup.log before the host exits.
            eprintln!("Loreweft startup failed: {error}");
            return;
        }
    };

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app_handle.state::<BackendProcess>().terminate();
        }
    });
}

fn start_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use std::net::{SocketAddr, TcpStream};
    use std::process::{Command, Stdio};
    use std::time::Duration;

    const BACKEND_PORT: u16 = 18000;
    const BACKEND_PORT_MAX: u16 = 18010;
    let backend_addr = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));

    let default_app_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    std::fs::create_dir_all(&default_app_dir)?;
    append_startup_log(&default_app_dir, "desktop host launch started");

    let active_storage = storage::prepare_for_launch(&default_app_dir)?;
    let app_dir = active_storage.creative_data_dir;
    let cache_dir = active_storage.cache_dir;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("failed to resolve resource dir: {error}"))?;
    let python_exe = if cfg!(windows) {
        resource_dir.join("python").join("python.exe")
    } else {
        resource_dir.join("python").join("bin").join("python")
    };
    let pid_path = cache_dir.join("backend.pid");
    std::fs::create_dir_all(&app_dir)?;
    std::fs::create_dir_all(&cache_dir)?;
    let port_path = cache_dir.join("backend.port");
    let previous_backend_port = read_backend_port(&port_path);
    append_startup_log(
        &cache_dir,
        &format!(
            "storage ready; creative_data_dir={} cache_dir={}",
            app_dir.display(),
            cache_dir.display()
        ),
    );

    // Prefer a previously selected fallback port so an abnormal UI exit does
    // not leave a healthy backend orphaned while a new process starts on a
    // different port.
    if let Some((port, process_id)) = previous_backend_port.and_then(|port| {
        verified_backend_process_id(SocketAddr::from(([127, 0, 0, 1], port)), &python_exe)
            .map(|process_id| (port, process_id))
    }) {
        append_startup_log(
            &cache_dir,
            &format!("reusing verified local backend pid={process_id} on port {port}"),
        );
        app.manage(BackendProcess::external(
            Some(pid_path.clone()),
            Some(port_path.clone()),
            process_id,
            python_exe.clone(),
            port,
        ));
        return Ok(());
    }

    if previous_backend_port != Some(BACKEND_PORT) {
        if let Some(process_id) = verified_backend_process_id(backend_addr, &python_exe) {
            // A backend left alive after an abnormal UI exit can safely be reused.
            append_startup_log(
                &cache_dir,
                &format!("reusing verified local backend pid={process_id}"),
            );
            app.manage(BackendProcess::external(
                Some(pid_path.clone()),
                Some(port_path.clone()),
                process_id,
                python_exe.clone(),
                BACKEND_PORT,
            ));
            return Ok(());
        }
    }

    if TcpStream::connect_timeout(&backend_addr, Duration::from_millis(250)).is_ok() {
        // Do not fail startup just because another unhealthy process owns the
        // preferred port. A stale runtime, another local app, or a previous
        // development server may legitimately be listening there. We select a
        // loopback fallback port below and report that port to the webview.
        append_startup_log(
            &cache_dir,
            "backend port 18000 is busy and unhealthy; looking for a fallback port",
        );
    }

    let Some(backend_port) = find_available_backend_port(BACKEND_PORT, BACKEND_PORT_MAX) else {
        let message =
            format!("no available local backend port in {BACKEND_PORT}-{BACKEND_PORT_MAX}");
        append_startup_log(&cache_dir, &format!("startup failed: {message}"));
        app.manage(BackendProcess::failed(message, BACKEND_PORT));
        return Ok(());
    };
    append_startup_log(&cache_dir, &format!("selected backend port {backend_port}"));

    let backend_dir = resource_dir.join("backend");
    if !python_exe.is_file() {
        let message = format!(
            "bundled Python runtime is missing: {}",
            python_exe.display()
        );
        append_startup_log(&cache_dir, &format!("startup failed: {message}"));
        app.manage(BackendProcess::failed(message, backend_port));
        return Ok(());
    }
    if !backend_dir.is_dir() {
        let message = format!("bundled backend is missing: {}", backend_dir.display());
        append_startup_log(&cache_dir, &format!("startup failed: {message}"));
        app.manage(BackendProcess::failed(message, backend_port));
        return Ok(());
    }

    let database_path = app_dir.join("loreweft.db");
    let database_path_string = database_path.to_string_lossy().replace('\\', "/");
    let database_url = format!("sqlite+aiosqlite:///{database_path_string}");

    let mut command = Command::new(&python_exe);
    command
        .args([
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            &backend_port.to_string(),
            "--no-access-log",
        ])
        .env("DEBUG", "false")
        .env("APP_RUNTIME", "tauri")
        .env("APP_VERSION", env!("CARGO_PKG_VERSION"))
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("DATA_DIR", &app_dir)
        .env("CACHE_DIR", &cache_dir)
        .env("DATABASE_URL", &database_url)
        .env("SQLITE_PATH", &database_path)
        .env("SQLITE_FTS_PATH", &database_path)
        .current_dir(&backend_dir);

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        // python.exe is a console-subsystem executable. When a GUI Tauri host
        // launches it without this flag, Windows 11 can open a Windows Terminal
        // tab for the embedded backend. Keep the process headless while its
        // stdout/stderr continue to flow into backend.log below.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let backend_log_path = cache_dir.join("backend.log");
    match open_bounded_log(&backend_log_path, 2 * 1024 * 1024)
        .and_then(|log| Ok((log.try_clone()?, log)))
    {
        Ok((stdout, stderr)) => {
            // Persist failures for support. A bounded file avoids both
            // invisible startup exits and unread-pipe deadlocks.
            command
                .stdout(Stdio::from(stdout))
                .stderr(Stdio::from(stderr));
        }
        Err(error) => {
            append_startup_log(
                &cache_dir,
                &format!("could not open backend.log; using null streams: {error}"),
            );
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }

    let process = match command.spawn() {
        Ok(process) => process,
        Err(error) => {
            let message = format!("failed to spawn bundled backend: {error}");
            append_startup_log(&cache_dir, &format!("startup failed: {message}"));
            app.manage(BackendProcess::failed(message, backend_port));
            return Ok(());
        }
    };

    append_startup_log(
        &cache_dir,
        &format!("spawned bundled backend pid={}", process.id()),
    );

    if let Err(error) = std::fs::write(&pid_path, process.id().to_string()) {
        eprintln!("Failed to persist backend PID: {error}");
    }
    if let Err(error) = std::fs::write(&port_path, backend_port.to_string()) {
        eprintln!("Failed to persist backend port: {error}");
    }
    append_startup_log(
        &cache_dir,
        "backend process launched; desktop bootstrap will wait for health",
    );
    app.manage(BackendProcess::owned(
        process,
        pid_path,
        port_path,
        backend_port,
    ));
    Ok(())
}

#[tauri::command]
fn backend_process_status(process: tauri::State<'_, BackendProcess>) -> String {
    process.status()
}

#[tauri::command]
fn backend_port(process: tauri::State<'_, BackendProcess>) -> u16 {
    process.port()
}

#[tauri::command]
fn desktop_app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[tauri::command]
fn desktop_system_proxy() -> Option<String> {
    network_proxy::system_proxy()
}

#[tauri::command]
fn get_desktop_storage_settings(app: tauri::AppHandle) -> Result<DesktopStorageSettings, String> {
    let default_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位默认应用数据目录：{error}"))?;
    storage::read_settings(&default_dir).map_err(|error| error.to_string())
}

#[tauri::command]
async fn select_desktop_storage_directory(kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let title = if kind == "cache" {
            "选择缓存与日志目录"
        } else {
            "选择创作数据目录"
        };
        Ok(rfd::FileDialog::new()
            .set_title(title)
            .pick_folder()
            .map(|path| path.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|error| format!("目录选择器启动失败：{error}"))?
}

#[tauri::command]
fn update_desktop_storage_settings(
    app: tauri::AppHandle,
    settings: DesktopStorageUpdate,
) -> Result<DesktopStorageSettings, String> {
    let default_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位默认应用数据目录：{error}"))?;
    storage::update_settings(&default_dir, settings)
}

#[tauri::command]
fn restart_desktop_app(app: tauri::AppHandle) {
    app.request_restart();
}

#[tauri::command]
async fn prepare_desktop_update(app: tauri::AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || app.state::<BackendProcess>().prepare_for_update())
        .await
        .map_err(|error| format!("无法准备软件更新：后台清理任务异常结束：{error}"))?
}

#[tauri::command]
fn record_desktop_update_event(
    app: tauri::AppHandle,
    stage: String,
    detail: String,
) -> Result<(), String> {
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    let stage = sanitize_update_log_field(&stage, 48);
    if stage.is_empty() {
        return Err("更新日志阶段不能为空".to_string());
    }
    let detail = sanitize_update_log_field(&detail, 512);
    let app_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位更新日志目录：{error}"))?;
    std::fs::create_dir_all(&app_dir).map_err(|error| format!("无法创建更新日志目录：{error}"))?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default();
    let mut log = open_bounded_log(&app_dir.join("updater.log"), 512 * 1024)
        .map_err(|error| format!("无法打开更新日志：{error}"))?;
    writeln!(
        log,
        "{timestamp} pid={} stage={stage} detail={detail}",
        std::process::id()
    )
    .map_err(|error| format!("无法写入更新日志：{error}"))
}

fn sanitize_update_log_field(value: &str, max_chars: usize) -> String {
    value
        .chars()
        .filter(|character| !character.is_control())
        .take(max_chars)
        .collect::<String>()
        .trim()
        .to_string()
}

#[tauri::command]
fn open_desktop_storage_directory(path: String) -> Result<(), String> {
    let directory = std::path::PathBuf::from(path.trim());
    if !directory.is_dir() {
        return Err("目录不存在或当前不可访问".to_string());
    }

    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer.exe");
    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = std::process::Command::new("xdg-open");

    command
        .arg(&directory)
        .spawn()
        .map_err(|error| format!("无法打开目录：{error}"))?;
    Ok(())
}

fn find_available_backend_port(start: u16, end: u16) -> Option<u16> {
    use std::net::{SocketAddr, TcpListener};

    (start..=end).find(|port| TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], *port))).is_ok())
}

fn open_bounded_log(path: &std::path::Path, max_bytes: u64) -> std::io::Result<std::fs::File> {
    use std::fs::OpenOptions;

    let truncate = path
        .metadata()
        .map(|metadata| metadata.len() >= max_bytes)
        .unwrap_or(false);
    let mut options = OpenOptions::new();
    options.create(true).write(true);
    if truncate {
        options.truncate(true);
    } else {
        options.append(true);
    }
    options.open(path)
}

fn append_startup_log(app_dir: &std::path::Path, message: &str) {
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default();
    if let Ok(mut log) = open_bounded_log(&app_dir.join("startup.log"), 512 * 1024) {
        let _ = writeln!(log, "{timestamp} pid={} {message}", std::process::id());
    }
}

#[derive(serde::Deserialize)]
struct BackendHealthIdentity {
    status: String,
    version: String,
    process_id: u32,
}

fn verified_backend_process_id(
    backend_addr: std::net::SocketAddr,
    expected_python_exe: &std::path::Path,
) -> Option<u32> {
    let process_id = fetch_backend_process_id(backend_addr)?;
    process_executable_matches(process_id, expected_python_exe).then_some(process_id)
}

fn fetch_backend_process_id(backend_addr: std::net::SocketAddr) -> Option<u32> {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    TcpStream::connect_timeout(&backend_addr, Duration::from_millis(250))
        .and_then(|mut stream| {
            stream.set_read_timeout(Some(Duration::from_millis(750)))?;
            stream.set_write_timeout(Some(Duration::from_millis(750)))?;
            stream.write_all(
                b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            )?;
            let mut response = String::new();
            stream.read_to_string(&mut response)?;
            parse_backend_process_id(&response)
                .ok_or_else(|| std::io::Error::other("health endpoint identity is not trusted"))
        })
        .ok()
}

fn parse_backend_process_id(response: &str) -> Option<u32> {
    let (headers, body) = response.split_once("\r\n\r\n")?;
    let status_line = headers.lines().next()?;
    if status_line != "HTTP/1.1 200 OK" && status_line != "HTTP/1.0 200 OK" {
        return None;
    }

    let identity: BackendHealthIdentity = serde_json::from_str(body).ok()?;
    if identity.status != "ok"
        || identity.version != env!("CARGO_PKG_VERSION")
        || identity.process_id == 0
    {
        return None;
    }
    Some(identity.process_id)
}

fn process_executable_matches(process_id: u32, expected_executable: &std::path::Path) -> bool {
    let Some(actual_executable) = process_executable_path(process_id) else {
        return false;
    };
    let Ok(actual_executable) = std::fs::canonicalize(actual_executable) else {
        return false;
    };
    let Ok(expected_executable) = std::fs::canonicalize(expected_executable) else {
        return false;
    };

    #[cfg(windows)]
    {
        normalize_windows_path(&actual_executable) == normalize_windows_path(&expected_executable)
    }

    #[cfg(not(windows))]
    {
        actual_executable == expected_executable
    }
}

#[cfg(windows)]
fn normalize_windows_path(path: &std::path::Path) -> String {
    let path = path.to_string_lossy();
    path.strip_prefix(r"\\?\")
        .unwrap_or(path.as_ref())
        .replace('/', "\\")
        .to_lowercase()
}

#[cfg(windows)]
fn process_executable_path(process_id: u32) -> Option<std::path::PathBuf> {
    use std::ffi::{c_void, OsString};
    use std::os::windows::ffi::OsStringExt;

    type Handle = *mut c_void;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(desired_access: u32, inherit_handle: i32, process_id: u32) -> Handle;
        fn QueryFullProcessImageNameW(
            process: Handle,
            flags: u32,
            executable_name: *mut u16,
            size: *mut u32,
        ) -> i32;
        fn CloseHandle(object: Handle) -> i32;
    }

    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id);
        if process.is_null() {
            return None;
        }

        let mut buffer = vec![0_u16; 32_768];
        let mut size = u32::try_from(buffer.len()).ok()?;
        let queried = QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut size);
        let _ = CloseHandle(process);
        if queried == 0 || size == 0 {
            return None;
        }
        buffer.truncate(size as usize);
        Some(std::path::PathBuf::from(OsString::from_wide(&buffer)))
    }
}

#[cfg(target_os = "linux")]
fn process_executable_path(process_id: u32) -> Option<std::path::PathBuf> {
    std::fs::read_link(format!("/proc/{process_id}/exe")).ok()
}

#[cfg(all(not(windows), not(target_os = "linux")))]
fn process_executable_path(_process_id: u32) -> Option<std::path::PathBuf> {
    None
}

pub struct BackendProcess {
    child: std::sync::Mutex<Option<std::process::Child>>,
    pid_path: Option<std::path::PathBuf>,
    port_path: Option<std::path::PathBuf>,
    cleanup_pid: std::sync::Mutex<Option<u32>>,
    external_executable: Option<std::path::PathBuf>,
    lifecycle_operation: std::sync::Mutex<()>,
    port: u16,
    startup_error: Option<String>,
}

impl BackendProcess {
    fn owned(
        child: std::process::Child,
        pid_path: std::path::PathBuf,
        port_path: std::path::PathBuf,
        port: u16,
    ) -> Self {
        let cleanup_pid = child.id();
        Self {
            child: std::sync::Mutex::new(Some(child)),
            cleanup_pid: std::sync::Mutex::new(Some(cleanup_pid)),
            external_executable: None,
            lifecycle_operation: std::sync::Mutex::new(()),
            pid_path: Some(pid_path),
            port_path: Some(port_path),
            port,
            startup_error: None,
        }
    }

    fn external(
        pid_path: Option<std::path::PathBuf>,
        port_path: Option<std::path::PathBuf>,
        cleanup_pid: u32,
        executable: std::path::PathBuf,
        port: u16,
    ) -> Self {
        Self {
            child: std::sync::Mutex::new(None),
            pid_path,
            port_path,
            cleanup_pid: std::sync::Mutex::new(Some(cleanup_pid)),
            external_executable: Some(executable),
            lifecycle_operation: std::sync::Mutex::new(()),
            port,
            startup_error: None,
        }
    }

    fn failed(message: String, port: u16) -> Self {
        Self {
            child: std::sync::Mutex::new(None),
            pid_path: None,
            port_path: None,
            cleanup_pid: std::sync::Mutex::new(None),
            external_executable: None,
            lifecycle_operation: std::sync::Mutex::new(()),
            port,
            startup_error: Some(message),
        }
    }

    fn port(&self) -> u16 {
        self.port
    }

    fn status(&self) -> String {
        if let Some(error) = &self.startup_error {
            return format!("failed:{error}");
        }
        let Ok(mut guard) = self.child.lock() else {
            return "failed:backend process state lock was poisoned".to_string();
        };
        let Some(child) = guard.as_mut() else {
            return "external".to_string();
        };
        match child.try_wait() {
            Ok(None) => "running".to_string(),
            Ok(Some(status)) => format!("exited:{status}"),
            Err(error) => format!("failed:could not inspect backend process: {error}"),
        }
    }

    fn terminate(&self) {
        // Keep the PID and marker files when shutdown fails. A later Exit
        // event, Drop, or next desktop launch can then retry the same backend
        // instead of losing the only identity capable of releasing its files.
        let _ = self.shutdown_backend_with(
            std::time::Duration::from_secs(5),
            Self::request_process_termination,
        );
    }

    fn prepare_for_update(&self) -> Result<(), String> {
        self.prepare_for_update_with(
            std::time::Duration::from_secs(15),
            Self::request_process_termination,
        )
    }

    fn prepare_for_update_with<F>(
        &self,
        timeout: std::time::Duration,
        request_termination: F,
    ) -> Result<(), String>
    where
        F: FnOnce(&Self, u32),
    {
        let log_dir = self
            .port_path
            .as_deref()
            .or(self.pid_path.as_deref())
            .and_then(std::path::Path::parent)
            .map(std::path::Path::to_path_buf);
        if let Some(log_dir) = &log_dir {
            append_startup_log(
                log_dir,
                &format!(
                    "desktop update preparation started; stopping backend on port {}",
                    self.port
                ),
            );
        }

        if self.shutdown_backend_with(timeout, request_termination) {
            if let Some(log_dir) = &log_dir {
                append_startup_log(
                    log_dir,
                    &format!(
                        "desktop update preparation completed; backend port {} released",
                        self.port
                    ),
                );
            }
            Ok(())
        } else {
            let timeout_seconds = timeout.as_secs().max(1);
            let message = format!(
                "无法安全关闭本地后台：进程或端口 {} 在 {} 秒后仍未释放。更新安装尚未开始，请重试；若问题持续，请重启万象谱。",
                self.port, timeout_seconds
            );
            if let Some(log_dir) = &log_dir {
                append_startup_log(
                    log_dir,
                    &format!("desktop update preparation failed: {message}"),
                );
            }
            Err(message)
        }
    }

    fn shutdown_backend_with<F>(&self, timeout: std::time::Duration, request_termination: F) -> bool
    where
        F: FnOnce(&Self, u32),
    {
        // prepare/update, Exit and Drop can otherwise race and send multiple
        // PID-based termination requests. Serialize the complete identity
        // check, termination, verification and marker-consumption sequence.
        let Ok(_operation) = self.lifecycle_operation.lock() else {
            return false;
        };
        let Ok(tracked_pid) = self.tracked_pid() else {
            return false;
        };
        if let Some(pid) = tracked_pid {
            if !self.process_released(pid) {
                if !self.termination_identity_is_current(pid) {
                    return false;
                }
                request_termination(self, pid);
            }
        }

        if !self.wait_for_process_and_port_release(tracked_pid, timeout) {
            return false;
        }

        self.consume_released_runtime_identity(tracked_pid);
        true
    }

    fn tracked_pid(&self) -> Result<Option<u32>, ()> {
        let guard = self.child.lock().map_err(|_| ())?;
        if let Some(child) = guard.as_ref() {
            return Ok(Some(child.id()));
        }
        drop(guard);
        self.cleanup_pid.lock().map(|pid| *pid).map_err(|_| ())
    }

    fn request_process_termination(&self, pid: u32) {
        terminate_process_tree(pid);

        // taskkill handles the full Windows process tree. Child::kill remains
        // a direct-child fallback and also provides the Unix implementation
        // with a standard-library-owned process handle.
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.as_mut().filter(|child| child.id() == pid) {
                if child.try_wait().ok().flatten().is_none() {
                    let _ = child.kill();
                }
            }
        }
    }

    fn termination_identity_is_current(&self, pid: u32) -> bool {
        if let Ok(guard) = self.child.lock() {
            if guard.as_ref().is_some_and(|child| child.id() == pid) {
                return true;
            }
        }

        let Some(executable) = self.external_executable.as_deref() else {
            return false;
        };
        verified_backend_process_id(
            std::net::SocketAddr::from(([127, 0, 0, 1], self.port)),
            executable,
        ) == Some(pid)
    }

    fn process_released(&self, pid: u32) -> bool {
        let Ok(mut guard) = self.child.lock() else {
            return false;
        };
        if let Some(child) = guard.as_mut().filter(|child| child.id() == pid) {
            return matches!(child.try_wait(), Ok(Some(_)));
        }
        drop(guard);
        !process_is_running(pid)
    }

    fn wait_for_process_and_port_release(
        &self,
        tracked_pid: Option<u32>,
        timeout: std::time::Duration,
    ) -> bool {
        use std::time::{Duration, Instant};

        let deadline = Instant::now() + timeout;
        loop {
            let process_released = tracked_pid
                .map(|pid| self.process_released(pid))
                .unwrap_or(true);
            if process_released && backend_port_is_released(self.port) {
                return true;
            }
            if Instant::now() >= deadline {
                return false;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    fn consume_released_runtime_identity(&self, tracked_pid: Option<u32>) {
        if let Ok(mut guard) = self.child.lock() {
            let should_consume = match (guard.as_mut(), tracked_pid) {
                (Some(child), Some(pid)) if child.id() == pid => {
                    matches!(child.try_wait(), Ok(Some(_)))
                }
                (None, _) => false,
                _ => false,
            };
            if should_consume {
                if let Some(mut child) = guard.take() {
                    let _ = child.wait();
                }
            }
        }

        if let Ok(mut cleanup_pid) = self.cleanup_pid.lock() {
            if tracked_pid.is_none() || *cleanup_pid == tracked_pid {
                *cleanup_pid = None;
            }
        }

        remove_runtime_marker(self.pid_path.as_deref(), "PID");
        remove_runtime_marker(self.port_path.as_deref(), "port");
    }
}

fn remove_runtime_marker(path: Option<&std::path::Path>, marker_name: &str) {
    let Some(path) = path else {
        return;
    };
    match std::fs::remove_file(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => eprintln!("Failed to remove backend {marker_name} file: {error}"),
    }
}

fn backend_port_is_released(port: u16) -> bool {
    use std::net::{SocketAddr, TcpListener};

    TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], port))).is_ok()
}

#[cfg(test)]
fn wait_for_port_release(port: u16, timeout: std::time::Duration) -> bool {
    use std::time::{Duration, Instant};

    let deadline = Instant::now() + timeout;
    loop {
        if backend_port_is_released(port) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn read_backend_port(path: &std::path::Path) -> Option<u16> {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|value| value.trim().parse::<u16>().ok())
}

fn terminate_process_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let tree_terminated = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok_and(|status| status.success());
        if !tree_terminated || process_is_running(pid) {
            terminate_windows_process(pid);
        }
    }

    #[cfg(not(windows))]
    {
        #[cfg(unix)]
        unsafe {
            unsafe extern "C" {
                fn kill(pid: i32, signal: i32) -> i32;
            }

            if let Ok(pid) = i32::try_from(pid) {
                const SIGKILL: i32 = 9;
                let _ = kill(pid, SIGKILL);
            }
        }

        #[cfg(not(unix))]
        let _ = pid;
    }
}

#[cfg(windows)]
fn terminate_windows_process(pid: u32) {
    use std::ffi::c_void;

    type Handle = *mut c_void;
    const PROCESS_TERMINATE: u32 = 0x0001;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(desired_access: u32, inherit_handle: i32, process_id: u32) -> Handle;
        fn TerminateProcess(process: Handle, exit_code: u32) -> i32;
        fn CloseHandle(object: Handle) -> i32;
    }

    unsafe {
        let process = OpenProcess(PROCESS_TERMINATE, 0, pid);
        if process.is_null() {
            return;
        }
        let _ = TerminateProcess(process, 1);
        let _ = CloseHandle(process);
    }
}

#[cfg(windows)]
fn process_is_running(pid: u32) -> bool {
    use std::ffi::c_void;

    type Handle = *mut c_void;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    const ERROR_INVALID_PARAMETER: u32 = 87;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(desired_access: u32, inherit_handle: i32, process_id: u32) -> Handle;
        fn GetExitCodeProcess(process: Handle, exit_code: *mut u32) -> i32;
        fn CloseHandle(object: Handle) -> i32;
        fn GetLastError() -> u32;
    }

    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process.is_null() {
            // ERROR_INVALID_PARAMETER is how OpenProcess reports a PID that
            // no longer exists. Access-denied and unexpected failures remain
            // conservatively "running" so cleanup identity is not consumed.
            return GetLastError() != ERROR_INVALID_PARAMETER;
        }
        let mut exit_code = STILL_ACTIVE;
        let inspected = GetExitCodeProcess(process, &mut exit_code);
        let _ = CloseHandle(process);
        inspected == 0 || exit_code == STILL_ACTIVE
    }
}

#[cfg(all(not(windows), unix))]
fn process_is_running(pid: u32) -> bool {
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }

    let Ok(pid) = i32::try_from(pid) else {
        return false;
    };
    if unsafe { kill(pid, 0) } == 0 {
        return true;
    }

    // ESRCH means no such process. EPERM and unexpected errors are treated as
    // still running so a failed inspection never destroys retry identity.
    std::io::Error::last_os_error().raw_os_error() != Some(3)
}

#[cfg(all(not(windows), not(unix)))]
fn process_is_running(_pid: u32) -> bool {
    true
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.terminate();
    }
}

#[cfg(test)]
mod tests {
    use super::{
        parse_backend_process_id, sanitize_update_log_field, verified_backend_process_id,
        wait_for_port_release, BackendProcess,
    };
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::path::PathBuf;
    use std::process::{Child, Command, Stdio};
    use std::time::Duration;

    fn spawn_backend_listener() -> (Child, u16) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("reserve test port");
        let port = listener.local_addr().expect("test address").port();
        drop(listener);

        let test_exe = std::env::current_exe().expect("current test executable");
        let child = Command::new(test_exe)
            .args(["--exact", "tests::backend_listener_child", "--nocapture"])
            .env("LOREWEFT_BACKEND_LISTENER_CHILD", "1")
            .env("LOREWEFT_BACKEND_LISTENER_PORT", port.to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn backend listener child");

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while TcpStream::connect(("127.0.0.1", port)).is_err() {
            assert!(
                std::time::Instant::now() < deadline,
                "backend listener child did not become ready"
            );
            std::thread::sleep(Duration::from_millis(25));
        }

        (child, port)
    }

    fn write_runtime_markers(test_name: &str, pid: u32, port: u16) -> (PathBuf, PathBuf, PathBuf) {
        let marker_dir = std::env::temp_dir().join(format!(
            "loreweft-{test_name}-{}-{port}",
            std::process::id()
        ));
        std::fs::create_dir_all(&marker_dir).expect("create marker directory");
        let pid_path = marker_dir.join("backend.pid");
        let port_path = marker_dir.join("backend.port");
        std::fs::write(&pid_path, pid.to_string()).expect("write pid marker");
        std::fs::write(&port_path, port.to_string()).expect("write port marker");
        (marker_dir, pid_path, port_path)
    }

    fn serve_health_response(body: String) -> (std::net::SocketAddr, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind health fixture");
        let address = listener.local_addr().expect("health fixture address");
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept health request");
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .expect("set health request timeout");
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request);
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write health response");
        });
        (address, server)
    }

    #[test]
    fn backend_listener_child() {
        if std::env::var_os("LOREWEFT_BACKEND_LISTENER_CHILD").is_none() {
            return;
        }

        let port: u16 = std::env::var("LOREWEFT_BACKEND_LISTENER_PORT")
            .expect("child port")
            .parse()
            .expect("valid child port");
        let listener = TcpListener::bind(("127.0.0.1", port)).expect("bind child listener");
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else {
                continue;
            };
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request);
            let body = format!(
                "{{\"status\":\"ok\",\"version\":\"{}\",\"process_id\":{}}}",
                env!("CARGO_PKG_VERSION"),
                std::process::id()
            );
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = stream.write_all(response.as_bytes());
        }
    }

    #[test]
    fn update_log_fields_drop_controls_and_enforce_a_character_limit() {
        assert_eq!(
            sanitize_update_log_field(" download\r\nstarted ", 32),
            "downloadstarted"
        );
        assert_eq!(sanitize_update_log_field("更新阶段abcdef", 5), "更新阶段a");
    }

    #[test]
    fn health_identity_rejects_a_legacy_response_without_process_id() {
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{{\"status\":\"ok\",\"version\":\"{}\"}}",
            env!("CARGO_PKG_VERSION")
        );

        assert_eq!(parse_backend_process_id(&response), None);
    }

    #[test]
    fn health_identity_rejects_a_wrong_version_or_degraded_service() {
        let wrong_version = format!(
            "HTTP/1.1 200 OK\r\n\r\n{{\"status\":\"ok\",\"version\":\"0.0.0\",\"process_id\":{}}}",
            std::process::id()
        );
        let degraded = format!(
            "HTTP/1.1 200 OK\r\n\r\n{{\"status\":\"degraded\",\"version\":\"{}\",\"process_id\":{}}}",
            env!("CARGO_PKG_VERSION"),
            std::process::id()
        );

        assert_eq!(parse_backend_process_id(&wrong_version), None);
        assert_eq!(parse_backend_process_id(&degraded), None);
    }

    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    fn health_identity_accepts_only_the_reported_bundled_executable() {
        let process_id = std::process::id();
        let body = format!(
            "{{\"status\":\"ok\",\"version\":\"{}\",\"process_id\":{process_id}}}",
            env!("CARGO_PKG_VERSION")
        );
        let (address, server) = serve_health_response(body);
        let current_exe = std::env::current_exe().expect("current executable");

        assert_eq!(
            verified_backend_process_id(address, &current_exe),
            Some(process_id)
        );
        server.join().expect("join health fixture");
    }

    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    fn health_identity_rejects_a_nonexistent_reported_process() {
        let body = format!(
            "{{\"status\":\"ok\",\"version\":\"{}\",\"process_id\":4294967295}}",
            env!("CARGO_PKG_VERSION")
        );
        let (address, server) = serve_health_response(body);
        let current_exe = std::env::current_exe().expect("current executable");

        assert_eq!(verified_backend_process_id(address, &current_exe), None);
        server.join().expect("join health fixture");
    }

    #[test]
    fn update_preparation_accepts_an_already_released_port() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("reserve test port");
        let port = listener.local_addr().expect("test address").port();
        drop(listener);

        assert!(wait_for_port_release(port, Duration::from_millis(100)));
    }

    #[test]
    fn update_preparation_waits_until_the_backend_port_is_released() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("reserve test port");
        let port = listener.local_addr().expect("test address").port();
        let release = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(120));
            drop(listener);
        });

        assert!(wait_for_port_release(port, Duration::from_secs(2)));
        release.join().expect("release test listener");
    }

    #[test]
    fn update_preparation_rejects_a_port_that_stays_occupied() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("reserve test port");
        let port = listener.local_addr().expect("test address").port();

        assert!(!wait_for_port_release(port, Duration::from_millis(120)));
        drop(listener);
    }

    #[test]
    fn update_preparation_terminates_the_owned_backend_and_removes_runtime_markers() {
        let (child, port) = spawn_backend_listener();
        let (marker_dir, pid_path, port_path) =
            write_runtime_markers("owned-update", child.id(), port);

        let process = BackendProcess::owned(child, pid_path.clone(), port_path.clone(), port);
        process
            .prepare_for_update()
            .expect("prepare update by terminating backend");

        assert!(wait_for_port_release(port, Duration::from_millis(100)));
        assert!(!pid_path.exists());
        assert!(!port_path.exists());
        drop(process);
        std::fs::remove_dir_all(marker_dir).expect("remove marker directory");
    }

    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    fn external_identity_mismatch_never_requests_termination() {
        let (mut child, port) = spawn_backend_listener();
        let pid = child.id();
        let (marker_dir, pid_path, port_path) =
            write_runtime_markers("external-identity-mismatch", pid, port);
        let process = BackendProcess::external(
            Some(pid_path.clone()),
            Some(port_path.clone()),
            pid,
            pid_path.clone(),
            port,
        );
        let termination_requested = std::sync::atomic::AtomicBool::new(false);

        assert!(process
            .prepare_for_update_with(Duration::from_millis(100), |_process, _pid| {
                termination_requested.store(true, std::sync::atomic::Ordering::SeqCst);
            })
            .is_err());
        assert!(!termination_requested.load(std::sync::atomic::Ordering::SeqCst));
        assert!(child.try_wait().expect("inspect external child").is_none());
        assert!(pid_path.exists());
        assert!(port_path.exists());

        child.kill().expect("terminate fixture child");
        let _ = child.wait();
        process
            .prepare_for_update_with(Duration::from_secs(1), |_process, _pid| {})
            .expect("consume identity after fixture exits");
        drop(process);
        std::fs::remove_dir_all(marker_dir).expect("remove marker directory");
    }

    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    fn backend_lifecycle_operations_are_serialized() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::{Arc, Barrier};

        let (mut child, port) = spawn_backend_listener();
        let pid = child.id();
        let (marker_dir, pid_path, port_path) =
            write_runtime_markers("serialized-lifecycle", pid, port);
        let process = Arc::new(BackendProcess::external(
            Some(pid_path.clone()),
            Some(port_path.clone()),
            pid,
            std::env::current_exe().expect("current executable"),
            port,
        ));
        let barrier = Arc::new(Barrier::new(3));
        let active = Arc::new(AtomicUsize::new(0));
        let maximum = Arc::new(AtomicUsize::new(0));

        let attempts = (0..2)
            .map(|_| {
                let process = Arc::clone(&process);
                let barrier = Arc::clone(&barrier);
                let active = Arc::clone(&active);
                let maximum = Arc::clone(&maximum);
                std::thread::spawn(move || {
                    barrier.wait();
                    process.prepare_for_update_with(
                        Duration::from_millis(50),
                        move |_process, _pid| {
                            let current = active.fetch_add(1, Ordering::SeqCst) + 1;
                            maximum.fetch_max(current, Ordering::SeqCst);
                            std::thread::sleep(Duration::from_millis(80));
                            active.fetch_sub(1, Ordering::SeqCst);
                        },
                    )
                })
            })
            .collect::<Vec<_>>();

        barrier.wait();
        for attempt in attempts {
            assert!(attempt.join().expect("join lifecycle attempt").is_err());
        }
        assert_eq!(maximum.load(Ordering::SeqCst), 1);
        assert_eq!(process.tracked_pid(), Ok(Some(pid)));

        child.kill().expect("terminate fixture child");
        let _ = child.wait();
        process
            .prepare_for_update_with(Duration::from_secs(1), |_process, _pid| {})
            .expect("consume identity after fixture exits");
        drop(process);
        std::fs::remove_dir_all(marker_dir).expect("remove marker directory");
    }

    #[cfg(windows)]
    #[test]
    fn update_preparation_terminates_a_reused_external_backend() {
        let (mut child, port) = spawn_backend_listener();
        let pid = child.id();
        let (marker_dir, pid_path, port_path) = write_runtime_markers("external-update", pid, port);
        let process = BackendProcess::external(
            Some(pid_path.clone()),
            Some(port_path.clone()),
            pid,
            std::env::current_exe().expect("current executable"),
            port,
        );

        process
            .prepare_for_update_with(
                Duration::from_secs(5),
                BackendProcess::request_process_termination,
            )
            .expect("prepare update by terminating reused external backend");

        assert!(wait_for_port_release(port, Duration::from_millis(100)));
        assert!(child.try_wait().expect("inspect external child").is_some());
        assert_eq!(*process.cleanup_pid.lock().expect("cleanup PID lock"), None);
        assert!(!pid_path.exists());
        assert!(!port_path.exists());
        let _ = child.wait();
        drop(process);
        std::fs::remove_dir_all(marker_dir).expect("remove marker directory");
    }

    #[test]
    fn failed_external_update_preparation_preserves_identity_for_retry() {
        let (mut child, port) = spawn_backend_listener();
        let pid = child.id();
        let (marker_dir, pid_path, port_path) = write_runtime_markers("retry-update", pid, port);
        let process = BackendProcess::external(
            Some(pid_path.clone()),
            Some(port_path.clone()),
            pid,
            std::env::current_exe().expect("current executable"),
            port,
        );

        let first_attempt =
            process.prepare_for_update_with(Duration::from_millis(120), |_process, _pid| {
                // Simulate an operating-system termination request that did
                // not reach the process. The next attempt must retain enough
                // identity to target the same backend again.
            });

        assert!(first_attempt.is_err());
        assert_eq!(
            *process.cleanup_pid.lock().expect("cleanup PID lock"),
            Some(pid)
        );
        assert_eq!(process.tracked_pid(), Ok(Some(pid)));
        assert!(process.child.lock().expect("child lock").is_none());
        assert!(pid_path.exists());
        assert!(port_path.exists());
        assert!(TcpStream::connect(("127.0.0.1", port)).is_ok());

        process
            .prepare_for_update_with(
                Duration::from_secs(5),
                BackendProcess::request_process_termination,
            )
            .expect("retry update preparation with preserved process identity");

        assert!(wait_for_port_release(port, Duration::from_millis(100)));
        assert_eq!(*process.cleanup_pid.lock().expect("cleanup PID lock"), None);
        assert!(process.child.lock().expect("child lock").is_none());
        assert!(!pid_path.exists());
        assert!(!port_path.exists());
        assert!(child.try_wait().expect("inspect external child").is_some());
        let _ = child.wait();
        drop(process);
        std::fs::remove_dir_all(marker_dir).expect("remove marker directory");
    }
}

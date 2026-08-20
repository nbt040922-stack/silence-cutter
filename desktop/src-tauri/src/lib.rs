use serde::Deserialize;
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::{Manager, State};

struct AppState {
    root: PathBuf,
    python: PathBuf,
    resources: PathBuf,
    data: PathBuf,
    backend_worker: Mutex<Option<Child>>,
    qwen_worker: Mutex<Option<Child>>,
}

#[derive(Deserialize)]
pub struct RpcRequest {
    operation: String,
    #[serde(default)]
    payload: Value,
}

fn project_root() -> Result<PathBuf, String> {
    if let Ok(value) = std::env::var("SILENCE_CUTTER_ROOT") {
        let path = PathBuf::from(value);
        if path.join("production").is_dir() {
            return Ok(path);
        }
    }
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let executable_dir = executable.parent().ok_or("cannot resolve installation directory")?;
    for path in [
        executable_dir.join("resources/app"),
        executable_dir.join("app"),
        std::env::current_dir().map_err(|error| error.to_string())?,
    ] {
        if path.join("production").is_dir() {
            return Ok(path);
        }
    }
    #[cfg(debug_assertions)]
    {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent().and_then(Path::parent)
            .ok_or("cannot resolve development project root")?.to_path_buf();
        if path.join("production").is_dir() { return Ok(path); }
    }
    Err("Silence Cutter application resources were not found".into())
}

fn python_path(root: &Path) -> Result<PathBuf, String> {
    if let Ok(value) = std::env::var("SILENCE_CUTTER_PYTHON") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Ok(path);
        }
    }
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let executable_dir = executable.parent().ok_or("cannot resolve installation directory")?;
    for path in [
        executable_dir.join("resources/runtime/python/python.exe"),
        executable_dir.join("runtime/python/python.exe"),
        root.join(".venv_asr_test/Scripts/python.exe"),
        root.join(".venv/Scripts/python.exe"),
    ] {
        if path.is_file() {
            return Ok(path);
        }
    }
    Err("Python environment was not found; set SILENCE_CUTTER_PYTHON".into())
}

fn resource_dir(root: &Path) -> PathBuf {
    if let Ok(value) = std::env::var("SILENCE_CUTTER_RESOURCE_DIR") {
        return PathBuf::from(value);
    }
    let executable = std::env::current_exe().expect("current executable is required");
    let installed = executable.parent().unwrap().join("resources");
    if installed.join("app").is_dir() { installed } else { root.join("release_assets") }
}

fn data_dir() -> PathBuf {
    if let Ok(value) = std::env::var("SILENCE_CUTTER_DATA_DIR") {
        return PathBuf::from(value);
    }
    PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into()))
        .join("SilenceCutter")
}

fn configure_command(command: &mut Command, root: &Path, resources: &Path, data: &Path) {
    let bin = resources.join("bin");
    let old_path = std::env::var("PATH").unwrap_or_default();
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONPATH", root)
        .env("SILENCE_CUTTER_RESOURCE_DIR", resources)
        .env("SILENCE_CUTTER_DATA_DIR", data)
        .env("PATH", format!("{};{}", bin.display(), old_path));
    #[cfg(windows)]
    command.creation_flags(0x08000000);
}

fn hidden_command(program: &Path) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn spawn_python_worker(
    root: &Path, python: &Path, resources: &Path, data: &Path,
    module: &str, log_name: &str,
) -> Result<Child, String> {
    let log_dir = data;
    fs::create_dir_all(&log_dir).map_err(|error| error.to_string())?;
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join(format!("{}.stdout.log", log_name)))
        .map_err(|error| error.to_string())?;
    let stderr = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join(format!("{}.stderr.log", log_name)))
        .map_err(|error| error.to_string())?;
    let mut command = hidden_command(python);
    configure_command(&mut command, root, resources, data);
    command.args(["-m", module]);
    if module == "backend.job_runner" { command.arg("worker"); }
    command.current_dir(root)
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .map_err(|error| error.to_string())
}

fn wait_qwen_ready() -> Result<(), String> {
    for _ in 0..360 {
        if let Ok(mut stream) = TcpStream::connect_timeout(
            &"127.0.0.1:8792".parse().unwrap(), Duration::from_secs(1),
        ) {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
            let _ = stream.write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n");
            let mut body = String::new();
            if stream.read_to_string(&mut body).is_ok() {
                if let Some(json) = body.split("\r\n\r\n").nth(1) {
                    if let Ok(value) = serde_json::from_str::<Value>(json) {
                        match value.get("status").and_then(Value::as_str) {
                            Some("READY") => return Ok(()),
                            Some("ERROR") => return Err("Qwen worker failed to warm up".into()),
                            _ => {}
                        }
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err("Qwen worker READY timeout".into())
}

fn call_backend(root: &Path, python: &Path, resources: &Path, data: &Path, request: RpcRequest) -> Result<Value, String> {
    let mut command = hidden_command(python);
    configure_command(&mut command, root, resources, data);
    let mut child = command.args(["-m", "backend.job_runner", "rpc"])
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| error.to_string())?;
    let input = serde_json::json!({"operation": request.operation, "payload": request.payload});
    child
        .stdin
        .as_mut()
        .ok_or("backend stdin unavailable")?
        .write_all(input.to_string().as_bytes())
        .map_err(|error| error.to_string())?;
    let output = child
        .wait_with_output()
        .map_err(|error| error.to_string())?;
    let body: Value = serde_json::from_slice(&output.stdout).map_err(|_| {
        format!(
            "backend returned invalid JSON: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    })?;
    if !output.status.success() || body.get("ok") != Some(&Value::Bool(true)) {
        return Err(body
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("backend request failed")
            .to_string());
    }
    Ok(body.get("result").cloned().unwrap_or(Value::Null))
}

fn stop_worker(worker: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &worker.id().to_string(), "/T", "/F"])
            .creation_flags(0x08000000)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(not(windows))]
    let _ = worker.kill();
    let _ = worker.wait();
}

#[tauri::command]
async fn backend_rpc(state: State<'_, AppState>, request: RpcRequest) -> Result<Value, String> {
    let root = state.root.clone();
    let python = state.python.clone();
    let resources = state.resources.clone();
    let data = state.data.clone();
    tauri::async_runtime::spawn_blocking(move || call_backend(&root, &python, &resources, &data, request))
        .await
        .map_err(|error| error.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let root = project_root().expect("Silence Cutter root is required");
    let python = python_path(&root).expect("Silence Cutter Python is required");
    let resources = resource_dir(&root);
    let data = data_dir();
    fs::create_dir_all(&data).expect("Silence Cutter data directory is required");
    let qwen_worker = spawn_python_worker(
        &root, &python, &resources, &data, "qwen_worker.supervisor", "qwen-worker",
    ).expect("Qwen worker failed to start");
    let qwen_pid = qwen_worker.id();
    let qwen_ready = wait_qwen_ready().is_ok();
    let backend_worker = spawn_python_worker(
        &root, &python, &resources, &data, "backend.job_runner", "desktop-worker",
    ).expect("desktop worker failed to start");
    let runtime_path = data.join("production-runtime.json");
    let _ = fs::write(&runtime_path, serde_json::json!({
        "owner_pid": std::process::id(),
        "qwen_worker_pid": qwen_pid,
        "qwen_worker_health": if qwen_ready { "READY" } else { "ERROR" },
        "backend_worker_pid": backend_worker.id(),
        "startup_unix_seconds": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs(),
    }).to_string());
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState {
            root,
            python,
            resources,
            data,
            backend_worker: Mutex::new(Some(backend_worker)),
            qwen_worker: Mutex::new(Some(qwen_worker)),
        })
        .invoke_handler(tauri::generate_handler![backend_rpc])
        .build(tauri::generate_context!())
        .expect("error while building Silence Cutter desktop app");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            let state = handle.state::<AppState>();
            for slot in [&state.backend_worker, &state.qwen_worker] {
                if let Some(mut worker) = slot.lock().ok().and_then(|mut guard| guard.take()) {
                    stop_worker(&mut worker);
                }
            }
            let _ = fs::remove_file(state.data.join("production-runtime.json"));
        }
    });
}

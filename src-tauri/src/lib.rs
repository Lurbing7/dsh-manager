use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};

/// The npm package that ships the harness.
const PKG_NAME: &str = "@deepseek-ai/dsh";
/// Same package, percent-encoded the way a registry metadata URL expects it.
const PKG_URL_PATH: &str = "@deepseek-ai%2Fdsh";
const DEFAULT_REGISTRY: &str = "https://registry.npmmirror.com";
/// "once a day" — 24h between automatic checks.
const CHECK_INTERVAL_SECS: u64 = 24 * 60 * 60;
/// The port this machine's harness UI is normally served on.
const DEFAULT_HARNESS_PORT: u16 = 3080;
const TRAY_ID: &str = "main-tray";

// Windows process creation flags.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;

// ---------------------------------------------------------------------------
// Data shapes handed to the frontend
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
struct LocalInfo {
    ok: bool,
    dsh_version: Option<String>,
    node_dir: Option<String>,
    node_version: Option<String>,
    error: Option<String>,
}

#[derive(Serialize, Clone)]
struct UpdateInfo {
    ok: bool,
    local_version: Option<String>,
    latest_version: Option<String>,
    has_update: bool,
    registry: String,
    checked_at: u64,
    error: Option<String>,
}

#[derive(Serialize, Clone)]
struct ActionResult {
    ok: bool,
    action: String,
    message: String,
    output: String,
}

#[derive(Serialize, Deserialize, Clone, Default)]
struct PersistedState {
    last_check_at: Option<u64>,
    last_latest_version: Option<String>,
    last_local_version: Option<String>,
}

// ---------------------------------------------------------------------------
// Local install discovery
// ---------------------------------------------------------------------------

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Strip the `\\?\` prefix `canonicalize` adds on Windows.
fn clean_path(p: PathBuf) -> PathBuf {
    let s = p.to_string_lossy().to_string();
    match s.strip_prefix(r"\\?\") {
        Some(rest) => PathBuf::from(rest),
        None => p,
    }
}

/// Locate the active Node install by finding `dsh.cmd` on PATH.
///
/// npm puts its global shims directly in the Node install root, so the directory
/// holding `dsh.cmd` *is* the Node root for whichever nvm version is active.
/// This tracks `nvm use` automatically — no hardcoded nvm paths.
fn find_node_dir() -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        if dir.join("dsh.cmd").is_file() {
            return Some(dir);
        }
    }
    None
}

fn read_local() -> LocalInfo {
    let Some(node_dir) = find_node_dir() else {
        return LocalInfo {
            ok: false,
            dsh_version: None,
            node_dir: None,
            node_version: None,
            error: Some(
                "PATH 上找不到 dsh.cmd：dsh 可能没装，或当前 nvm 版本里没有它".to_string(),
            ),
        };
    };

    let pkg = node_dir
        .join("node_modules")
        .join("@deepseek-ai")
        .join("dsh")
        .join("package.json");

    let installed = std::fs::read_to_string(&pkg)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| {
            v.get("version")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
        });

    // Directory name of the resolved junction, e.g. v24.9.0
    let node_version = std::fs::canonicalize(&node_dir)
        .map(clean_path)
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .filter(|n| n.starts_with('v'));

    LocalInfo {
        ok: installed.is_some(),
        error: if installed.is_none() {
            Some(format!("找到 Node 目录，但读不到 {}", pkg.display()))
        } else {
            None
        },
        dsh_version: installed,
        node_dir: Some(node_dir.to_string_lossy().to_string()),
        node_version,
    }
}

/// Respect the user's npm registry (npmmirror on this machine) so the version we
/// report is the one that can actually be installed.
fn read_registry() -> String {
    if let Some(home) = std::env::var_os("USERPROFILE") {
        let npmrc = PathBuf::from(home).join(".npmrc");
        if let Ok(txt) = std::fs::read_to_string(&npmrc) {
            for line in txt.lines() {
                let line = line.trim();
                if let Some(rest) = line.strip_prefix("registry=") {
                    let v = rest.trim().trim_end_matches('/');
                    if !v.is_empty() {
                        return v.to_string();
                    }
                }
            }
        }
    }
    DEFAULT_REGISTRY.to_string()
}

// ---------------------------------------------------------------------------
// Registry check
// ---------------------------------------------------------------------------

async fn fetch_latest(registry: &str) -> Result<String, String> {
    let url = format!("{}/{}", registry.trim_end_matches('/'), PKG_URL_PATH);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .user_agent("dsh-panel")
        .build()
        .map_err(|e| format!("创建 HTTP 客户端失败: {e}"))?;

    let resp = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("请求 registry 失败: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("registry 返回 HTTP {}", resp.status()));
    }

    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("解析 registry 响应失败: {e}"))?;

    body.get("dist-tags")
        .and_then(|t| t.get("latest"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| "registry 响应里没有 dist-tags.latest".to_string())
}

fn is_newer(latest: &str, current: Option<&str>) -> bool {
    let Some(cur) = current else { return false };
    match (
        semver::Version::parse(latest),
        semver::Version::parse(cur),
    ) {
        (Ok(l), Ok(c)) => l > c,
        _ => latest != cur,
    }
}

async fn run_check(app: &AppHandle) -> UpdateInfo {
    let local = read_local();
    let registry = read_registry();
    let now = now_secs();

    let mut info = UpdateInfo {
        ok: false,
        local_version: local.dsh_version.clone(),
        latest_version: None,
        has_update: false,
        registry: registry.clone(),
        checked_at: now,
        error: None,
    };

    if local.dsh_version.is_none() {
        info.error = local.error.clone();
        return info;
    }

    match fetch_latest(&registry).await {
        Ok(latest) => {
            info.has_update = is_newer(&latest, local.dsh_version.as_deref());
            info.latest_version = Some(latest.clone());
            info.ok = true;
            save_state(
                app,
                &PersistedState {
                    last_check_at: Some(now),
                    last_latest_version: Some(latest),
                    last_local_version: local.dsh_version,
                },
            );
            update_tray(app, &info);
        }
        Err(e) => info.error = Some(e),
    }

    info
}

// ---------------------------------------------------------------------------
// Persisted state (drives the "once a day" rule)
// ---------------------------------------------------------------------------

fn state_file(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_data_dir().ok()?;
    let _ = std::fs::create_dir_all(&dir);
    Some(dir.join("state.json"))
}

fn load_state(app: &AppHandle) -> Option<PersistedState> {
    let path = state_file(app)?;
    let txt = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&txt).ok()
}

fn save_state(app: &AppHandle, state: &PersistedState) {
    if let Some(path) = state_file(app) {
        if let Ok(txt) = serde_json::to_string_pretty(state) {
            let _ = std::fs::write(path, txt);
        }
    }
}

fn check_due(app: &AppHandle) -> bool {
    match load_state(app).and_then(|s| s.last_check_at) {
        Some(t) => now_secs().saturating_sub(t) >= CHECK_INTERVAL_SECS,
        None => true,
    }
}

// ---------------------------------------------------------------------------
// Tray
// ---------------------------------------------------------------------------

fn update_tray(app: &AppHandle, info: &UpdateInfo) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let text = if info.has_update {
            format!(
                "DSH Panel · 有新版本 {}（当前 {}）",
                info.latest_version.clone().unwrap_or_default(),
                info.local_version.clone().unwrap_or_default()
            )
        } else {
            format!(
                "DSH Panel · {} 已是最新",
                info.local_version.clone().unwrap_or_default()
            )
        };
        let _ = tray.set_tooltip(Some(&text));
    }
}

fn show_panel(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async fn port_alive(url: &str) -> bool {
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_millis(1500))
        .build()
    else {
        return false;
    };
    // Any HTTP answer at all means something is serving there.
    client.get(url).send().await.is_ok()
}

/// The button is "open the harness", not "start a server":
/// already serving → just open a browser tab; otherwise boot it in a new console.
async fn open_harness_inner(port: u16) -> ActionResult {
    let url = format!("http://127.0.0.1:{}/", port);

    if port_alive(&url).await {
        return match Command::new("cmd")
            .args(["/c", "start", "", &url])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
        {
            Ok(_) => ActionResult {
                ok: true,
                action: "browser".into(),
                message: format!("harness 已在运行，已打开浏览器 {url}"),
                output: String::new(),
            },
            Err(e) => ActionResult {
                ok: false,
                action: "browser".into(),
                message: format!("打开浏览器失败: {e}"),
                output: String::new(),
            },
        };
    }

    let line = format!("dsh web --port {port}");
    match Command::new("cmd")
        .args(["/c", "start", "", "cmd", "/k", &line])
        .creation_flags(CREATE_NEW_CONSOLE)
        .spawn()
    {
        Ok(_) => ActionResult {
            ok: true,
            action: "started".into(),
            message: format!("已在新终端窗口启动 dsh web --port {port}"),
            output: String::new(),
        },
        Err(e) => ActionResult {
            ok: false,
            action: "started".into(),
            message: format!("启动 harness 失败: {e}"),
            output: String::new(),
        },
    }
}

async fn upgrade_inner() -> ActionResult {
    let local = read_local();
    let Some(node_dir) = local.node_dir.clone() else {
        return ActionResult {
            ok: false,
            action: "upgrade".into(),
            message: "找不到 Node 目录，无法升级".into(),
            output: String::new(),
        };
    };

    let dir = PathBuf::from(&node_dir);
    let npm_cmd = dir.join("npm.cmd");
    if !npm_cmd.is_file() {
        return ActionResult {
            ok: false,
            action: "upgrade".into(),
            message: format!("{} 不存在", npm_cmd.display()),
            output: String::new(),
        };
    }

    // Put the active Node dir first on PATH and run there, so `npm` resolves to
    // this nvm version's npm rather than whatever else is on PATH.
    let new_path = match std::env::var("PATH") {
        Ok(p) => format!("{node_dir};{p}"),
        Err(_) => node_dir.clone(),
    };

    let joined = tauri::async_runtime::spawn_blocking(move || {
        Command::new("cmd")
            .arg("/c")
            .arg("npm")
            .args(["install", "-g", &format!("{PKG_NAME}@latest")])
            .current_dir(&dir)
            .env("PATH", new_path)
            .creation_flags(CREATE_NO_WINDOW)
            .output()
    })
    .await;

    match joined {
        Ok(Ok(out)) => {
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            let combined = format!("{stdout}{stderr}");

            if out.status.success() {
                let after = read_local();
                ActionResult {
                    ok: true,
                    action: "upgrade".into(),
                    message: format!(
                        "升级完成，当前版本 {}。正在运行的 harness 需要重启才会生效",
                        after.dsh_version.unwrap_or_else(|| "未知".into())
                    ),
                    output: combined,
                }
            } else {
                let locked = ["EBUSY", "EPERM", "operation not permitted", "resource busy"]
                    .iter()
                    .any(|k| combined.contains(k));
                ActionResult {
                    ok: false,
                    action: "upgrade".into(),
                    message: if locked {
                        "升级失败：文件被占用（harness 多半正在运行）。请先关掉正在运行的 harness 再试".into()
                    } else {
                        format!("升级失败，npm 退出码 {:?}", out.status.code())
                    },
                    output: combined,
                }
            }
        }
        Ok(Err(e)) => ActionResult {
            ok: false,
            action: "upgrade".into(),
            message: format!("无法执行 npm: {e}"),
            output: String::new(),
        },
        Err(e) => ActionResult {
            ok: false,
            action: "upgrade".into(),
            message: format!("升级任务失败: {e}"),
            output: String::new(),
        },
    }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

#[tauri::command]
fn get_local_info() -> LocalInfo {
    read_local()
}

#[tauri::command]
async fn check_update(app: AppHandle) -> UpdateInfo {
    run_check(&app).await
}

#[tauri::command]
async fn upgrade_dsh() -> ActionResult {
    upgrade_inner().await
}

#[tauri::command]
async fn open_harness(port: Option<u16>) -> ActionResult {
    open_harness_inner(port.unwrap_or(DEFAULT_HARNESS_PORT)).await
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let open_panel = MenuItem::with_id(app, "open_panel", "打开面板", true, None::<&str>)?;
            let launch = MenuItem::with_id(app, "launch", "打开 Harness", true, None::<&str>)?;
            let check = MenuItem::with_id(app, "check", "立即检查更新", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_panel, &launch, &check, &quit])?;

            TrayIconBuilder::with_id(TRAY_ID)
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("DSH Panel")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "quit" => app.exit(0),
                    "open_panel" => show_panel(app),
                    "launch" => {
                        tauri::async_runtime::spawn(async move {
                            let _ = open_harness_inner(DEFAULT_HARNESS_PORT).await;
                        });
                    }
                    "check" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let info = run_check(&app).await;
                            let _ = app.emit("update-checked", &info);
                        });
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_panel(tray.app_handle());
                    }
                })
                .build(app)?;

            // Background sweep: checks on startup, then at most once every 24h.
            let handle = app.handle().clone();
            std::thread::spawn(move || loop {
                if check_due(&handle) {
                    let info = tauri::async_runtime::block_on(run_check(&handle));
                    let _ = handle.emit("update-checked", &info);
                }
                std::thread::sleep(Duration::from_secs(3600));
            });

            Ok(())
        })
        // Tray-resident: closing the window hides it instead of quitting.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_local_info,
            check_update,
            upgrade_dsh,
            open_harness,
            quit_app
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

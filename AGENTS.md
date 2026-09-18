# AGENTS.md — dsh-panel 项目代理指令

Windows 桌面小面板：常驻托盘，围绕本机 DSH 做**更新检测、启动、监控与管理**。
技术栈 **Tauri 2 · Rust · Vue 3 + TypeScript**。用户不写代码，改动全部由 AI 完成。

## 常用命令

```bash
npm install
npm run tauri dev              # 开发
npm run build                  # 前端类型检查 + 构建（生成 dist/）
cd src-tauri && cargo check    # 只检查 Rust
npm run tauri build            # 打包
```

> **做完整重装用 `scripts/build-and-install.ps1`**（含清 cargo 图标缓存 + 安装 + 刷图标缓存）：
> `-SkipIcons` 跳过图标生成，只改代码时用。别直接 `npm run tauri build` 后就完事 —— 理由见「图标缓存」。

**顺序要求**：`cargo build` 之前 `dist/` 必须存在，否则 `tauri::generate_context!()` 编译失败。
改了前端先 `npm run build`。

**`cargo build`（debug）产出的 exe 不能独立运行**（已实测）：debug 构建**不内嵌前端**，
启动后 WebView 去连 `http://localhost:1420`，没跑 `tauri dev` 时就显示
「嗯…无法访问此页面 / localhost 拒绝连接」——看起来像应用坏了，其实是启动方式错了。
要独立运行/长期使用必须 `npm run tauri build`（release 内嵌 dist），再跑 `scripts/install.ps1`。

**图标缓存有两层，两层都踩过**：

1. **编译层（更隐蔽）**：`tauri-build` **不会**因为 `icons/icon.ico` 变化而重跑 build script，
   于是 `npm run tauri build` 一直把**旧图标**打进 exe。实测：`icon.ico` 里已是新图，
   从 exe 里扒出来的还是上一版。**改图标后必须删掉本 crate 的 build-script 缓存**：
   `target/release/build/dsh-panel-*` 与 `target/release/.fingerprint/dsh-panel-*`
   （`cargo clean -p dsh-panel` **不够**）。`scripts/build-and-install.ps1` 已包含这一步。
   **验证方法**：从 exe 二进制里按 `\x89PNG` 签名扒出所有 PNG，挑 256×256 那张肉眼确认。
2. **Shell 层**：Windows 按**路径**缓存图标且不看文件时间戳。`ie4uinit -show` 刷不动
   桌面快捷方式与任务栏按钮——要 `Stop-Process -Name explorer -Force`，
   再删 `%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*`，然后重启 explorer。

**`scripts/*.ps1` 一律纯 ASCII**（同 workspace 约定）。

**`scripts/build-and-install.ps1` 里不要用 `$ErrorActionPreference = 'Stop'`**：tauri CLI 把进度
写到 stderr，PowerShell 会把 native stderr 当终止错误，脚本会在构建中途自己退出（踩过）。
现在是 `'Continue'` + 每步 `Assert-LastExit` 检查 `$LASTEXITCODE`。

## 关键约束（改代码前必读）

1. **Rust 里调用命令行程序必须走 `cmd /c`**。`Command::new("dsh")` 或 `Command::new("npm")` 在 Windows 上找不到 `.cmd`（`CreateProcess` 不查 PATHEXT）。正确写法是 `Command::new("cmd").arg("/c")...`。
2. **不要用 `tauri-plugin-shell` 的 `open`** —— 它只放行 `http(s)/tel/mailto`，会拦本地命令。本项目所有进程启动都在 Rust 侧用 `std::process::Command`。
3. **窗口关闭 = 隐藏，不是退出**（`on_window_event` 里 `prevent_close` + `hide`）。退出只能走托盘菜单或界面上的"退出"按钮，两者都调 `app.exit(0)`。
4. **dsh 定位靠 PATH**：遍历 `PATH` 找 `dsh.cmd`，其父目录即当前激活的 Node 根目录。**不要硬编码 nvm 路径**——硬编码会在用户 `nvm use` 切换版本后失效。
5. **升级要在对的 Node 下跑**：`cmd /c npm install -g ...`，`current_dir` 设为该 Node 根目录，并把该目录前置到 `PATH`，否则可能用到别的 npm。
6. **子进程创建标志**：一律用 `CREATE_NO_WINDOW` (0x08000000)——npm 升级、启动 harness、起桥都不弹黑框。
   所有长任务的输出通过管道流进面板内嵌终端（见第 8 条），**不要再弹独立终端窗口**。
7. **`src-tauri/capabilities/default.json`**：`core:default` + `dialog:default`（后者是选目录对话框要的）。除此之外不要往里塞插件权限——逻辑写在 Rust command 里，前端只管 `invoke`。
8. **前端与 Rust 的接口**（`lib.rs` 里 `invoke_handler` 注册的）：
   - `get_local_info() -> { ok, dsh_version, node_dir, node_version, error }`
   - `check_update() -> { ok, local_version, latest_version, has_update, registry, checked_at, error }`
   - `upgrade_dsh() -> { ok, action, message, output }`
   - `open_harness(port?) -> ActionResult`（旧的"另开窗口"路径，保留）
   - `start_harness_inline(port?) -> ActionResult`（界面用这个）
   - `stop_harness(port?) -> ActionResult`（先杀面板启动的子进程；否则按端口找 node 进程）
   - `harness_running() -> bool`、`quit_app()`
   - `get_settings() -> SettingsView`（含 api_key_set/hint/source、feishu_paired/app_id/allowed_users；**不含任何密钥**）
   - `set_api_key(key?) -> ActionResult`（面板内保存的 key 优先于 DSH 凭据文件）
   - `set_feishu_allowed_users(users) -> ActionResult`
   - `set_desktop_source_dir(dir) -> ActionResult`（存 `settings.json`）
   - `start_desktop_app() / stop_desktop_app() / desktop_running() -> bool`
   - `get_balance_native(app, provider?) -> BalanceInfo`
   - `usage_series(bucket?, count?) -> UsageSeries`（day / week / month 聚合）
   - `usage_analysis(days?) -> UsageAnalysis`（指标卡 + 模型明细 + 会话排行）
   - `scan_sessions(days?) / scan_sessions_diag(limit?) -> ActionResult`（解析会话日志）
   - `list_plugins() -> Vec<ProfilePlugins>`
   - `install_plugin(profile, package) / remove_plugin(profile, package) / upgrade_plugins(profile) -> ActionResult`
   - `plugin_op_running() -> bool`
   - `feishu_bridge_status() -> BridgeStatus` / `feishu_bridge_start(allowed_users, profile?) -> ActionResult` / `feishu_bridge_stop() -> ActionResult`
   - `feishu_qr_begin() -> FeishuQrSession` / `feishu_qr_poll(device_code) -> FeishuQrPoll` / `feishu_qr_apply(app_id, app_secret, open_id) -> ActionResult`
   - `hermes_status() -> HermesStatus` / `open_hermes_page() -> ActionResult`
   - `open_harness_page(port?) -> ActionResult`（纯导航，绝不启动）
   事件：`update-checked`(UpdateInfo) · `harness-state`(bool) · `harness-output`(每行字符串)。
   改返回结构要**两边一起改**（`lib.rs` 的 struct + `App.vue` 的 interface）。
9. **后端一律不返回 `Err`**，用带 `ok` / `error` 字段的结构体。前端展示更简单，不会出现"命令静默失败"。
10. **端口 3080 写死在两处**：`lib.rs` 的 `DEFAULT_HARNESS_PORT` 和 `App.vue` 的 `HARNESS_PORT`；改要一起改。飞书桥与用量接口也都指向它。
11. **`scripts/*.ps1` 一律纯 ASCII**。本机是 Windows PowerShell 5.1，会把无 BOM 的 UTF-8 中文读成 GBK 导致语法错误。
12. **`stop_harness` 的两级策略不要简化**：面板自己启动的子进程用 `taskkill /T`（那是我们的下级，安全）；
    **停止"别处启动的"harness 时绝对不能带 `/T`** —— 如果 harness 正是拉起面板的那个进程，`/T` 会把面板自己也杀掉。
13. **提交信息里不要写 `<消息>` 这类尖括号**：PowerShell 会把 `<` 当重定向符，`git commit -m` 直接语法错误。用 `git commit -F <文件>`。

## 设计意图（别"优化"掉这些）

- **"启动 Web 端"和"启动桌面端"是两件事**（用户纠正过）：前者是 `dsh web`（浏览器 UI），后者是源码里的
  Electron 桌面端（`pnpm run dev:desktop`，**不随 npm 包分发**）。不要合并成一个"启动 Harness"。
- **按钮要随状态变红**：Web 端/桌面端在跑时显示红色的「停止 …」，不要一直显示"启动"。
- **忙碌时必须有可见反馈**：npm 装包要几十秒，按钮上要转圈（`.spinner`），否则用户以为卡死。
- **每日检查用 `state.json` 里的 `last_check_at` 时间戳判断**（`%APPDATA%\com.dshpanel.app\state.json`），后台线程每小时醒一次看是否到期。不要改成固定 sleep 24h——那样休眠/唤醒后会错乱。
- **registry 从 `~/.npmrc` 读**，不要硬编码 npmmirror：用户换源后检测结果必须跟着变。
- **托盘图标现在直接用应用 Logo**（harness 状态 × 任务栏主题共 4 张）：彩色 = 运行中，灰度 = 未启动。
  之前试过"扁平单色剪影"，但剪影在 20px 下只是一坨、读不出形状，用户判定不可用。
  状态由 `HARNESS_RUNNING`、任务栏主题由 `TASKBAR_LIGHT`（两个 `AtomicBool`）承载；
  一个每 10 秒的线程同时探测 3080 端口与注册表主题，任一翻转才重绘并 `emit("harness-state")`。
  那次比较必须用**非短路**的 `|`，否则第二个 swap 会被跳过。改图标跑 `tools/make_icons.py build`，
  不要手工替换 `icons-tray/` 里的文件。
- **任务栏主题看 `SystemUsesLightTheme`，不是 `AppsUseLightTheme`**：Win11 默认任务栏深色、应用浅色，
  看错那个就会选错图标（本机就是 taskbar=dark / apps=light）。
- **应用图标源图是 v2（`tools/source/logo-v2.png`）**：它铺了一层**假的"透明棋盘格"**（255 与 247–250 交替），右下角还有水印。
  `clean_artwork()` 两步清理：把近白**中性色**拍平成纯白（去棋盘格，中性判据保护彩色像素）；在右下角把水印破坏的地方补回来。
  水印是**"白色填充 + 浅灰描边"**的文字——白背景上只有灰描边可见（→ 变白），压在深蓝边框上会把边框冲淡（→ 补回边框色）。
  裁剪框 `APP_CROP = (90, 90, 1885)` 是量出来的窗口边缘，不要凭感觉改。
- **圆角半径用 22%**：半径按边长比例算，5% 在 1024px 下是 56px 看着挺圆，
  但缩到 **48px 只剩 2.6px、32px 只剩 1.8px**，抗锯齿会把它抹平成直角——用户看到的就是"图标没圆角"。
  这不是白色背景的问题。**验证方法**：读 `icon.ico` 各尺寸的角落 alpha，必须都是 0。
- **面板必须脱离 harness 的 Job Object**（重要，见「已知陷阱」）。
- **用量聚合放在 Rust 侧**：`/usage/api` 的 `list` action **一次返回约 1.9 MB** 全量记录，
  不能丢给 webview 处理。且用**滚动 24h / N 天窗口**而不是自然日，避免在没有时区库的情况下猜本地时间。
- **用量/余额只在 Web 端运行时刷新，间隔 60 秒**：余额查询会真的打服务商 API（DeepSeek），不能更频繁。
- **插件管理不做"搜索插件"**：DSH 没有官方插件目录，按 npm keywords 搜的结果取决于作者有没有打对 keyword
  （实测 `@feiyang666/dsh-usage-plugin` 打了 `dsh/harness/plugin`，而 `dsh-token-usage-xc` 打的是
  `dsh/deepseek-harness/token/usage/statistics`，没有统一约定）。所以以「输入包名直接装」为主。
- **飞书桥的白名单是硬门槛**：`FEISHU_ALLOWED_USERS` 为空时**拒绝启动**（而不是静默放行）——
  飞书消息在这台机器上等于 shell 执行权限。
- **飞书桥为什么不用 Hermes 那条通道**：Hermes 跑在 Docker 里（有容器隔离，用户嫌不方便），
  而且飞书的 **WebSocket 长连接对同一应用只允许一个消费者**，两边同时接管会互相抢消息。
  所以给 panel 单独建一个飞书应用。
- **`dsh --profile headless` 是一问一答、无会话延续**：所以每条飞书消息都是独立任务，
  不要以为能连续对话；要做多轮得自己加 session 管理。

## 已知陷阱

- **Windows Job Object**：harness 把它启动的子进程放进一个 Job Object，**job 关闭会终止所有成员**。
  所以如果面板是从 harness 的 shell 里启动的，杀掉 harness 会连带杀掉面板 —— **这跟 `taskkill` 用不用 `/T` 无关**
  （第一轮误判成 `/T` 的问题，改了没用；用 `IsProcessInJob` 实测才发现 `inJob=True`）。
  面板启动时会自检，若在 job 中则通过 `explorer.exe` 重新拉起自己（`explorer` 不在该 job 内），
  用 `DSH_PANEL_DETACHED` 环境变量保证只跳一次、避免死循环。**验证方法**：`IsProcessInJob` + 看父进程链。
- **`System.Drawing.Icon($exe, w, h)` 在本机一直失败**，不能用它验证 exe 里的图标；正确做法是从二进制里扒 PNG payload。
- **PowerShell 的 `Get-ChildItem -Recurse` 会少算文件数**（实测 401 vs 真实 402）。要精确计数用 `cmd /c dir /s /b /a-d`。
- **`tools/__pycache__/` 曾经被误提交过**，已在 `.gitignore` 里加 `__pycache__/`。

## 状态

- 版本：0.1.0
- 远程：https://github.com/Lurbing7/dsh-panel （public, MIT）
- 未做的：应用自身自动更新 · 端口可配置 · 升级进度实时输出（目前升级完才一次性返回 npm 输出）·
  内嵌终端只是**只读输出**（要能输入得换 xterm.js + ConPTY）· bundle identifier 以 `.app` 结尾（macOS 会冲突，只做 Windows 没影响）·
  **飞书桥尚未端到端联调**（等用户建好飞书应用、`lark-cli config init` 绑定后才能测）·
  Hermes 网关状态尚未接进面板（Hermes 已实现「飞书 → 本地 AI」，但它跑在容器里）

# AGENTS.md — dsh-panel 项目代理指令

Windows 桌面小面板：常驻托盘，检测 `@deepseek-ai/dsh` 更新 + 一键打开 harness。
技术栈 **Tauri 2 · Rust · Vue 3 + TypeScript**。用户不写代码，改动全部由 AI 完成。

## 常用命令

```bash
npm install
npm run tauri dev              # 开发
npm run build                  # 前端类型检查 + 构建（生成 dist/）
cd src-tauri && cargo check    # 只检查 Rust
npm run tauri build            # 打包
```

**顺序要求**：`cargo build` 之前 `dist/` 必须存在，否则 `tauri::generate_context!()` 编译失败。
改了前端先 `npm run build`。

**`cargo build`（debug）产出的 exe 不能独立运行**（已实测）：debug 构建**不内嵌前端**，
启动后 WebView 去连 `http://localhost:1420`，没跑 `tauri dev` 时就显示
「嗯…无法访问此页面 / localhost 拒绝连接」——看起来像应用坏了，其实是启动方式错了。
要独立运行/长期使用必须 `npm run tauri build`（release 内嵌 dist），再跑 `scripts/install.ps1`。

**图标缓存**：Windows 按**路径**缓存 shell 图标且不看文件时间戳，重编译安装后桌面/任务栏可能仍显示旧图标。
`scripts/install.ps1` 里带了 `ie4uinit -show`，但那**刷不动桌面快捷方式和任务栏按钮**——
彻底的办法是重启 explorer：`Stop-Process -Name explorer -Force` 然后 `Start-Process explorer`。

**`scripts/*.ps1` 一律纯 ASCII**（同 workspace 约定）。

## 关键约束（改代码前必读）

1. **Rust 里调用命令行程序必须走 `cmd /c`**。`Command::new("dsh")` 或 `Command::new("npm")` 在 Windows 上找不到 `.cmd`（`CreateProcess` 不查 PATHEXT）。正确写法是 `Command::new("cmd").arg("/c")...`。
2. **不要用 `tauri-plugin-shell` 的 `open`** —— 它只放行 `http(s)/tel/mailto`，会拦本地命令。本项目所有进程启动都在 Rust 侧用 `std::process::Command`。
3. **窗口关闭 = 隐藏，不是退出**（`on_window_event` 里 `prevent_close` + `hide`）。退出只能走托盘菜单或界面上的"退出"按钮，两者都调 `app.exit(0)`。
4. **dsh 定位靠 PATH**：遍历 `PATH` 找 `dsh.cmd`，其父目录即当前激活的 Node 根目录。**不要硬编码 nvm 路径**——硬编码会在用户 `nvm use` 切换版本后失效。
5. **升级要在对的 Node 下跑**：`cmd /c npm install -g ...`，`current_dir` 设为该 Node 根目录，并把该目录前置到 `PATH`，否则可能用到别的 npm。
6. **子进程创建标志**：一律用 `CREATE_NO_WINDOW` (0x08000000)——npm 升级、启动 harness 都不弹黑框。
   harness 的输出通过管道流进面板内嵌终端（见第 8 条），**不要再弹独立终端窗口**；
   `CREATE_NEW_CONSOLE` (0x00000010) 目前没有使用点，留着备用。
7. **`src-tauri/capabilities/default.json` 只需要 `core:default`**。不要为了加功能往这里塞插件权限——逻辑写在 Rust command 里，前端只管 `invoke`。
8. **前端与 Rust 的接口**（`lib.rs` 里 `invoke_handler` 注册的）：
   - `get_local_info() -> { ok, dsh_version, node_dir, node_version, error }`
   - `check_update() -> { ok, local_version, latest_version, has_update, registry, checked_at, error }`
   - `upgrade_dsh() -> { ok, action, message, output }`
   - `open_harness(port?) -> ActionResult`（旧的"另开窗口"路径，`action` = `"browser"` / `"started"`）
   - `start_harness_inline(port?) -> ActionResult`（**界面现在用这个**，`action` = `"browser"` / `"inline"`）
   - `stop_harness() -> ActionResult`（kill 掉面板启动的那个子进程）
   - `harness_running() -> bool`、`quit_app()`
   事件：`update-checked`(UpdateInfo) · `harness-state`(bool) · `harness-output`(每行字符串)。
   harness 子进程句柄存在 `HARNESS_CHILD`（`OnceLock<Mutex<Option<Child>>>`）：面板退出**不会**杀掉它
   （Windows 上子进程独立存活），只有 `stop_harness` 才 kill。
   改返回结构要**两边一起改**（`lib.rs` 的 struct + `App.vue` 的 interface）。
9. **后端一律不返回 `Err`**，用带 `ok` / `error` 字段的结构体。前端展示更简单，不会出现"命令静默失败"。
10. **端口 3080 写死在两处**：`lib.rs` 的 `DEFAULT_HARNESS_PORT` 和 `App.vue` 的 `HARNESS_PORT`；改要一起改。
11. **`scripts/*.ps1` 一律纯 ASCII**。本机是 Windows PowerShell 5.1，会把无 BOM 的 UTF-8 中文读成 GBK 导致语法错误。

## 设计意图（别"优化"掉这些）

- **"打开 Harness"按钮的语义是"打开"不是"启动"**：先探测 `127.0.0.1:3080` 是否在服务，在服务就开浏览器，避免开出第二个实例。不要简化成无条件启动。
- **harness 必须在面板内启动，不弹独立终端窗口**（用户明确要求）：`start_harness_inline` 用管道接住
  stdout/stderr，经 `harness-output` 事件逐行推给前端，显示在面板下方的可展开终端里。
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
- **插画级线稿不能直接当托盘图标**：线条仅 1–2px 宽，缩到 16–20px 会**完全消失**；
  即使转成剪影，"人物 + 家具"的复合形状在 20px 下也只能读出一个模糊轮廓。
  托盘尺寸只能承载**单一符号**——这是物理限制，不是处理手法问题。不要再试图"把插画缩小"。
- **`tools/source/` 的托盘源图是开放式线稿**（轮廓有缺口）：做剪影必须先形态学闭运算封口再填充，
  否则泛洪会从缺口渗入、填不出东西。
- **应用图标源图是 v2（`tools/source/logo-v2.png`）**：它铺了一层**假的"透明棋盘格"**（255 与 247–250 交替），右下角还有水印。
  `clean_artwork()` 两步清理：把近白**中性色**拍平成纯白（去棋盘格，中性判据保护彩色像素）；在右下角把水印破坏的地方补回来。
  水印是**"白色填充 + 浅灰描边"**的文字——白背景上只有灰描边可见（→ 变白），压在深蓝边框上会把边框冲淡（→ 补回边框色）。
  裁剪框 `APP_CROP = (90, 90, 1885)` 是量出来的窗口边缘，不要凭感觉改。
- **圆角半径必须给足（18%，别用 5%）**：半径按边长比例算，5% 在 1024px 下是 56px 看着挺圆，
  但缩到 **48px 只剩 2.6px、32px 只剩 1.8px**，抗锯齿会把它抹平成直角——用户看到的就是"图标没圆角"。
  这不是白色背景的问题。**验证方法**：读 `icon.ico` 各尺寸的角落 alpha，必须都是 0。

## 状态

- 版本：0.1.0
- 未做的：应用自身自动更新 · 端口可配置 · 升级进度实时输出（目前升级完才一次性返回 npm 输出）·
  内嵌终端只是**只读输出**（要能输入得换 xterm.js + ConPTY）· bundle identifier 以 `.app` 结尾（macOS 会冲突，只做 Windows 没影响）

# dsh-panel

DeepSeek Harness 的 Windows 桌面小面板：常驻托盘，围绕本机 DSH 做**更新检测、启动、监控与管理**。

## 功能

### 更新

- **更新检测**：比较本机安装的 `@deepseek-ai/dsh` 与 npm registry 上的 `latest`
  - 启动时检查一次，之后每 24 小时检查一次
  - 因为托盘常驻，所以"每天 1 次"是真实生效的（不是"每次打开才检查"）
  - registry 跟随 `~/.npmrc`（本机是 `https://registry.npmmirror.com`），也就是你能装到的那个版本
- **一键升级**：在**当前 nvm 激活的那个 Node** 下执行 `npm install -g @deepseek-ai/dsh@latest`

### 启动

面板里启动的都是**在面板内**跑，不弹独立终端窗口，输出实时流进下方的「终端」区：

| 按钮 | 实际执行的命令 |
|---|---|
| **启动 Web 端** | `dsh web --port 3080` |
| **启动桌面端** | 在配置的源码目录执行 `pnpm run dev:desktop`（Electron 桌面端**不随 npm 包分发**，只能从源码跑） |

- **按钮会随状态变红**：Web 端/桌面端在跑时显示为红色的「停止 …」
- 点「停止 Web 端」时，如果 harness **不是面板启动的**（比如你手动在终端起的），面板会用 `netstat` 找到占用 3080 的进程、**确认是 `node.exe` 之后**再停掉它
- 桌面端需要一份 **DeepSeek Harness 源码**，在界面里填（或点「浏览…」选）源码根目录，该目录需已 `pnpm install`；首次启动会构建，比较慢

### 监控（数据来自 harness 内的 dsh-usage-plugin）

- **余额**：显示 DeepSeek 账户余额（实测取到 `CNY 133.66`）
- **用量与消耗**：近 24 小时 / 近 7 天的 token 与花费、按模型拆分、累计调用次数与总花费
- 刷新策略：打开面板时查一次 + **每 60 秒**（仅在 Web 端运行时）；余额查询会真的打服务商 API，所以不更频繁

### 插件管理

- 列出各 profile（`web` / `open-design` / `headless`）的第三方插件，含**实际装到的版本**
- 输入包名安装、卸载、全部升级 —— 本质是 `dsh plugin --profile <p> <add|remove|update>`
- **装/卸后需要重启 Web 端才生效**
- 没做"搜索插件"：DSH 没有官方插件目录，按 npm keywords 搜的结果完全取决于作者有没有打对 keyword（实测两个已装插件就没有统一约定），所以以「输入包名直接装」为主

### 飞书远程控制

在飞书里给机器人发消息 → 本机执行 → 结果回飞书。

```
飞书 App
  ↓ im.message.receive_v1
lark-cli event consume              (NDJSON)
  ↓ 去重（message_id）+ 白名单（open_id）
dsh --profile headless "<消息>"     (宿主机，无容器隔离)
  ↓ 最终回复
lark-cli im +messages-send          → 飞书 App
```

- 面板里有「飞书远程控制」区：运行状态、白名单、profile、启停、日志
- 桥脚本 `scripts/feishu-bridge.mjs` **内嵌进 exe**，运行时释放到 `%APPDATA%\com.dshpanel.app\`
- **需要先自己建一个飞书自建应用**并 `lark-cli config init` 绑定（见下方"飞书桥的前置条件"）
- 两个硬限制：**每条消息是独立任务**（`dsh --profile headless` 一问一答、无会话延续，AI 不记得上文）；**白名单必填**（飞书消息在这台机器上等于执行权限，面板拒绝在空白名单下启动）

### 托盘

- 关闭窗口只是隐藏；左键单击图标打开面板；右键菜单可直接启动 Web 端 / 检查更新 / 退出
- 图标跟随状态与任务栏主题，每 10 秒探测一次，任一变化才重绘
- **忙碌反馈**：检查更新 / 升级 / 启动时按钮上会转圈（npm 装包通常要几十秒，不会让你以为卡死了）

## 怎么定位"本机的 dsh"

不硬编码任何 nvm 路径。做法是**在 `PATH` 上找 `dsh.cmd`**，它所在的目录就是当前激活的 Node 安装根目录（npm 在 Windows 上把全局 shim 直接放在 Node 根目录）。

由此得到：

- dsh 版本 ← `<node根>\node_modules\@deepseek-ai\dsh\package.json`
- Node 版本 ← 把 `<node根>` 软链接解析到底，取目录名（如 `v24.9.0`）
- npm ← `<node根>\npm.cmd`

**所以你 `nvm use` 切了版本，这个应用会自动跟着切**——只要那个版本里装了 dsh。

## 图标

**应用图标**：`src-tauri/icons/`，由 `src-tauri/app-icon.png` 经 `npm run tauri icon` 生成。
源图是 **v2 logo**（`tools/source/logo-v2.png`），圆角半径 **22%**。

**托盘图标**：直接使用 **app logo**（不再是早期的单色剪影）——运行中是彩色，未启动是灰度。灰度靠降低饱和度 + 提亮实现，这样在深/浅任务栏上都能读出来。

|  | 深色任务栏 | 浅色任务栏 |
|---|---|---|
| **未启动** | 灰度 logo | 灰度 logo |
| **运行中** | 彩色 logo | 彩色 logo |

- 主题读注册表 `HKCU\...\Themes\Personalize\SystemUsesLightTheme`。**注意不是**应用主题 `AppsUseLightTheme`——Win11 默认任务栏深色而应用浅色，看错会选错图标
- 4 个组合共 4 张 PNG，编译时内嵌进 exe

> **圆角为什么是 22%**：早期用 5.5%（1024px 下 56px），缩到 48px 只剩 2.6px、缩到 32px 只剩 1.8px，**抗锯齿直接把它抹掉了**，看起来就是没圆角。放大到 22% 后各尺寸的角上 alpha 才稳定可见。

源图在 `tools/source/`，生成脚本：

```powershell
python tools\make_icons.py preview              # 对比预览图（tools/preview/，已 gitignore）
python tools\make_icons.py build                # 托盘 png + 应用图标源图 app-icon.png
python tools\make_icons.py backup               # 圆角化 logo 备份到下载目录（单文件，只读源图）
npm run tauri icon src-tauri\app-icon.png       # 从 app-icon.png 生成全套应用图标
```

一键重建 + 安装（**推荐用这个**，理由见下方"改图标为什么必须清缓存"）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1            # 含重新生成图标
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipIcons # 只改代码时用
```

### 两处必须知道的图像处理

**① v2 logo 要清掉假背景和水印。** `clean_artwork()` 做两件事：
- 源图铺了一层**假的"透明棋盘格"**（255 和 247–250 交替）→ 把接近白色的**中性色**像素拍平成纯白（中性判据保证头发、花边这些彩色像素不受影响）
- 右下角有豆包水印，它是**"白色填充 + 浅灰描边"**的文字——压在白色背景上只有灰描边可见，压在深蓝边框上则把边框冲淡 → 分两路处理：灰描边直接变白，被冲淡的蓝色描边补回边框色

裁剪框 `APP_CROP = (90, 90, 1885)` 是从源图量出来的窗口边缘，圆角由 `rounded()` 施加。

**② 改图标必须清 cargo 的 build-script 缓存。** `tauri-build` **不会**因为 `icons/icon.ico` 变了就重跑它的 build script，所以直接 `npm run tauri build` 会**继续把旧图标打进 exe**（`cargo clean -p dsh-panel` 也不够）。必须删掉：

```
src-tauri\target\release\build\dsh-panel-*
src-tauri\target\release\.fingerprint\dsh-panel-*
```

`build-and-install.ps1` 已经包含这一步。另外 Windows 的**外壳图标缓存按路径**记，`ie4uinit.exe -show` 往往不够，必要时得重启 explorer 并删除 `%LOCALAPPDATA%\Microsoft\Windows\IconCache\*`（实际路径是 `...\Explorer\iconcache*`）。

## 技术栈

| 层 | 用什么 |
|---|---|
| 壳 | Tauri 2 |
| 后端 | Rust —— 版本探测、registry 查询、子进程启动、托盘、用量聚合 |
| 前端 | Vue 3 + TypeScript |
| HTTP | reqwest（rustls，不依赖系统 OpenSSL） |
| 版本比较 | semver |
| 目录选择 | tauri-plugin-dialog |

选 Tauri 而不是 Electron 的原因：本机已有 Tauri 项目跑通（`D:\projects\desktop-toolbox`），Rust 工具链就绪，产物是单 exe、体积小；而这个应用要做的事（起进程、查 HTTP、读文件）正好都在 Rust 侧。

## 安装（日常使用）

已经装好了，日常直接用快捷方式：

- **桌面 / 开始菜单**：`DSH Panel`
- **实际位置**：`%LOCALAPPDATA%\Programs\dsh-panel\dsh-panel.exe`
- **开机自启**：启动文件夹里有快捷方式，登录后自动进托盘（「每天检查 1 次」靠它才生效）

重新编译后重装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipIcons
```

不要自启就加 `-Autostart:$false`。

> ⚠️ **别直接跑 `src-tauri\target\debug\dsh-panel.exe`**：debug 构建**不内嵌前端**，启动后
> WebView 会去连 `http://localhost:1420`（`tauri dev` 起的 Vite 服务），没跑 dev server 时
> 就显示「嗯…无法访问此页面 / localhost 拒绝连接」。要独立运行必须用 **release** 构建。

## 飞书桥的前置条件

1. 在[飞书开放平台](https://open.feishu.cn/)建**企业自建应用** → 启用**机器人**
2. **连接方式选「长连接（WebSocket）」** —— 不需要公网 IP / 内网穿透
3. 权限：`im:message.p2p_msg:readonly` + `im:message:send_as_bot`
4. 事件订阅启用 **`im.message.receive_v1`**
5. **发布版本**（自建应用必须发布才生效）
6. 绑定：

```powershell
lark-cli config init --app-id <你的AppID>
```

7. 在面板的「飞书远程控制」里填白名单 open_id（`ou_` 开头，逗号分隔）→ 启动桥

> 为什么不用本机已有的 Hermes 飞书网关：Hermes 跑在 Docker 里，有容器隔离；而且飞书的
> **WebSocket 长连接对同一个应用只允许一个消费者**，两边同时接管会互相抢消息。所以给面板
> **单独建一个应用**，两个通道互不干扰。

## 运行

```bash
npm install
npm run tauri dev             # 开发（热重载）
npm run build                 # 只做前端类型检查 + 构建
cd src-tauri && cargo check   # 只检查 Rust
npm run tauri build           # 打包 release exe / 安装包
```

打包产物在 `src-tauri/target/release/`（exe）与 `.../bundle/`（msi / nsis 安装包）。

## 目录结构

```
src/                    Vue 前端（App.vue 界面、style.css）
src-tauri/
  src/lib.rs            全部后端逻辑：版本探测、更新、启动子进程、托盘、用量聚合、插件管理、飞书桥
  src/main.rs           入口
  tauri.conf.json       窗口与打包配置
  capabilities/         权限（core:default + dialog:default）
  icons-tray/           4 张托盘 PNG（状态 × 任务栏主题）
scripts/
  build-and-install.ps1 一键重建 + 安装（含清 cargo 图标缓存）
  install.ps1           复制 exe、建快捷方式、刷图标缓存
  feishu-bridge.mjs     飞书 → dsh 的桥（内嵌进 exe）
tools/
  make_icons.py         图标生成（preview / build / backup）
```

## 已知边界

- **Windows Job Object（重要）**：harness 把它启动的子进程放进一个 Job Object，**job 关闭会终止所有成员**。所以如果面板是从 harness 的 shell 里启动的，杀掉 harness 会连带杀掉面板 —— 这跟 `taskkill` 用不用 `/T` 无关。面板启动时会用 `IsProcessInJob` 自检，若身处 job 中则通过 `explorer.exe` 重新拉起自己（并用 `DSH_PANEL_DETACHED` 保证只跳一次）。
- 升级后**必须重启正在运行的 harness** 才生效——升级的是它自己。如果 harness 正在跑，Windows 上文件可能被占用，npm 会报 `EBUSY`/`EPERM`，应用会把这种情况翻成一句人话提示。
- 应用自身不自动更新，只检测 dsh。
- 端口写死 `3080`（改的话是 `src-tauri/src/lib.rs` 的 `DEFAULT_HARNESS_PORT` 和 `src/App.vue` 的 `HARNESS_PORT`，两处要一起改）。
- `/usage/api` 由 harness 内的插件提供，**无需认证**（根路径是 401，插件路由豁免）。这是插件作者有意为外部客户端留的口子。
- 用量里的"近 24 小时 / 近 N 天"是**滚动窗口**，不是自然日 —— 避免在没有时区库的情况下猜本地时间。

## License

MIT —— 见 [LICENSE](LICENSE)。

# dsh-panel

DeepSeek Harness 的 Windows 桌面小面板：常驻托盘，只做两件事——**告诉你 dsh 能不能更新**、**一键打开 harness**。

## 功能

- **更新检测**：比较本机安装的 `@deepseek-ai/dsh` 与 npm registry 上的 `latest`
  - 启动时检查一次，之后每 24 小时检查一次
  - 因为托盘常驻，所以"每天 1 次"是真实生效的（不是"每次打开才检查"）
  - registry 跟随 `~/.npmrc`（本机是 `https://registry.npmmirror.com`），也就是你能装到的那个版本
- **一键升级**：在**当前 nvm 激活的那个 Node** 下执行 `npm install -g @deepseek-ai/dsh@latest`
- **打开 Harness**：先在 `127.0.0.1:3080` 探测
  - 已经在服务 → 直接打开浏览器（不会开出第二个实例）
  - 没在服务 → 新开一个终端窗口跑 `dsh web --port 3080`
- **托盘常驻**：关闭窗口只是隐藏；左键单击图标打开面板；右键菜单可直接打开 harness / 检查更新 / 退出

## 怎么定位"本机的 dsh"

不硬编码任何 nvm 路径。做法是**在 `PATH` 上找 `dsh.cmd`**，它所在的目录就是当前激活的 Node 安装根目录（npm 在 Windows 上把全局 shim 直接放在 Node 根目录）。

由此得到：

- dsh 版本 ← `<node根>\node_modules\@deepseek-ai\dsh\package.json`
- Node 版本 ← 把 `<node根>` 软链接解析到底，取目录名（如 `v24.9.0`）
- npm ← `<node根>\npm.cmd`

**所以你 `nvm use` 切了版本，这个应用会自动跟着切**——只要那个版本里装了 dsh。

## 图标

**应用图标**：`src-tauri/icons/`，由 `src-tauri/app-icon.png` 经 `npm run tauri icon` 生成。
源图是 **v2 logo**（`tools/source/logo-v2.png`，蓝白细边框窗口 + 圆点 + 搜索栏，人物更大）。

**托盘图标**：两张，按 harness 是否在运行自动切换，每 10 秒探测一次 `127.0.0.1:3080`（只在状态翻转时重绘并通知界面）：

| 状态 | 图标 |
|---|---|
| 未启动 | 暗灰蓝圆底 + 白色躺姿剪影（躺着喝茶） |
| 运行中 | 亮蓝圆底 + 白色坐姿剪影（在打字） |

颜色 + 形状**双重区分**；加圆底是因为白色剪影放在浅色任务栏上会隐形。

源图在 `tools/source/`，生成脚本：

```powershell
python tools\make_icons.py preview              # 对比预览图（tools/preview/，已 gitignore）
python tools\make_icons.py build                # 托盘 ico/png + 应用图标源图 app-icon.png
python tools\make_icons.py backup               # 圆角化 logo 备份到下载目录（单文件，只读源图）
npm run tauri icon src-tauri\app-icon.png       # 从 app-icon.png 生成全套应用图标
```

### 两处必须知道的图像处理

**① 托盘线稿要先"封闭轮廓"再填充。** `tools/source/tray-*.png` 是开放式线稿（头发与脸、手与杯子的交界处轮廓有缺口），直接泛洪填充会从缺口渗进去、填不出剪影 → 先做**形态学闭运算**（膨胀封口再腐蚀还原）。而且线稿线条只有 1–2 像素宽，直接缩到 16px 会**完全消失**，必须转成实心剪影。

**② v2 logo 要清掉假背景和水印。** `clean_artwork()` 做两件事：
- 源图铺了一层**假的"透明棋盘格"**（255 和 247–250 交替）→ 把接近白色的**中性色**像素拍平成纯白（中性判据保证头发、花边这些彩色像素不受影响）
- 右下角有豆包水印，它是**"白色填充 + 浅灰描边"**的文字——压在白色背景上只有灰描边可见，压在深蓝边框上则把边框冲淡 → 分两路处理：灰描边直接变白，被冲淡的蓝色描边补回边框色

裁剪框 `APP_CROP = (90, 90, 1885)` 是从源图量出来的窗口边缘，圆角由 `rounded()` 施加。

## 技术栈

| 层 | 用什么 |
|---|---|
| 壳 | Tauri 2 |
| 后端 | Rust —— 版本探测、registry 查询、子进程启动、托盘 |
| 前端 | Vue 3 + TypeScript |
| HTTP | reqwest（rustls，不依赖系统 OpenSSL） |
| 版本比较 | semver |

选 Tauri 而不是 Electron 的原因：本机已有 Tauri 项目跑通（`D:\projects\desktop-toolbox`），Rust 工具链就绪，产物是单 exe、体积小；而这个应用要做的事（起进程、查 HTTP、读文件）正好都在 Rust 侧。

## 安装（日常使用）

已经装好了，日常直接用快捷方式：

- **桌面 / 开始菜单**：`DSH Panel`
- **实际位置**：`%LOCALAPPDATA%\Programs\dsh-panel\dsh-panel.exe`
- **开机自启**：启动文件夹里有快捷方式，登录后自动进托盘（「每天检查 1 次」靠它才生效）

重新编译后重装（会替换 exe，保留快捷方式与自启设置）：

```powershell
npm run tauri build
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart
```

不要自启就把 `-Autostart` 换成 `-Autostart:$false`。

> ⚠️ **别直接跑 `src-tauri\target\debug\dsh-panel.exe`**：debug 构建**不内嵌前端**，启动后
> WebView 会去连 `http://localhost:1420`（`tauri dev` 起的 Vite 服务），没跑 dev server 时
> 就显示「嗯…无法访问此页面 / localhost 拒绝连接」。要独立运行必须用 **release** 构建，
> 即 `npm run tauri build`。

## 运行

```bash
npm install
npm run tauri dev          # 开发（热重载）
npm run build              # 只做前端类型检查 + 构建
cd src-tauri && cargo check  # 只检查 Rust
npm run tauri build        # 打包 release exe / 安装包
```

打包产物在 `src-tauri/target/release/`（exe）与 `.../bundle/`（msi / nsis 安装包）。

## 目录结构

```
src/                 Vue 前端（App.vue 界面、style.css）
src-tauri/
  src/lib.rs         全部后端逻辑：版本探测、更新检查、升级、开 harness、托盘
  src/main.rs        入口
  tauri.conf.json    窗口与打包配置
  capabilities/      权限（只有 core:default，逻辑都在 Rust，不需要额外插件权限）
```

## 已知边界

- 升级后**必须重启正在运行的 harness** 才生效——升级的是它自己。如果 harness 正在跑，Windows 上文件可能被占用，npm 会报 `EBUSY`/`EPERM`，应用会把这种情况翻成一句人话提示。
- 应用自身不自动更新，只检测 dsh。
- 端口写死 `3080`（改的话是 `src-tauri/src/lib.rs` 的 `DEFAULT_HARNESS_PORT` 和 `src/App.vue` 的 `HARNESS_PORT`，两处要一起改）。

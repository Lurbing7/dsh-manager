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

## 技术栈

| 层 | 用什么 |
|---|---|
| 壳 | Tauri 2 |
| 后端 | Rust —— 版本探测、registry 查询、子进程启动、托盘 |
| 前端 | Vue 3 + TypeScript |
| HTTP | reqwest（rustls，不依赖系统 OpenSSL） |
| 版本比较 | semver |

选 Tauri 而不是 Electron 的原因：本机已有 Tauri 项目跑通（`D:\projects\desktop-toolbox`），Rust 工具链就绪，产物是单 exe、体积小；而这个应用要做的事（起进程、查 HTTP、读文件）正好都在 Rust 侧。

## 运行

```bash
npm install
npm run tauri dev          # 开发（热重载）
npm run build              # 只做前端类型检查 + 构建
cd src-tauri && cargo check  # 只检查 Rust
npm run tauri build        # 打包 exe / 安装包
```

打包产物在 `src-tauri/target/release/bundle/`。

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

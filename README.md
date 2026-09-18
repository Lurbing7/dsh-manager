# DSH Manager

DeepSeek Harness 的 Windows 桌面管理器：常驻托盘，把**更新、启动、用量、插件、远程控制**收在一个窗口里。

> 原名 `dsh-panel`。功能早已超出「面板」——用量分析、插件管理、飞书桥、Hermes 状态——因此改名为 `dsh-manager`。旧仓库地址由 GitHub 自动重定向。

## 下载

从 [Releases](https://github.com/Lurbing7/dsh-manager/releases) 取最新版：

| 文件 | 说明 |
|---|---|
| `dsh-manager_<版本>_x64-setup.exe` | **推荐**，NSIS 安装包 |
| `dsh-manager_<版本>_x64_en-US.msi` | MSI 安装包 |

装完在桌面／开始菜单有 `DSH Manager` 快捷方式，可选开机自启（只进托盘）。

## 界面

```
┌──────────────────────────────────────────────────────────────┐
│ ● Dashboard  [已是最新]                                刷新   │
├────────────────┬────────────────┬────────────────────────────┤
│ 本月花费        │ 剩余余额        │ Web 端                     │
│ ¥141.35        │ CNY 122.69     │ 运行中 · 打开网页 →        │
├────────────────┴────────────────┴────────────────────────────┤
│ 消耗趋势                              [近14天][近8周][近12月]  │
│   （曲线图，空心点 = 该区间没有数据）                          │
├──────────────────────────────────────────────────────────────┤
│ 用量分析    [近7天][近30天][近90天][全部]                      │
│   指标卡 · token 六桶 · 模型饼图 · 模型明细表 · 会话排行        │
└──────────────────────────────────────────────────────────────┘
  ⚙（左下：设置抽屉）                        ▶/■（右下：启停）
```

**左下悬浮按钮**打开设置抽屉，里面依次是：运行环境（版本 + 检查更新/一键升级）、
DeepSeek API Key、桌面端（源码目录 + 启停）、插件管理、飞书远程控制、Hermes 网关、终端输出、退出。

## 功能

### 更新

- **更新检测**：比较本机 `@deepseek-ai/dsh` 与 registry 的 `latest`；启动时一次 + 每 24 小时一次
- **一键升级**：在**当前 nvm 激活的那个 Node** 下执行 `npm install -g @deepseek-ai/dsh@latest`，
  所以 `nvm use` 切了版本，它自动跟着切（做法是遍历 `PATH` 找 `dsh.cmd`，其父目录即当前 Node 根目录）

### 启动

面板里启动的都在**面板内**跑，不弹独立终端窗口，输出实时流进抽屉里的「终端输出」：

| 按钮 | 实际执行的命令 |
|---|---|
| **启动 Web 端** | `dsh web --port 3080` |
| **启动桌面端** | 在配置的源码目录执行 `pnpm run dev:desktop`（Electron 桌面端**不随 npm 包分发**，只能从源码跑） |

- 运行中的按钮变红「停止」；点停止时若 harness **不是面板启动的**，会用 `netstat` 找到占用 3080 的进程、
  **确认是 `node.exe`** 之后再停
- 桌面端需要一份 DeepSeek Harness 源码（目录需已 `pnpm install`），首次启动会构建，比较慢

### 用量与余额（**不依赖任何 DSH 插件**）

这一点是刻意设计的：插件在 dsh 升级后可能导致 harness 起不来，所以面板把需要的功能都做进了自身。

- **余额**：读 `~/.dsh/.credentials.yaml` 的 key，或使用你在设置里填的 key（面板内的优先），
  直连 `https://api.deepseek.com/user/balance`
- **用量**：**直接解析 harness 的会话日志**（`~/.dsh/sessions/**/session.v3.jsonl.zstd`）——
  自己解多帧 zstd、解析 `assistant/message` 事件、按本地日历日聚合；启动时扫一次 + 每 5 分钟一次
- 也可从**已安装的用量插件**一次性导入历史（那是唯一带价格图标的来源）

**口径说明**：

- `cache_write` 不计入总量 —— 记录器把它与「未命中」记成同一个值，相加会重复计算
- 会话日志**不含价格**，所以由解析得出的天数只统计 token；花费金额来自插件导入的那部分
- 「近 N 天」是**本地日历日**（不是滚动 24 小时窗口）

### 插件管理

- 列出各 profile 的第三方插件与实际版本，支持安装／卸载／全部升级
- 本质是 `dsh plugin --profile <p> <add|remove|update>`，转发给该 profile 目录里的 pnpm
- 装/卸后需重启 Web 端才生效

### 飞书远程控制

在飞书里给机器人发消息 → 本机执行 → 结果回飞书。

```
飞书 App
  ↓ 扫码配对（一次性）
飞书自动创建应用 + 配事件 + 授权限
  ↓
lark-cli event consume im.message.receive_v1   (NDJSON，WebSocket 长连接)
  ↓ 去重（message_id）+ 白名单（open_id）
dsh --profile headless "<消息>"
  ↓
lark-cli im +messages-send  →  飞书 App
```

**配对只需扫码**：设置抽屉里点「扫码配对飞书」→ 弹出二维码 → 用飞书扫一下。
飞书会**自动创建一个属于你的应用**并把凭据交回面板；面板随即绑定 `lark-cli`
并**把返回的 `open_id` 直接填进白名单** —— 你不需要去开放平台做任何配置，
也不需要自己找 open_id。

> 用的是飞书的 device-code 注册流程（`POST https://accounts.feishu.cn/oauth/v1/app/registration`），
> 与飞书移动端同一套机制。
>
> 若本机有 Hermes 环境，`lark-cli config init` 会**默认拒绝**（它希望你绑定 Agent 已有的应用），
> 面板会带 `--force-init` 显式声明要一个独立应用。

### 其他

- **一键打开网页**：Web 端在跑时信息卡上有入口，关了浏览器标签也能回去
- **Hermes 网关状态**（只读）：显示 `gateway_state` 与各平台连通性，一键打开它的 9119 面板

## 技术栈

| 层 | 用什么 |
|---|---|
| 壳 | Tauri 2 |
| 后端 | Rust —— 版本探测、registry 查询、子进程管理、托盘、会话日志解析 |
| 前端 | Vue 3 + TypeScript（图表为手写 SVG，无图表库） |
| HTTP | reqwest（rustls） |
| 时间 | chrono（本地日历日与 ISO 周） |
| 压缩 | zstd（会话日志是多帧 zstd） |
| 二维码 | qrcode（前端生成） |

## 开发

```bash
npm install
npm run tauri dev             # 开发（热重载）
npm run build                 # 前端类型检查 + 构建
cd src-tauri && cargo check   # 只检查 Rust
npm run tauri build           # 打包 release
```

一键重建 + 安装（含清 cargo 图标缓存、卸旧版、刷图标缓存）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1            # 含重新生成图标
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipIcons # 只改代码时用
```

**CLI 诊断**（GUI 子系统没有控制台，输出写文件）：

```powershell
dsh-manager.exe --scan-sessions [n]              # 看会话日志的事件类型分布与 usage 采样
dsh-manager.exe --scan-sessions-apply [days]     # 真正刷新本地用量库
dsh-manager.exe --dump-session <file.zstd> [out] # 解压会话日志（多帧 zstd）
```

输出在 `%TEMP%\dsh-panel-*.txt`。

## 目录结构

```
src/
  App.vue                 Dashboard + 设置抽屉 + 悬浮按钮
  components/
    UsageChart.vue        消耗趋势曲线（手写 SVG）
    UsageAnalysis.vue     指标卡 / 饼图 / 模型明细表 / 会话排行
scripts/
  build-and-install.ps1   一键重建安装
  install.ps1             复制 exe、建快捷方式、清旧版
  feishu-bridge.mjs       飞书 → dsh 的桥（内嵌进 exe，运行时释放）
src-tauri/src/lib.rs      全部后端逻辑
tools/make_icons.py       图标生成
```

## 已知限制

- **飞书桥的端到端验证**依赖你的飞书账号（扫码配对后的真实收发）
- 会话日志格式随 DSH 版本演进；解析器对未知事件类型是**跳过**而非报错
- 应用自身不自动更新（只检测 dsh）
- 端口固定 3080
- bundle identifier 仍是 `com.dshpanel.app`（改名时**故意没动** —— 那是 app-data 目录，
  本地用量库存在里面；改了会看起来像历史全丢）

## License

MIT —— 见 [LICENSE](LICENSE)。构建于 Tauri 2 + Rust + Vue 3。

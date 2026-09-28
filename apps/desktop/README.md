# Gunther Desktop

Gunther Desktop 是本地优先知识工作区的深度工作端：负责 Capture、Inbox 整理、Library 阅读与问答、用户审核，以及持久 Output 历史。技术上由 React/Vite 界面、Tauri 2 壳和私有 Python sidecar 组成。

## 运行

仓库根目录：

```bash
npm install
uv sync --project apps/backend
npm run dev
```

只启动桌面或浏览器界面：

```bash
npm run dev:desktop
npm run dev:desktop:web
```

验证：

```bash
npm run test:desktop
npm run typecheck -w @gunther/desktop
npm run build:desktop:web
npm run check:desktop
```

macOS 原生退出保护回归（需要图形桌面；使用独立空白窗口，不启动后端、不访问用户知识库、不录制麦克风）：

```bash
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --features native-smoke --test native_quit_guard
```

原生构建：

```bash
npm run build:desktop
```

最终回归：Desktop 72 passed，TypeScript typecheck 与 Vite production build 通过；Rust 壳 10 passed，原生构建通过；Chrome UI→backend E2E 32/32；冻结 Helper 随机 loopback 启动 1 passed。macOS 候选为 `artifacts/releases/Gunther_0.1.0_macOS_arm64.dmg`，33,658,539 bytes，SHA-256 `98684b1da7b4728b9c3e94e3a0687cdb8f30fb6cf8dff5006631ad67150a641d`。

## 产品结构

一级导航（侧栏）：

- **Home**：以搜索为首页。输入框直接检索全部本地知识（Notes、已接受知识、Sources 与会话，包括尚在 Inbox 中未归类的采集）；输入 `@` 可选择一个或多个 Library 限定范围，选中单个 Library 时可一键把问题交给该 Library 的 Ask。`Web` 开关按需加入在线研究。下方是一键 Capture 与最近的 Libraries / 采集；
- **Libraries**：长期主题空间，每个 Library 有自己的颜色与首字母标识；
- **Inbox**：未归类 Source/Note、失败项和待审核建议；
- **Capture**：标题栏全局动作，不是另一套导航。

`⌘K` 或 `/` 在任意页面回到 Home 搜索。侧栏同时列出你的 Libraries，便于直接切换。

**详情页**：Inbox 中的每一项、Home 搜索结果、最近采集和 Library 的 Sources 都可以点开成独立页面，并按类型呈现——笔记（Markdown 阅读/书写，可勾选任务）、录音（播放器、标记时刻、概览与可点击时间戳的转写）、网页（站点卡片与捕获正文）、文档（按页重建的正文或原始 Markdown/CSV/文本）、照片（大图查看与可定位的识别文字）、表格（数据网格）、知识建议（提议内容与来源会话）。右侧决策栏负责归档、审核与接受（⌘↵），完成后自动进入下一项；J/K 上下切换，Esc 返回。

**Markdown**：渲染使用 react-markdown + remark-gfm（原始 HTML 只显示为文本，远程图片不加载）；书写使用 CodeMirror 6（首次书写时才加载）。

**回收站**：Inbox 行、详情页工具栏与 Library 设置都可以“移到回收站”（⌘⌫），提示条可撤销（⌘Z）。回收站中的内容不出现在 Inbox、搜索与 Ask，可随时恢复；30 天后或“永久删除”时才真正删除，原件只在不再被其他来源使用时移除。

**快捷键**：统一注册表驱动匹配、提示与 ⌘/ 快捷键一览；桌面版中 ⌘N、⌘⇧C、⌘⇧R、⌘K、⌘,、⌘/、⌘⇧L 由原生菜单负责。

界面遵循 [Gunther Design System](../../docs/DESIGN_SYSTEM.md)：白底黑字，颜色只用于 Library、来源类型与状态标识；设计 token 与组件样式位于 `src/design/`。

Notebook / Note 是快速记录编辑界面，不是临时 Library。未归类 Note 在 Inbox 中出现；归类时提升为 Library 的 Source。

## Capture

Capture 打开即可书写（默认笔记），类型栏 ⌘1–6 切换且保留已输入内容；粘贴链接或表格会提示切换类型，文件可拖放到窗口任意位置；底栏选择 Inbox 或 Library，⌘↵ 保存，Esc 隐藏到菜单栏。菜单栏图标是 Gunther 的“G”标记，只在采集时改变（录音红灯与计时、暂停琥珀双竖线），菜单只列出当前可用的动作；设置中可选择“仅在采集时显示”。详见 [独立 Capture 与菜单栏设计](../../docs/CAPTURE_MENU_BAR_DESIGN_CN.md)。

统一 Capture 支持：

- Quick note；
- 粘贴文本或表格；
- Document / PDF / image；
- Link 不可变网页快照；
- Web search；
- 实时 Recording；
- 导入已有音频。

文件原件、OCR 与网页快照由后端持久化。纯文本类采集在服务暂时不可用时保留小型本地重试副本，并绑定已验证 workspace；文件/录音使用各自的 durable 原件路径，不把大文件塞进 localStorage。

## Recording workspace

录音界面支持实时转写、暂停/恢复、可编辑 transcript、重点时刻、lecture/meeting/memo 场景、checkpoint、完成与独立 Capture 窗口后台隐藏。主窗口和 Capture 窗口的关闭按钮只隐藏窗口；菜单栏仍可重新打开、暂停、标记或结束同一个录音，不会创建第二个会话。

`Cmd+Q` / 应用菜单 Quit 与状态栏 Quit 共用同一退出检查：录音、暂停、导入、收尾保存或未保存 Capture 时保留进程，并重新显示 Capture 提示；完成保存或保留为草稿、释放会话后才正常退出。macOS 的默认 `PredefinedMenuItem::quit` 会直接执行 AppKit `terminate:`，当前 Tauri runtime 不会为该路径触发可取消的 `ExitRequested`，因此应用菜单必须使用带 `CmdOrCtrl+Q` 快捷键的普通自定义菜单项。不要恢复默认 Quit，也不要再为状态栏单独注册第二次菜单处理器。系统 Force Quit、终止开发进程以及 Dock 的系统 Quit 不属于此菜单保护路径，不可用于保持录音。

Capture WebView 配置了 `backgroundThrottling: "disabled"`，但 WebKit 只在 macOS 14+ 提供这项控制；即使在 14+，它也只是调度策略，不是音频持久性保证。它不能作为 macOS 11–13 隐藏窗口连续录音的保证；若继续支持这些系统，成熟版本必须把麦克风采集、分块写入与录音时钟移出 WebView，放到主应用的原生音频层。当前最低系统版本仍为 11.0，因此发布验收必须明确区分这条边界。

macOS 包内已包含 `NSMicrophoneUsageDescription` 和 `com.apple.security.device.audio-input=true`。首次录音仍必须由用户点击开始并在 TCC 提示中授权；界面最多等待 15 秒，结束等待时清理计时状态，未获授权则显示系统设置路径和 Retry。此前安装版已实际触发 TCC 提示，但用户未授权，因此不能据此声称最终包已经完成真实麦克风采集验收。

### 实时录音

- 未确认 chunk 先写 IndexedDB；
- chunk 保存 size、SHA-256、workspace、source 与 sequence；
- 服务端 ACK 后才删除对应本地 chunk；
- IndexedDB 不可用或写入失败时停止录音，不回退易失内存；
- STT WebSocket 中断不影响音频 chunk 保存。

### 导入已有音频

- 文件上限 2 GB；
- 初始阶段按块复制到 app-owned IndexedDB，并为每块计算 SHA-256；
- manifest 固定文件大小、块数、content type、workspace 与 RecordingSession；
- 完整 staging 后从服务端 `nextExpectedSequence` 继续上传；
- 崩溃/重启后无需重选原文件，可根据服务端确认 offset 和剩余 durable chunks 完成；
- 若进程在初始完整 staging 之前退出，仍需重选原文件重新开始，不完整残片不会被当作完整导入。

## Evidence 与 Outputs

Library 的 Ask 只使用当前 Library / selected Sources，并显示 citation snapshots。若 Source 尚无可用的结构化 Assertion，Ask 会从保存的原始正文检索有限相关段落，并以 `assertionId=null` 的 provisional citation 回答；不足以匹配时仍显示证据缺口。回答可以提出 Knowledge Proposal；只有用户接受后才形成 trusted KnowledgeUnit revision。

Outputs 当前读取后端持久 Artifact history：

- 列出全部不可变版本；
- 打开历史正文；
- 显示 pinned revision、Proposal/Session/Message provenance 与 evidence count；
- 只从当前 lineage head 生成下一版；
- 409 stale-head 后刷新历史并恢复 UI；
- 复制/下载持久 Markdown；
- Library workbook v3 导出包含 Artifact 详情。

Artifact 尚未加入全局 Search，也没有 archive 或 PDF/DOCX renderer。

## 打包 sidecar

release build 冻结 Python backend，Tauri 启动时：

1. 生成 launch nonce；
2. 启动 Helper 并预绑定 loopback；
3. Helper 打开私有数据目录和单实例 lock；
4. 创建本次 256-bit token；
5. 以 `backend-ready/backend-auth-token.<launchNonce>` 原子发布 nonce+token；
6. Tauri 只接受匹配本次 nonce 的 ready 文件；
7. HTTP / WebSocket 使用 token 并校验 Origin；
8. App 退出时终止 Helper 并删除 ready token。

8787 已被占用时不会把数据发给占位进程。不要关闭认证或把 sidecar 改成 LAN 服务。

macOS 默认数据目录：

```text
~/Library/Application Support/com.gunther.knowledge/
```

可选 `.env` 可放入该目录，但其中 key 应单独加密备份。业务 backup 不复制 `.env`、日志、ready token 或 mobile gateway 私钥。

## Mobile gateway

物理手机通过独立的私网 HTTPS gateway：

- 私有 CA / leaf；
- 一次性短时 pairing code；
- device bearer 与 revoke；
- workspace identity 与 scope；
- 移动 secure storage 和证书/fingerprint 校验。

桌面 Settings 提供 gateway 状态与配对入口。源码/自动化完成不等于跨物理手机验收；仍需真实设备验证安装、发现、证书、撤销、网络切换与长时录音。

## 目录

```text
src/
  App.tsx                     主导航、Capture 与 workspace 协调
  design/                    设计 token、基础组件、外壳与页面样式（最后加载）
  components/                Capture、Recording、Settings
  components/search/         搜索输入（@ Library）、结果与检索 hook
  pages/                     Home（搜索）、Libraries、Inbox、Note、Library workspace
  services/recordingSpool.ts IndexedDB durable chunks
  api.ts                     typed backend client
src-tauri/
  src/lib.rs                 Helper 生命周期与私有 token handoff
  src/capture_shell.rs       Capture 单例窗口、菜单栏状态与安全退出
  capabilities/              Tauri 权限
  tauri.conf.json            CSP、窗口与 bundle
```

## 发布边界

源码构建成功不能替代：

- Developer ID、notarization、Gatekeeper 与干净机器；
- 用户实际授予麦克风权限后的原生录音、文件权限与音质；
- 真实连续长时录音与强杀/休眠/断网/磁盘压力；
- Intel/universal（若产品承诺）；
- 正式签名 provenance 与公开分发验收。

当前 DMG 已由最终源码生成：App/Helper 均为 arm64，`LSMinimumSystemVersion=11.0`；ad-hoc `codesign --verify --deep --strict`、DMG verify 和只读挂载通过。签名包含 audio-input entitlement，Info.plist 包含麦克风用途说明；这些静态/包内证据不等于用户已经授权或录音成功。它没有 Developer ID 或 notarization，不应作为公开正式版。哈希以 [`artifacts/releases/SHA256SUMS`](../../artifacts/releases/SHA256SUMS) 为准。

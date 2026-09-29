# Gunther

Gunther 是一个**本地优先、多来源、证据可追溯的个人知识工作区**。它的核心不是“先建文件夹”，而是先可靠保存正在发生的材料，再由用户决定如何归类、相信什么，以及要产出什么。

```text
Capture first → Inbox → Libraries → 有依据地问答与审核 → Outputs
```

- **Capture** 是统一采集动作：笔记、文件、照片、网页、网页研究、实时录音和已有音频地位相同。
- **Inbox** 是待整理与待确认的工作队列，不是另一个知识库。
- **Library** 是一个长期主题空间，例如“生物信息学”，容纳课程录音、论文、书籍、网页和会议材料。
- **Notebook / Note** 是快速记录和编辑随手想法的界面；笔记可以先留在 Inbox，也可以提升为 Library 中的 Source。它不是临时数据库。
- **Outputs** 是从用户已经接受的知识生成并持久保存的成果版本，不是浏览器里的临时 Markdown。

## 当前能力基线

以下结论以当前源码为准；安装包、真机和长时间运行属于单独的发布验收边界。

### 多来源采集

| 来源 | 当前行为 |
| --- | --- |
| Quick note / 粘贴文本或表格 | 可先进入 Inbox，使用稳定采集 ID 重试；随后可归入 Library |
| 文件、PDF、DOCX、EPUB、HTML | 原始字节先保存为 Asset，再提取可检索正文并创建 Source |
| 图片与扫描 PDF | 支持离线 OCR；保存页码、区域、Provider、置信度和处理状态；OCR 不可用或超限时仍保留原件并标记 degraded |
| Link | 后端抓取并保存不可变网页响应 Asset、可读正文 Source、original/final URL、时间、HTTP 元数据和哈希；每次跳转继续执行 SSRF 与 DNS rebinding 防护 |
| Web search | 配置在线 Provider 后返回带来源的研究结果；用户选择后再保存，不等同于 Link 快照 |
| 实时录音 | 音频分片与实时转写分离；暂停、恢复、重点时刻、转写编辑、检查点和完成态均有持久状态 |
| 导入已有音频 | 桌面先按块复制到 app-owned IndexedDB，再从服务端已确认 offset 续传；完成初始本地复制后，崩溃重启无需重选原文件 |
| 手机系统分享 | Android `ACTION_SEND` / `ACTION_SEND_MULTIPLE` 与 iOS Share Extension 接收文本、URL、文件、图片和音频，并交给同一 durable outbox |

macOS OCR 默认使用随桌面 Helper 构建的 Apple Vision 本地适配；其他平台可使用已安装的 Tesseract。OCR 不调用云端。桌面文件采集先保存原件，再通过持久化后台任务解析；旧客户端仍可使用同步接口。处理状态、重试、取消与结构化证据在 Sources 中可见。

新增：持久化主题树、跨来源证据归类、按主题问答、FTS5 中英混合检索、不可变来源版本与引用定位。默认开启的本地语义检索（多语言 E5 + sqlite-vec，中英互查）、面向上千篇文献的整理（文件夹批量导入、论文信息与摘要 summary.md、重复副本合并、主题建议与主题综述、大库问答附论文摘要）、可选 Docling 接口、运行方式和发布验收边界见 [结构化知识后端说明](docs/KNOWLEDGE_BACKEND_V4.md)。

录音默认优先连接本机 SenseVoice：

```text
STT_PROVIDER=auto
SENSEVOICE_URL=http://127.0.0.1:8765
SENSEVOICE_SEGMENT_SECONDS=3.2
```

SenseVoice 是独立的本机服务，**不随 Gunther 安装包分发**。本轮已确认本机 `127.0.0.1:8765` 服务健康；它不可用时，Gunther 会明确降级。录音原始字节的保存不以转写成功为前提；配置 `OPENAI_API_KEY` 后，`auto` 才可使用在线转写回退。

### macOS 独立 Capture 与菜单栏

macOS 应用中的 Capture 不再是主页面里的临时弹窗。所有入口都会唤起同一个可移动、可缩放的独立窗口；隐藏 Capture 或关闭主窗口不会卸载录音器。菜单栏的专用 Capture 图标会区分 Ready、Recording、Paused、Review 与 Attention，并提供打开 Capture、开始录音、Quick Note、暂停/继续、标记时刻和结束录音等动作。

活动录音、导入、未完成 Review，以及 Note/Link/File 等已有未保存输入时，新入口只能显示当前 Capture，不能覆盖；此时退出也会被阻止。`Finish Recording` 只进入 Review，不会自动把未经确认的内容写入 Library。完整的入口、图标、关闭语义和验收场景见[独立 Capture 与菜单栏设计](./docs/CAPTURE_MENU_BAR_DESIGN_CN.md)。

隐藏 WebView 的后台节流只在 macOS 14 及以上可明确关闭。Gunther 仍保持 `LSMinimumSystemVersion=11.0`，但 macOS 11–13 的隐藏窗口长时录音不能宣称与 macOS 14+ 等价；这些系统要获得相同保证，后续需迁移为原生音频采集。

### Inbox、证据与用户确认

AI 抽取或对话产生的内容不会直接变成可信知识：

```text
Asset / Source
  → 带引用快照的回答或候选 Proposal
  → 用户接受 / 暂存 / 拒绝
  → accepted KnowledgeUnit + immutable revision
```

- Source 保留原件关系与采集 provenance。
- 回答只在所选 Library / Source / Topic 范围内检索，并保存当时的 citation snapshot。Ask 同时检索结构化 Assertion 与原始证据块，避免已有结论遮蔽相反证据；原文引用包含 source revision、block ID 和定位信息；没有足够相关证据时仍返回证据缺口。
- Proposal 由用户明确接受后，才物化为 `trusted` KnowledgeUnit revision。
- 证据不足时应保留缺口，不把模型推测伪装成来源事实。

### 持久 Outputs / Artifact history

当前数据库 schema 为 **v11**；migration 11 是 `structured_knowledge_and_durable_processing`。原有 migration 10 的不可变 Outputs 历史保持兼容：

- 按 workspace 与 knowledge base 隔离；
- 保存 `format`、`audience`、`title`、`content` 与创建时间；
- 固定 accepted unit IDs、每个 KnowledgeUnit 的 revision snapshot、Proposal / Session / Message provenance 与 evidence count；
- 保存 content hash 与 manifest hash，读取时执行完整性校验；
- 同一 lineage 只能从当前 head 生成下一版；并发或旧 head 返回冲突，界面可刷新后继续；
- `clientRequestId` 支持结果丢失后的幂等重放；
- 桌面可浏览历史、重新打开、生成新版本，并从持久 Artifact 复制或下载 Markdown；workbook 导出也包含 Artifact 历史。

Artifact 当前没有删除、覆盖或归档接口，也未加入全局 Search；它可在对应 Library 的 Outputs 中发现。Markdown 只是编辑、交换和导出格式，**不是主数据库**。

### 移动可靠采集

移动端 durable outbox 当前格式为 **v3**，覆盖 quick note、source、link、web、document、photo 与 audio：

- payload 采用文件锁与原子 manifest；原件流式复制到 app-owned 目录并记录大小和 SHA-256；
- 采集项在第一次上传前绑定已验证的 `workspaceId + connectionProfileId`，已绑定目标不能被替换；未配对时可保持 unassigned，待用户连接后再绑定；
- 进程中断的 uploading 状态会恢复为 retryable；只有服务端明确确认后才清理 staged original；
- v1 / v2 清单只在路径、普通文件、大小和校验均安全时迁移到 v3；损坏清单 fail closed 并保留原件；
- 系统 Share Sheet 的 native 临时副本只有在 durable outbox 已提交后才 ACK；重复分享按 `systemShareId` 去重；
- Android `image_picker` 的进程中断结果也会先进入独立恢复队列，再交给 outbox。

这说明“离线后可以重试”，不等于承诺任意设备、任意磁盘故障下零丢失。

### 桌面界面

桌面端以搜索为首页：打开即可检索全部本地知识，输入 `@` 选择 Library 限定范围，并可把问题直接交给该 Library 的有据问答（Ask）。界面采用白底黑字的统一设计系统，颜色只保留给 Library 标识、来源类型与状态；细节见 [设计系统](docs/DESIGN_SYSTEM.md)。

## 技术结构

```text
apps/
  backend/      FastAPI + SQLAlchemy + SQLite WAL；领域真源、文件与录音服务
  desktop/      Tauri 2 + React 19 + TypeScript + Vite
  mobile/       Flutter/Dart + Android/iOS 原生 Share 接入
packages/
  contracts/    桌面 TypeScript API 契约
scripts/        备份恢复、打包和验收辅助工具
docs/           产品、架构、运维与验收说明
data/           开发数据；不应提交
```

客户端不直接读写 SQLite。后端启动时按顺序执行 v1–v10 迁移；v6 引入采集幂等，v7 引入 workspace/device identity，v8–v9 引入网页快照 provenance 与请求身份，v10 引入不可变 Artifact history。

## 安全边界

- 打包桌面的 FastAPI sidecar 只监听 `127.0.0.1`，每次启动生成新的私有 token，以 `backend-ready/backend-auth-token.<launchNonce>` 原子发布，并校验 token 与 Origin。
- 手机不直接连接 8787。桌面提供独立的私网 HTTPS gateway，使用持久私有 CA / leaf、一次性配对、device bearer、撤销和 scope 隔离。
- 移动 token 与 CA 材料进入 secure storage；连接时校验证书链或 pinned fingerprint。
- Asset、Recording、outbox、Share ingress 与 Artifact 都验证路径、大小、哈希或目标 workspace，发现不一致时拒绝继续而不是静默改写。
- Asset 与 Recording 共用默认 20 GiB 存储预算并保留至少 1 GiB 可用磁盘；支持环境覆盖、并发预留、未知长度流检查、507/413 语义、失败回滚和 symlink fail-closed。
- 网页采集限制 scheme、解析地址、重定向、内容类型、压缩前后大小和超时；不能把它视为通用内网代理。
- 可选 Provider key 只进入本地后端配置，不应写入仓库、普通移动 profile、日志或导出。

安全机制已经进入代码和自动化验证，但安全 gateway 仍需跨物理手机完成安装、配对、证书、撤销和网络切换验收。

## 开发运行

要求：Node.js 20.19+、Python 3.12+、`uv`，以及 Tauri 2 对应平台依赖。

```bash
cp .env.example .env
npm install
uv sync --project apps/backend
npm run models:fetch   # 语义检索模型，约 135 MB，只需一次
npm run dev
```

`npm run dev` 同时启动本地 FastAPI 与 Tauri 开发窗口。分开调试：

```bash
npm run dev:backend
npm run dev:desktop:web
```

移动端：

```bash
npm run mobile:get
npm run mobile:analyze
npm run mobile:test
cd apps/mobile && flutter run
```

Android 模拟器的开发默认 API 是 `http://10.0.2.2:8787/api/`，iOS 模拟器默认是 `http://127.0.0.1:8787/api/`。物理手机应通过应用内一次性配对获得 HTTPS gateway profile，不能把无认证的开发端口暴露到局域网。

## 备份与恢复

业务真源是一个集合：`gunther.sqlite + assets/ + recordings/`。只复制其中一部分不是完整备份。

```bash
npm run backup:data -- --output-dir /安全的备份父目录
npm run verify:backup -- /备份目录/gunther-backup-...
uv run --project apps/backend python scripts/backup_gunther.py restore \
  /备份目录/gunther-backup-... --data-dir /新的空目标目录
```

备份工具使用一致性 SQLite snapshot，复制并哈希 Asset / Recording，写入 manifest 后自校验，再原子发布。恢复会先完整验证备份，只允许写入**不存在的新目录**，不会覆盖现有数据；验证还检查数据库引用与文件、Recording ledger 以及 Artifact 内容 / manifest / unit binding 完整性。

详细停机、验证、恢复和回退流程见[运行与恢复手册](./docs/OPERATIONS_AND_RECOVERY_CN.md)。

## 验证与发布状态

标准源码检查：

```bash
npm run check
npm run mobile:analyze
npm run mobile:test
```

### 打包桌面安装包

`npm run build:desktop` 在当前系统上把前端、Rust 外壳和冻结的 Python Helper 打成一个安装包。PyInstaller 不能跨平台构建，所以每个平台都要在对应系统上打包。

| 平台 | 产物（`apps/desktop/src-tauri/target/release/bundle/`） | 安装后 Helper 位置 |
| --- | --- | --- |
| macOS | `dmg/Gunther_<版本>_<arch>.dmg`、`macos/Gunther.app` | `Gunther.app/Contents/Helpers/GuntherBackend.app` |
| Windows | `nsis/Gunther_<版本>_x64-setup.exe`、`msi/*.msi` | 安装目录下的 `backend\` |
| Linux | `appimage/*.AppImage`、`deb/*.deb` | `/usr/lib/Gunther/backend/`（AppImage 内同样路径） |

macOS 正式发布（Developer ID 签名 + 公证）：

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: <名字> (<TEAMID>)"
export APPLE_ID="<Apple ID>" APPLE_PASSWORD="<App 专用密码>" APPLE_TEAM_ID="<TEAMID>"
npm run build:desktop
```

不设置这些变量时仍是 ad-hoc 签名，只适合本机使用；设置后 Helper 会强制重建，并由 PyInstaller 用同一身份（hardened runtime + `HelperEntitlements.plist`）签名，外层 App 由 Tauri 签名并公证。DMG 只包含构建机的架构（在 Apple Silicon 上构建即 arm64）。

通过 GitHub 发布：先把 `apps/desktop/src-tauri/tauri.conf.json` 的 `version` 改成新版本，然后推送同名 tag，例如 `git tag v0.1.0 && git push origin v0.1.0`。[`release.yml`](./.github/workflows/release.yml) 会在 macOS（Apple Silicon）、Windows 和 Linux runner 上分别构建，并把 DMG、`setup.exe`/MSI、AppImage/deb 上传到同一个 draft Release；三个平台全部成功后自动发布，任一平台失败则保持 draft。tag 与版本号不一致时会直接失败。macOS 签名与公证使用以下仓库 Secrets（缺少时仍构建 ad-hoc 签名的 DMG）：

| Secret | 内容 |
| --- | --- |
| `APPLE_CERTIFICATE` | 导出的 Developer ID Application 证书 `.p12`，base64 编码（`base64 -i cert.p12 \| pbcopy`） |
| `APPLE_CERTIFICATE_PASSWORD` | 导出 `.p12` 时设置的密码 |
| `APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID` | 公证用的 Apple ID、App 专用密码和 Team ID |

Windows / Linux 构建机需要 Node 20.19+、Rust、uv；Linux 另需 Tauri 系统依赖（如 `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev`）。在这两个平台上 Helper 目录是 Tauri 资源，所以单独运行 `npm run check:desktop` 前需先执行一次 `npm run prepare:sidecar -w @gunther/desktop`。

本轮最终回归：Backend 177 passed/1 skipped；Desktop 74 passed；Rust 10 passed；Flutter 108 passed、`flutter analyze` 0 issues；真实 Chrome production bundle → 随机 loopback backend E2E 32/32；冻结 Helper 随机 loopback 启动 1 passed。

当前仓库提供与最终源码一致并已校验的 macOS 与 Android 工程候选：

| 候选 | 发布边界 | 大小 / SHA-256 |
| --- | --- | --- |
| `artifacts/releases/Gunther_0.1.0_macOS_arm64.dmg` | App/Helper arm64；ad-hoc 深度签名与 DMG verify/只读挂载通过；仍无 Developer ID/notarization/干净机器验收 | 33,831,087 bytes / `dd0b8a7aee22447a0ae70dd2262c4b958da7b1e4241ae9c837517eda186815d9` |
| `artifacts/releases/Gunther_0.1.0_Android_debug.apk` | V2 debug 签名；不等同于 release keystore / AAB / 商店包 | 178,653,480 bytes / `e4325238000a8682e99f8f37d3dc51d5e9970d37682a5c327b46f207181434c1` |

可机器校验的清单见 [`artifacts/releases/SHA256SUMS`](./artifacts/releases/SHA256SUMS)。

macOS 最终包的 `LSMinimumSystemVersion=11.0`，并包含麦克风用途说明与 audio-input entitlement。当前候选已安装到 `/Applications/Gunther.app` 并成功启动主程序、Bundled Helper 与本机 API；健康响应确认连接 `sensevoice-small`。此前安装版已触发 TCC 提示，但当前用户未授权；本轮验收时 Mac 处于锁屏，因此没有把菜单栏人工走查或真实麦克风采集写成已完成。最终界面增加 15 秒授权等待、状态清理、系统设置指引和 Retry，仍需用户主动点击允许后才能完成真实录音验收。Web Research Provider 当前未配置。

尚不能由源码自动化替代的边界：iOS 需要完整 Xcode 才能 build/archive/sign；Android/iOS 仍需真机或模拟器安装、权限、Share Sheet、配对、录音、离线与恢复验收；还需要真实连续长时麦克风、休眠/锁屏、断网、磁盘压力和故障注入。系统后台/锁屏持续录音尚未交付。

## 文档

- [产品规划](./docs/PRODUCT_PLAN_V3_CN.md)
- [技术架构](./docs/TECHNICAL_ARCHITECTURE_V3_CN.md)
- [运行与恢复](./docs/OPERATIONS_AND_RECOVERY_CN.md)
- [独立 Capture 与菜单栏设计](./docs/CAPTURE_MENU_BAR_DESIGN_CN.md)
- [验收报告](./docs/ACCEPTANCE_REPORT_V3_CN.md)
- [设计系统](./docs/DESIGN_SYSTEM.md)

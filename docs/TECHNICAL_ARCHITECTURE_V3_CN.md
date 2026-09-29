# Gunther 技术架构 v3

更新时间：2026-08-30  
权威基线：当前源码，`LATEST_SCHEMA_VERSION=10`

## 1. 架构目标

Gunther 是本地优先的多来源知识工作区。架构首先保证四条边界：

1. 原件先持久化，派生处理失败不能抹掉原件；
2. workspace / knowledge base / connection profile 不得跨界写入；
3. AI 结果在用户批准前只是候选，不得直接成为 trusted knowledge；
4. Output 固定当时使用的 KnowledgeUnit revisions，历史版本不能被原地改写。

```text
Desktop / Mobile
       │ typed HTTP + WebSocket
       ▼
FastAPI domain boundary
       ├── SQLite WAL：对象、状态、证据、版本、ledger
       ├── assets/：上传原件与网页响应快照
       ├── recordings/：分片组装后的音频
       ├── local OCR：Apple Vision / Tesseract
       └── local SenseVoice 或显式配置的在线 Provider
```

客户端不直接读写 SQLite、Asset 目录或 Recording 目录。FastAPI 是业务行为与权限校验的唯一入口。

## 2. 技术栈

### Backend

- Python 3.12+；
- FastAPI / Pydantic；
- SQLAlchemy；
- SQLite，启用 foreign keys、WAL、busy timeout 与 `synchronous=NORMAL`；
- PyPDF 与有界格式解析；
- Apple Vision 原生 helper 或 Tesseract 本地 OCR；
- WebSocket 实时转写代理；
- 可选的模型 Provider（DeepSeek、Kimi、GLM、Qwen、OpenAI 兼容及自托管），在 Settings → Models 配置，密钥仅保留在本地服务层；
- 可选 Tavily 联网搜索。

### Desktop

- Tauri 2；
- React 19 + TypeScript + Vite；
- `packages/contracts` 维护桌面 HTTP DTO；
- IndexedDB 保存未确认录音分片和完整 staged import chunks；
- localStorage 只保存小型草稿、UI 偏好和不含大文件的重试元数据。

### Mobile

- Flutter / Dart；
- 原生 Android Kotlin share ingress；
- 原生 iOS Share Extension + App Group ingress；
- app-owned 文件目录、原子 JSON manifest、文件锁和 SHA-256 durable outbox；
- secure storage 保存 device token 与 CA 材料。

移动 DTO 当前仍是手写实现，尚未从 OpenAPI / 共享 schema 自动生成。

### Markdown

Markdown 是 Artifact 内容和导出/交换格式。对象身份、状态、知识 revision、证据、workspace、文件路径与完整性哈希均在结构化存储中；Markdown 不是主数据库。

## 3. 领域模型

### 采集与原件

- `NotebookNote`：可编辑的快速记录，状态为 inbox / filed / archived；
- `Asset`：按原始字节 SHA-256 标识的不可变原件；
- `Source`：可读材料与来源身份，关联 Asset 或网页 provenance；
- `WebSnapshot`：original/final URL、抓取时间、HTTP/媒体元数据、Asset 与 request fingerprint；
- `RecordingSession`：capturing/completed/failed 生命周期、byte size、next sequence、checkpoint；
- `RecordingChunk`：服务端已确认的序号、大小与 SHA-256 ledger。

Asset 身份与 Source 身份有意分离：相同字节可被不同标题/类型引用；相同正文但不同材料不会因正文哈希而错误合并。

### 知识与证据

- `KnowledgeBaseRecord`：长期主题空间；
- `SourceMembership`：Source 到 Library 的归类；
- `KnowledgeSession` / `SessionMessage`：范围限定的对话、分支、归档和 pin；
- `Assertion`：从 Source 抽取的可审核断言；
- `KnowledgeProposal`：从回答产生的候选知识；
- `KnowledgeUnit`：用户已经接受的 trusted knowledge；
- `KnowledgeUnitRevision`：不可变修订，指回来源 Proposal。

每个回答保存 citation snapshot。检索先使用 scope 内匹配的结构化 Assertion；没有可用匹配 Claim 时，后端对已保存 Source 正文做有界 passage retrieval（最多 5 段、每个 Source 最多 2 段，并要求足够查询词重合），返回 `assertion_id=null`、`status=provisional` 的来源引用。若两层都不相关则返回 evidence gap，不调用全局材料补齐。接受 Proposal 是从“AI 建议”进入“长期可信知识”的显式 approval boundary。

### Artifact / Outputs

schema v10 新增：

- `artifacts`：一个不可变 Output 版本；
- `artifact_unit_bindings`：Artifact 到固定 KnowledgeUnit revision 的可查询反向关系。

每个 Artifact 保存：

- `workspace_id`、`knowledge_base_id`；
- `client_request_id`、规范请求 fingerprint；
- `lineage_id`、`version_number`、`supersedes_artifact_id`；
- `format`、`audience`、`title`、`content`；
- `accepted_unit_ids_json`；
- revision snapshot：unit/revision ID、revision number、title、content、content hash、source proposal/session/message、evidence count；
- provenance；
- `content_hash` 与 `manifest_hash`；
- `created_at`。

读取 list/detail 时重新校验内容、manifest 和 bindings。创建新版本时，`supersedesArtifactId` 必须是当前 lineage head；旧 head 或并发竞态返回 409。Artifact 没有更新/删除接口，因此历史正文不能被原地覆盖。

当前限制：最多采用 100 个 KnowledgeUnits；revision snapshot 正文总量最多 2,000,000 bytes；最终 Artifact 正文最多 2,500,000 bytes。尚无归档接口、全局 Search 集成或 PDF/DOCX renderer。

## 4. Schema migration

迁移记录只追加，不编辑或重排已经发布的版本：

| 版本 | 名称 | 目的 |
| ---: | --- | --- |
| 1 | `baseline_current_schema` | 建立/接管基础 schema |
| 2 | `durable_recording_lifecycle` | RecordingSession / Chunk |
| 3 | `immutable_source_assets` | Asset store 与 Source 关联 |
| 4 | `recording_recovery_checkpoints` | 转写/UI 恢复检查点 |
| 5 | `source_capture_identity` | Source 身份与正文哈希分离 |
| 6 | `capture_idempotency` | Quick Note `clientCaptureId` |
| 7 | `workspace_device_identity` | Workspace、配对与可撤销设备 |
| 8 | `web_snapshot_provenance` | 不可变网页快照关系 |
| 9 | `web_capture_request_identity` | 网页采集意图 fingerprint |
| 10 | `immutable_artifact_history` | 持久 Output 版本与 revision bindings |

启动会在单个事务边界内按序应用缺失迁移；失败回滚。应用拒绝打开 schema 高于自身理解范围的数据库。

## 5. Capture 管线

### Quick note 与纯文本

```text
client capture id
  → local retry metadata / mobile outbox
  → POST /api/notes 或 POST /api/sources
  → Inbox
  → 用户归类
```

Quick Note 的 `clientCaptureId` 是唯一幂等边界。相同请求丢失响应后重试不会重复创建；已改变的意图不得复用同一 ID。

### 文件、图片与 OCR

```text
request stream
  → assets/.incoming/*.part
  → bounded copy + fsync + SHA-256
  → content-addressed Asset
  → bounded extraction / OCR
  → structured Capture processing response + Source 内嵌 OCR provenance/note
```

关键约束：

- 普通 Asset 最大 512 MB；
- 文本、压缩文档、PDF 页/stream、OCR 图片与时间均有独立上限；
- macOS `auto` 优先打包的 Apple Vision helper；其他平台可落到 Tesseract；`disabled` 明确关闭；
- OCR 输出包含页、region ppm、文本、置信度和 Provider；
- PDF 中已有文本时保留文本定位；缺少文本的页才进入 OCR；
- OCR unavailable、limit 或 partial failure 返回 degraded，并保留完整原件。

当前 OCR 在 Capture 请求内同步执行，整份 OCR 最长 60 秒、最多 100 页；它不是可恢复后台 Job。

### Link 快照

```text
normalized capture intent
  → scheme/host validation
  → DNS resolve + public-address policy
  → pin verified address for request
  → every redirect repeats validation
  → bounded response Asset
  → readable Source + WebSnapshot provenance
```

防护包括 loopback、私网、link-local、特殊 IPv6 映射/隧道地址、DNS rebinding、重定向、内容类型、超时和压缩前后大小。相同 `clientCaptureId` 只有在 URL 规范等价且意图一致时可重放，否则 409。

Web search 是独立在线研究流程；保存搜索结果不会自动为每个来源 URL 建立网页快照。

### Recording

```text
durable client chunk
  → PUT /recordings/{id}/chunks (sequence + SHA-256)
  → RecordingChunk ledger + *.part
  → checkpoint transcript/UI state
  → POST complete
  → final recording file + optional Source
```

- 单 chunk 最大 16 MB；
- 单会话最大 2 GB；
- 同序号同字节重试幂等；不同字节、跳号、完成后追加均拒绝；
- 完成时原子重命名并保留 ledger；
- checkpoint 使用 revision + hash 进行并发和幂等控制；
- 读取时协调数据库与 `.part` / final file，不一致时 fail closed 或标记 failed。

桌面录音每个未确认 chunk 先写 IndexedDB。导入已有音频先把完整文件切成 app-owned chunks；完整 staging 后可结合服务端 offset 恢复。初始 staging 尚未完成时仍需用户重选原文件。

移动现场录音把 PCM 写入 durable WAV，同时把 PCM 送到 live transcription client；上传完成后的 WAV 仍经过 durable outbox / recording ledger。

## 6. SenseVoice 与实时转写

后端健康检查根据配置选择：

1. `auto` / `sensevoice` 检查 `SENSEVOICE_URL`；
2. SenseVoice 可用时使用本地 `sensevoice-small` 适配；
3. `auto` 且配置了 OpenAI 兼容转写服务（`STT_BASE_URL`）时才可回退到该服务（同样按段调用 `/audio/transcriptions`）；
4. 都不可用时返回明确的 not configured / unavailable 状态。

桌面通过 `/api/recordings/live` 发送 24 kHz 单声道 PCM；后端按配置窗口包装 WAV 并调用 SenseVoice `/v1/audio/transcriptions`。开始时可调用 `/reset` 清理说话人会话。

实时转写 WebSocket 和 Recording chunk API 是两条链路。重连只影响实时文字，不决定音频是否持久化。SenseVoice 是外部本机依赖，不在 Gunther repo 或安装包中。

## 7. Mobile Share Sheet 与 durable outbox

### Native ingress

- Android：处理冷启动和运行中的 `ACTION_SEND` / `ACTION_SEND_MULTIPLE`；
- iOS：真正的 Share Extension target，经 App Group 复制原件，URL scheme 唤醒主 App，前台再读取；
- 支持文本、HTTP/HTTPS URL、单/多文件、单/多图片与音频；
- native manifest 损坏时拒绝继续并保留原件；
- native 副本只有在 Dart outbox manifest 成功提交后才 ACK。

### Outbox v3

```text
native/in-app capture
  → streaming copy to app-owned file
  → size + SHA-256
  → atomic manifest under lock
  → immutable workspace/profile binding
  → retry / awaiting confirmation
  → server ACK
  → cleanup journal + staged-file deletion
```

v3 覆盖 quickNote/source/link/web/document/photo/audio。文件/图片上限 512 MB，音频上限 2 GB。目标可以暂时 unassigned；一旦第一次上传前绑定，不能切换到另一个 workspace/profile。恢复会校验路径必须位于受控目录、普通文件、大小、哈希、类型与 manifest 版本；异常 fail closed。

系统分享使用 `systemShareId` 做 native→outbox 去重。Android 丢失拍照结果也经独立恢复队列交给同一 outbox。

## 8. API 轮廓

主要路由：

- 健康与身份：`/health`、`/workspace/bootstrap`；
- 安全移动连接：`/mobile-gateway/status`、`/pairing/sessions`、`/pairing/exchange`、`/devices`、revoke；
- Capture：`/notes`、`/sources`、`/captures/assets`、`/captures/web`、`/search/web`；
- Recording：session、chunk、checkpoint、complete、metadata、download、live WebSocket；
- Inbox / Library：`/inbox`、`/knowledge-bases`、base sources；
- Session / evidence：messages、branch、proposals、assertions、knowledge units；
- Artifact：Library 范围 list/detail/create。

workspace 写请求通过已验证 principal 与 workspace header/context 绑定；错误 workspace 不允许降级为默认库。Artifact list/detail/create 还同时限定 workspace 与 knowledge base。

## 9. 本地存储与运行形态

开发默认：

```text
data/
├── gunther.sqlite
├── assets/
└── recordings/
```

macOS 打包默认：

```text
~/Library/Application Support/com.gunther.knowledge/
├── gunther.sqlite
├── assets/
├── recordings/
├── mobile-gateway-pki/
├── backend-ready/
│   └── backend-auth-token.<launchNonce>  本次启动 ready token；不属于业务备份
├── backend-instance.lock
├── backend.log
└── .env                    可选秘密配置；备份需单独加密管理
```

打包 Helper 将整个私有数据树限制为当前用户访问，拒绝 symlink 与异常文件类型，并用进程锁阻止两套 Helper 同时打开同一数据目录。

### 共享存储预算

Asset 与 Recording 的全部 managed-file 写入口共用 `StorageBudget`。默认总配额为 20 GiB，并保留至少 1 GiB 文件系统可用空间；`STORAGE_QUOTA_BYTES` 与 `STORAGE_MIN_FREE_BYTES` 可覆盖默认值。预算同时计算已落盘文件与并发未完成 reservation：已知长度先预检，未知长度按流增量预留，写入前再检查。单请求上限返回 413；配额、最低剩余空间或 ENOSPC/EDQUOT 返回 507。失败会回滚数据库/文件、释放 reservation 并清理临时文件；根目录、祖先、`.incoming`、哈希子目录和 Recording `.part`/final 路径遇到 symlink 均 fail closed。

## 10. 安全架构

### Desktop sidecar

- 仅 `127.0.0.1`；
- 预绑定端口，端口占用时在发布凭据前失败；
- 每次启动 256-bit token；以 `backend-ready/backend-auth-token.<launchNonce>` 私有文件原子发布，nonce 与 token 绑定；退出删除；
- HTTP 使用 `X-Gunther-Token`，媒体/WebSocket 使用受限 query fallback；
- HTTP / WebSocket 校验 Origin；
- sidecar-only 管理 API 不向 device principal 开放。

### Mobile gateway

- 独立私网 HTTPS listener，不复用无认证开发端口；
- 持久私有 CA 与 server leaf；
- 短时一次性 pairing code，限制请求体、速率和复用；
- device bearer 可撤销并具有 scope；
- 客户端 secure storage 与系统 trust / private CA / pinned fingerprint 校验；
- profile 切换不改变已经绑定的 outbox 或录音目标。

这些边界已有代码和自动化覆盖，但跨物理手机的安装、发现、证书、撤销和网络切换仍是外部验收项。

## 11. 备份完整性

备份对象是 `gunther.sqlite + assets/ + recordings/`。工具：

- 使用 SQLite backup API 创建一致快照；
- 流式复制 Asset / Recording 并记录大小和 SHA-256；
- 生成 `manifest.json` 与 `manifest.sha256`；
- 检查 quick check、foreign keys、迁移历史和 schema；
- 检查数据库引用文件存在、目录内文件具有权威数据库归属；
- 校验 Recording 文件/ledger/哈希；
- 校验 Artifact content hash、manifest hash 与 unit binding hash；
- 全部通过后原子发布备份。

`restore` 先验证再复制，只接受不存在的新目标目录，从不覆盖当前数据。自动化恢复闭环已覆盖非空 Asset、完成态 Recording 与 Artifact；真实用户数据、异地副本和周期性人工演练仍属于运维责任。

## 12. 验证策略与边界

源码验证命令：

```bash
npm run check
npm run mobile:analyze
npm run mobile:test
```

当前架构回归证据：Backend 177 passed/1 skipped、Desktop 74 passed、Rust 10 passed、Flutter 108 passed且 analyze 0 issues、Chrome UI→backend E2E 32/32、冻结 Helper 随机 loopback 启动 1 passed。候选包大小与 SHA-256 由 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS) 固定。

仍不能由单元/集成测试替代：

- Android/iOS 真机或模拟器的安装、权限、Share Sheet、拍照与录音；
- iOS 完整 Xcode build/archive/sign；
- 正式 Apple/Android 签名、公证与商店打包；
- 物理手机上的 HTTPS 配对与撤销；
- 真实连续长时麦克风、后台/锁屏、断网、休眠、磁盘压力和故障注入；
- macOS 工程候选在干净机器上的 Gatekeeper、用户实际授权后的原生录音和长时间运行行为；包内用途说明与 audio-input entitlement 已验证。

系统后台/锁屏持续录音、异步 OCR Job、Artifact 归档/全局 Search/PDF-DOCX renderer 尚未实现，应与“代码已完成但等待外部验证”的项目分开表述。

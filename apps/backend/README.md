# Gunther Backend

Gunther Backend 是本地知识工作区的领域真源。桌面端和移动端只通过 HTTP / WebSocket 使用它，不直接读写 SQLite、Asset 或 Recording 文件。

## 运行

在仓库根目录：

```bash
uv sync --project apps/backend
npm run dev:backend
```

默认 API：`http://127.0.0.1:8787/api/`  
交互文档：`http://127.0.0.1:8787/docs`

测试与静态检查：

```bash
npm run test:backend
uv run --project apps/backend ruff check apps/backend
```

最终回归：177 passed、1 skipped；Ruff 通过。跳过项是可选的 frozen release helper 测试。

## 目录

```text
gunther/
  api.py                    FastAPI routes
  main.py                   app factory、auth 与 provider wiring
  config.py                 环境配置
  database.py               SQLite engine/session
  migrations.py             v1–v10 迁移
  models.py / schemas.py    持久化与 HTTP DTO
  service.py                Library、evidence、approval、Artifact
  asset_service.py          原件保存、抽取、OCR
  web_capture.py            安全网页快照
  recording_service.py      分片、ledger、checkpoint、恢复
  realtime.py               SenseVoice / online STT adapter
  ocr.py                    Apple Vision / Tesseract provider
  device_auth.py            workspace、配对、device bearer、撤销
  mobile_gateway_*.py       私网 HTTPS gateway 与 PKI
  desktop_server.py         打包 sidecar 入口
tests/                      API、迁移、安全、恢复与完整性测试
```

## 当前领域能力

### Capture 与 Source

- Quick Note / Source 的稳定采集 ID 与幂等边界；
- 文件、PDF、DOCX、EPUB、HTML、图片与扫描 PDF；
- 原始字节 Asset、SHA-256、受控相对路径与有界流式请求；
- 本地 OCR：macOS Apple Vision helper，其他平台 Tesseract fallback；
- OCR 页、region、Provider、置信度与 completed/degraded provenance；
- Link 不可变网页响应快照；每跳 SSRF、DNS rebinding、地址、类型、大小与超时防护；
- Inbox、Source membership 与原件下载。

### Recording 与转写

- durable RecordingSession / RecordingChunk ledger；
- sequence + SHA-256 幂等、大小上限、原子 complete；
- transcript/duration/moments/context/Library checkpoint；
- 服务重启后的 `.part` / final file 协调；
- `/api/recordings/live` 实时 PCM；
- `auto` 优先本机 SenseVoice，显式配置后才可在线回退。

SenseVoice 是独立本机服务，不在 backend package 内。

### Evidence 与 user approval

- Library / Source 范围内的持久 Session；
- citation snapshots、branch、archive 与 pin；
- Assertion 审核；
- scope 内没有可用匹配 Assertion 时，从保存的 Source 正文检索有界相关段落，生成 `assertionId=null` 的 provisional 来源引用；
- Answer → Proposal → user accept/hold/reject；
- accepted Proposal 幂等物化 trusted KnowledgeUnit revision；
- 空 Library、没有足够相关原文的提问和无关问题不跨库补证据。

### Artifact / Outputs

schema v10 的 `immutable_artifact_history` 提供 Library 范围 list/detail/create：

- immutable lineage versions；
- workspace + knowledge-base 双隔离；
- 只使用 trusted KnowledgeUnits；
- pinned revision snapshots 与 Proposal/Session/Message provenance；
- content / manifest / binding hash；
- `clientRequestId` 幂等；
- 只有当前 lineage head 可以生成下一版，旧 head 返回 409。

Artifact 当前没有 PATCH/DELETE/archive、全局 Search 或 PDF/DOCX renderer。

## Schema

当前 `LATEST_SCHEMA_VERSION=10`：

```text
1 baseline_current_schema
2 durable_recording_lifecycle
3 immutable_source_assets
4 recording_recovery_checkpoints
5 source_capture_identity
6 capture_idempotency
7 workspace_device_identity
8 web_snapshot_provenance
9 web_capture_request_identity
10 immutable_artifact_history
```

已应用迁移不可修改或重排。新迁移只能追加下一个连续版本。

## 配置

从仓库根目录的 `.env` 读取。主要变量见 [`.env.example`](../../.env.example)：

- `DATABASE_URL`、Asset/Recording 默认目录；
- `OCR_PROVIDER=auto|vision|tesseract|disabled`；
- `OCR_TESSERACT_COMMAND`、`OCR_PDFTOPPM_COMMAND`、语言；
- `STT_PROVIDER=auto|sensevoice|openai`；
- `SENSEVOICE_URL` 与 segment seconds；
- `STORAGE_QUOTA_BYTES`（默认 20 GiB）与 `STORAGE_MIN_FREE_BYTES`（默认 1 GiB）；
- 可选 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`；
- `SEED_DEMO=false` 默认创建空工作区。

Provider key 不应进入 Git、日志、前端 bundle 或移动普通 profile。

Asset 与 Recording 共用线程安全的 `StorageBudget`。已知/未知长度、并发 reservation 与写入前复检均在同一预算内；单请求过大返回 413，配额、最低剩余空间或 ENOSPC/EDQUOT 返回 507。失败路径回滚数据库/文件并清理临时文件，受控目录遇到 symlink 时 fail closed。

## 两种运行安全边界

### 开发 API

用于本机开发。不要为了连接物理手机把 8787 暴露成无认证 LAN 服务。

### 打包 desktop sidecar

`desktop_server.py`：

- 只预绑定 `127.0.0.1`；
- 对数据目录加单实例 OS lock；
- 每次启动创建 256-bit token；
- 以 `backend-ready/backend-auth-token.<launchNonce>` 私有文件原子发布；
- HTTP / WebSocket 校验 token 与 Origin；
- 私有数据树拒绝 symlink 和异常文件类型；
- 手机使用独立 HTTPS gateway、一次性 pairing 与可撤销 device bearer。

这些安全机制有自动化证据；跨物理手机仍需真机验收。

## Backup / verify / restore

```bash
npm run backup:data -- --output-dir /private/backups
npm run verify:backup -- /private/backups/gunther-backup-...
uv run --project apps/backend python scripts/backup_gunther.py restore \
  /private/backups/gunther-backup-... --data-dir /new/absent/data-dir
```

恢复只允许写入不存在的新目录，绝不覆盖现有数据。完整性范围包括 SQLite、Asset、Recording ledger/file 与 Artifact hashes/bindings。详细 Runbook 见 [`docs/OPERATIONS_AND_RECOVERY_CN.md`](../../docs/OPERATIONS_AND_RECOVERY_CN.md)。

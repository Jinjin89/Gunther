# Gunther 运行、备份与恢复手册

更新时间：2026-08-30  
当前数据库：schema v10

## 1. 先读结论

- Gunther 的业务真源不是一个 Markdown 文件，而是 `gunther.sqlite + assets/ + recordings/` 的一致集合。
- 使用仓库内的 backup / verify / restore 工具，不要只复制正在运行的 SQLite 文件。
- `restore` 只写入**不存在的新目录**，不会覆盖、删除或重命名当前数据；先在恢复副本上验收，再人工决定是否切换。
- 备份验证会检查文件大小/哈希、SQLite、迁移、数据库—文件关系、Recording ledger 以及 Artifact 内容、manifest 和 revision bindings。
- `.env`、日志、sidecar 启动 token 和移动 gateway 私钥不进入普通业务备份；秘密材料应单独加密管理。
- SenseVoice、OCR 与在线 Provider 是派生处理依赖。它们失败时不应删除已经保存的原件。
- 回收站是应用内的撤销，不是备份：移到回收站的内容 30 天内可恢复（`TRASH_RETENTION_DAYS` 可调）；“永久删除”、清空回收站或到期清理之后，只能从备份恢复。

不要把自动化恢复测试描述成用户真实备份已经演练，也不要承诺“零丢失”。

## 2. 启动与开发

### 整体开发环境

```bash
cp .env.example .env
npm install
uv sync --project apps/backend
npm run dev
```

分别启动：

```bash
npm run dev:backend
npm run dev:desktop:web
```

FastAPI 文档默认位于 `http://127.0.0.1:8787/docs`。开发服务器无意成为局域网服务；不要把它改为无认证公开监听来给手机使用。

### 移动开发

```bash
npm run mobile:get
npm run mobile:analyze
npm run mobile:test
cd apps/mobile && flutter run
```

Android 模拟器开发默认使用 `http://10.0.2.2:8787/api/`，iOS 模拟器默认使用 `http://127.0.0.1:8787/api/`。物理手机应使用桌面应用生成的一次性配对信息，连接独立的私网 HTTPS gateway。

## 3. 数据目录

### 开发模式

默认路径：

```text
<repo>/data/
├── gunther.sqlite
├── gunther.sqlite-wal      运行中可能存在
├── gunther.sqlite-shm      运行中可能存在
├── assets/
│   └── .incoming/          未完成接收临时区
└── recordings/
```

如果 `.env` 修改了 `DATABASE_URL`、`ASSETS_DIR` 或 `RECORDINGS_DIR`，先以实际启动配置为准。排障前不要假定看到的 `data/` 就是当前 App 正在使用的目录。

### Library 目录（`LIBRARY_ROOT`）

设置 `LIBRARY_ROOT` 后，原件（`assets/`、`recordings/`）在启动时一次性移入 `<LIBRARY_ROOT>/.gunther/`，并为每个 Library 写出可读目录（`Inbox/`、`Libraries/`、`Trash/`）。数据库仍留在私有数据目录。桌面 App 默认 `~/Gunther`；开发模式默认关闭。布局与保证见 [Library folders](LIBRARY_FOLDERS.md)。

备份时需要同时给出 Library 根目录，否则备份会因缺少原件而拒绝发布：

```bash
npm run backup:data -- --data-dir <数据目录> --library-root <LIBRARY_ROOT>
```

### macOS 打包桌面

默认：

```text
~/Library/Application Support/com.gunther.knowledge/
├── gunther.sqlite
├── assets/
├── recordings/
├── mobile-gateway-pki/     私有 CA / leaf；敏感身份材料
├── backend-ready/
│   └── backend-auth-token.<launchNonce>  本次启动 ready token
├── backend-instance.lock
├── backend.log             旧版本的日志；现在的日志在资料库的 .gunther/logs/
└── .env                    可选 Provider secrets
```

打包 Helper 会收紧 POSIX 权限、拒绝 symlink/异常文件类型，并锁住数据目录，避免两套 Helper 同时写同一数据库。

Asset 与 Recording 默认共用 20 GiB 总存储预算，并保留至少 1 GiB 文件系统可用空间。可在 `.env` 中用 `STORAGE_QUOTA_BYTES` 和 `STORAGE_MIN_FREE_BYTES` 覆盖；调整前应同时评估录音时长、原件大小、备份空间与磁盘其他负载。单请求超过固定上限返回 413；共享配额、最低剩余空间或文件系统空间不足返回 507。遇到 507 时先释放或扩容存储并保留现有数据，不要删除 `.incoming`、Recording `.part` 或数据库行来强行绕过保护。

### 不是业务真源

- `apps/desktop/dist/`、Rust `target/`、Flutter `build/`；
- `node_modules/`、`.uv-cache/`、虚拟环境；
- `backend.log` 与资料库里的 `.gunther/logs/`；
- 每次启动的 `backend-ready/backend-auth-token.<launchNonce>` token；
- 导出的 Markdown 或 `.gunther.json` workbook。

workbook 便于交换和人工审阅，但不能替代完整灾难恢复包。

## 4. 停机与冷备份

### 4.1 准备

1. 完成或暂停当前导入、录音和输出生成；
2. 退出桌面 App；
3. 开发模式下停止 FastAPI 与前端；
4. 确认没有其他进程继续写入数据目录；
5. 选择仓库外、权限受控的备份父目录。

备份工具也会检测数据库在备份期间是否变忙/变化，并在不一致时拒绝发布；停机仍是最容易审计的标准流程。

### 4.2 创建

开发数据：

```bash
npm run backup:data -- --output-dir /path/to/private-backups
```

打包数据：

```bash
npm run backup:data -- \
  --data-dir "$HOME/Library/Application Support/com.gunther.knowledge" \
  --output-dir /path/to/private-backups
```

成功结果：

```text
gunther-backup-<timestamp>-<id>/
├── gunther.sqlite
├── assets/
├── recordings/
├── manifest.json
└── manifest.sha256
```

工具在同一目标文件系统的私有临时目录内完成 SQLite snapshot、流式文件复制、哈希和完整性验证；全部通过后再原子重命名。失败不会发布一个看似完成的目录。

### 4.3 普通备份故意不包含什么

- `.env`：可能含 Provider key；
- `backend.log` 与 `.gunther/logs/`：诊断记录（密钥已遮蔽，但含文件名与操作经过）；
- sidecar token：每次启动轮换，不能跨恢复复用；
- `mobile-gateway-pki/`：含 CA / leaf 私钥，不应混入普通业务数据副本。

如业务必须保留相同移动 gateway 身份，应制定单独的加密私钥备份、访问审计和恢复流程。默认恢复没有旧 PKI 时会产生新证书身份；移动端应检测 certificate change，操作员应复核并重新配对。恢复数据库中原有 paired-device 记录后，建议先在桌面撤销旧设备，再用新证书重新配对，避免把“DB 已恢复”和“设备信任仍有效”混为一谈。

## 5. 验证备份

```bash
npm run verify:backup -- /path/to/gunther-backup-...
```

`verify` 是只读操作，检查：

- `manifest.sha256` 与 manifest 内容；
- manifest 文件集合、每个文件大小与 SHA-256；
- SQLite `PRAGMA quick_check` 与 `foreign_key_check`；
- schema v10 与完整迁移历史；
- Asset / Recording 的相对路径不越界；
- 数据库引用的 Asset/Recording 文件存在，备份内业务文件具有数据库归属；
- Recording 完成文件、byte size、chunk ledger 与哈希一致；
- Artifact `content_hash`、`manifest_hash`、accepted unit snapshot 与 `artifact_unit_bindings` 一致。

校验成功只说明这份备份内部一致，不说明恢复后的 App 已经过用户路径验收。

## 6. 安全恢复到新目录

### 6.1 执行

目标目录必须不存在：

```bash
uv run --project apps/backend python scripts/backup_gunther.py restore \
  /path/to/gunther-backup-... \
  --data-dir /path/to/new-gunther-data
```

恢复流程：

1. 先完整执行 `verify`；
2. 在目标父目录创建私有临时目录；
3. 逐文件复制并重新核对大小/哈希；
4. 再次检查 SQLite、文件关系、Recording 与 Artifact 完整性；
5. 目标仍不存在时才原子发布。

如果目标已经存在、是 symlink、位于备份内部，或复制期间出现差异，操作会失败且不覆盖现有数据。

### 6.2 在恢复副本上验收

不要立刻替换当前目录。先让一套隔离后端指向恢复副本，检查：

- `/api/health` 与 `/api/workspace/bootstrap`；
- Knowledge Bases、Inbox、最近 Sources 和 Notes；
- 随机下载多个原件并打开；
- 随机播放已完成 Recording；
- 打开多个 Artifact 历史版本，检查正文和 pinned provenance；
- 移动 gateway 是否生成新证书身份；
- `quick_check` 与 foreign keys；
- 从恢复副本再次 `backup + verify`。

只有这些检查完成后，才由操作员保留旧目录、切换应用数据路径。删除旧数据属于独立的破坏性动作，不应由 restore 命令自动执行。

### 6.3 当前证据边界

当前自动化在隔离临时目录中覆盖非空 Source/Asset、完成态分片 Recording 与不可变 Artifact 的：

```text
backup → verify → restore to new dir
→ API/read/download/ledger/hash/integrity checks
→ re-backup → verify
→ tamper detection
```

仓库还保留早期 schema 副本的受控迁移历史材料，但它不是当前 v10 真实用户数据恢复证明。当前仍需要一次以非空真实工作区为输入、由操作员完整记录的 v10 灾难恢复演练，以及周期性异地备份验证。

## 7. SQLite 与迁移

当前连接设置：

- `PRAGMA foreign_keys=ON`；
- `PRAGMA journal_mode=WAL`；
- `PRAGMA busy_timeout=5000`；
- `PRAGMA synchronous=NORMAL`。

当前迁移范围是 v1–v10；migration 10 为 `immutable_artifact_history`。v6 的 `clientCaptureId`、v7 的 workspace/device identity、v8–v9 的网页快照迁移都是历史链的一部分，不代表数据库停留在那些版本。

只读诊断：

```bash
sqlite3 data/gunther.sqlite "PRAGMA quick_check;"
sqlite3 data/gunther.sqlite "PRAGMA foreign_key_check;"
sqlite3 data/gunther.sqlite \
  "SELECT version, name, applied_at FROM gunther_schema_migrations ORDER BY version;"
```

不要在没有验证备份的情况下手工改表、删除迁移记录、删除 `-wal` / `-shm`，或把 v10 数据库交给只理解旧 schema 的应用。

## 8. Asset、OCR 与网页快照恢复

### Asset

```text
request → .incoming/*.part → fsync → SHA-256 → content-addressed file → Asset row
```

如果 Source 存在但下载失败：

1. 保留 Source 和数据库；
2. 确认恢复的是完整 `assets/`；
3. 对照 Asset `relative_path`；
4. 确认文件是受控目录内普通文件；
5. 不要手工把另一文件重命名到哈希路径冒充原件。

### OCR

OCR 文本是派生数据，原图/扫描 PDF 仍是权威 Asset。健康接口会显示当前 OCR mode/provider。默认：

- macOS：Apple Vision helper；
- 非 macOS：已安装 Tesseract 时使用本地 fallback；
- `OCR_PROVIDER=disabled`：明确关闭。

OCR unavailable、limit 或 partial failure 会在 Capture processing 响应中标记 degraded，并把 OCR provenance / 处理说明写入 Source 正文。修复 Provider 后，当前没有通用 UI 级批量重跑 OCR Job；不要通过替换原件来“修复”派生文本。

### WebSnapshot

网页 Source 还依赖 WebSnapshot → Asset 的 provenance。只恢复 Source 正文而缺 Asset 会失去当时 HTTP 响应证据。备份工具会把网页响应 Asset 与普通文件一起校验。

## 9. Recording 恢复

### 服务端状态

```text
capturing → recordings/*.part
completed → final file
failed    → 元数据和可诊断文件保留
```

服务端按 sequence、size、SHA-256 维护 chunk ledger。读取 metadata/list 时会协调：

- 数据库 capturing 但只有 final file：恢复可继续状态；
- `.part` 大于已确认 byte size：截断到权威 offset；
- `.part` 小于已确认 byte size：标记 failed；
- completed 但只剩 `.part`：尝试完成重命名；
- final file 缺失或大小不符：标记 failed。

不要手工拼接、截断或改名 `.part`。先做完整备份，再让 API 协调并读取 recovery 说明。

### 桌面实时录音与音频导入

- 未确认实时 chunk 先写 IndexedDB，服务 ACK 后才删除；
- IndexedDB 不可用或写入失败时停止录音，不降级到内存；
- transcript、duration、moments、context 与 target Library 周期性写入带 revision 的服务端 checkpoint；
- 导入已有音频先完整切块到 app-owned IndexedDB，之后可从服务端已确认 offset 继续；
- 如果崩溃发生在初始完整 staging 之前，仍需用户重选原文件；不完整残片不会被冒充为完整导入。

macOS 最终包包含 `NSMicrophoneUsageDescription` 与 `com.apple.security.device.audio-input=true`。首次开始录音时必须由用户在 TCC 提示中点击允许；Gunther 最多等待 15 秒，随后清理等待状态并显示“系统设置 → 隐私与安全性 → 麦克风”指引和 Retry。此前安装版已触发过系统提示，但当前用户未授权；排障时不要把“提示出现”或“entitlement 存在”当成已经录到音频。

### 移动录音与 outbox

- PCM 写入 durable WAV，同时进入 live transcription client；
- WAV 保存与 SenseVoice 返回相互独立；
- 活动录音另有原子 recovery record；重启后恢复为 local-only 文件，不声称系统仍在后台录制；
- 完成录音/导入音频经过 durable outbox，并固定 workspace/profile；
- 服务确认后才清理 app-owned 原件。

系统后台/锁屏持续录音尚未实现。自动化和稀疏长时 WAV 边界检查不能替代真实连续长时麦克风、休眠、强杀、断网和磁盘压力测试。

## 10. 移动 Share Sheet 与 outbox 维护

当前 Android/iOS Share ingress 支持文本、URL、单/多文件、单/多图片和音频。运维语义：

- native ingress 先复制 Provider 原件；
- Dart outbox 成功提交后 native 才 ACK；
- outbox v3 manifest 在锁内原子替换；
- staged file 每次使用前复核路径、大小与 SHA-256；
- uploading 中断恢复为 retryable；
- 只有服务器确认后进入 cleanup journal；
- manifest / cleanup journal 损坏时 fail closed，需用户确认后才能隔离损坏元数据；
- unassigned 分享需要在已验证连接建立后显式绑定；已绑定目标不能改写。

不要直接删除应用支持目录来“清空失败队列”，这会同时删除尚未上传的唯一原件。先在 UI 查看 pending captures 与 maintenance 状态，并在确认内容已无保留价值后执行明确清理。

## 11. SenseVoice 运维

默认配置：

```text
STT_PROVIDER=auto
SENSEVOICE_URL=http://127.0.0.1:8765
SENSEVOICE_SEGMENT_SECONDS=3.2
```

健康检查：

```bash
curl --max-time 3 http://127.0.0.1:8765/health
curl --max-time 3 http://127.0.0.1:8787/api/health
```

Gunther 健康响应应明确显示 `sensevoice_local`、在线回退或 `not_configured`。本轮已确认本机 8765 服务健康；这只证明 Provider 可达，不证明用户已授权麦克风或完整实时转写已经通过。SenseVoice 的安装位置与启动方式属于外部本机服务，不应在通用文档中假定固定用户路径。

故障时：

1. 不要停止或删除正在保存的 RecordingSession；
2. 检查 8765 服务健康；
3. 检查 Gunther live WebSocket 是否能重连；
4. 把“音频已经保存”和“实时文字是否完整”分开判断；
5. 需要云回退时，显式配置 Key 并确认隐私政策。

## 12. 安全连接故障

### Desktop sidecar

- 安装版 Helper 优先使用 `127.0.0.1:28787`（开发后端为 8787）；28787 被别的程序占用时不会动那个程序，而是换一个空闲端口，并在 ready 文件里告诉 App；
- 生命周期由 App 负责：打开 App 时启动 Helper；启动前若发现上次遗留的 Gunther Helper（`backend-owner.json` 记录的进程，且确认是 Gunther 的 Helper、启动它的 App 已不在），先停掉它（先 SIGTERM，3 秒后 SIGKILL），别的程序一律不碰；退出 App 时停止 Helper；App 崩溃或被强制退出时，Helper 从 stdin 管道关闭或父进程变化得知，最多 3 秒礼貌收尾、5 秒后强制退出（未完成的后台任务在下次启动时继续）；
- 同一时间只运行一个 Gunther：再次打开会把已运行的窗口带到前面；
- 查占用：macOS/Linux `lsof -nP -iTCP:28787 -sTCP:LISTEN`，Windows `netstat -ano | findstr 28787`；
- 不要向占位进程发送数据，也不要关闭认证绕过；
- 看资料库 `.gunther/logs/` 里当天的 `*.backend.log`：启动失败时最后有一行 `FATAL … knowledge service stopped: <原因>`，启动页也会直接显示这个原因；`*.app.log` 记录外壳与窗口一侧（见 [DEVELOPER.md](DEVELOPER.md#log-files)）；
- 刚退出就重开不会再因旧连接的 TIME_WAIT 而占不到端口；如果仍提示端口被占用，就是确实有别的程序在监听；
- 再检查实例锁和本次 ready token；
- token 是一次启动凭据，不能从旧备份复用。

### Mobile gateway

- 物理手机连接独立 HTTPS gateway，不连接 loopback 8787；
- pairing code 短时且一次性；
- certificate changed 必须人工核对，不应自动信任；
- revoked / auth expired 需要重新配对；
- 恢复数据但未恢复 PKI 时，预期证书身份变化，应撤销旧设备记录并重新配对。

## 13. 故障定位表

| 现象 | 先检查 | 安全动作 |
| --- | --- | --- |
| App 打开但没有原数据 | 实际 data dir、`.env`、启动方式 | 退出并保留现目录；确认是否打开了另一套数据 |
| 数据库 locked | 是否有多个后端/桌面实例 | 停止重复实例；不要删除 WAL/SHM |
| Source 可见但原件打不开 | Asset row、relative path、完整 `assets/` | 保留数据库，找回匹配备份，不手工伪造哈希文件 |
| OCR 显示 degraded | `/api/health` 的 OCR provider、页/时间/大小限制 | 原件已保留；修复 Provider 后规划受控重处理 |
| 录音停在 capturing | metadata recovery、`.part`、ledger | 先备份，让服务端协调，不手工改文件 |
| 导入音频等待恢复 | IndexedDB 完整 chunks、workspace、server offset | 完整 staging 后直接恢复；不完整则重选原文件 |
| 手机有 pending capture | outbox 状态、目标 profile、网络 | Retry；不要删除应用目录 |
| Share 后未出现 | native ingress 状态、损坏 manifest、是否未绑定 | 保留原件，修复/隔离需用户确认 |
| SenseVoice 无文字 | 8765 health、live WebSocket | 继续保存音频；不要把 STT 故障当录音丢失 |
| 手机证书变化 | gateway PKI 是否重建、恢复是否换目录 | 人工核对，撤销旧设备后重新配对 |
| Artifact 打不开 | content/manifest/binding integrity 错误 | 保留数据库和备份，禁止手工覆盖历史正文 |

## 14. 正式运营前门槛

- 当前工程回归已固定为 Backend 177 passed/1 skipped、Desktop 74 passed、Rust 10 passed、Flutter 108 passed/analyze 0 issues、Chrome UI→backend 32/32；候选哈希见 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS)；
- iOS build provenance，以及 macOS/Android 的正式分发构建 provenance；
- 正式平台签名、公证、AAB/archive 与干净机器安装；
- Android/iOS 真机或模拟器的 Share、权限、录音、配对、离线恢复；
- 真实连续长时录音及强杀、休眠/锁屏、断网、磁盘压力、STT 故障注入；
- 周期性 v10 真实数据 backup→restore→rebackup 人工演练；
- 加密异地备份、密钥轮换、PKI 恢复/重新配对 Runbook；
- 磁盘空间预警、录音前容量估算、脱敏诊断包；
- 应用内恢复向导与可读一致性报告。

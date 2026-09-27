# Gunther v3 验收报告

更新时间：2026-08-30  
验收对象：当前源码；数据库 schema v10  
发布计数与产物校验：当前工程候选已完成最终回归与 SHA-256 校验

## 1. 结论

当前源码已经形成完整的产品闭环：

```text
多来源 Capture
→ durable local preservation / server acknowledgement
→ Inbox
→ Library 范围内的 Source、Session 与 evidence
→ user-approved KnowledgeUnit revisions
→ immutable Artifact / Output history
→ verified backup / safe restore
```

可以确认“当前源码、工程候选和自动化边界已建立”，但**不能确认已经正式发布**。macOS/Android 候选已由最终源码重建并校验；Android/iOS 真机或模拟器、完整 iOS build、正式签名、公证、跨物理手机安全网关、真实连续长时录音与故障注入仍缺外部验收。

## 2. 证据口径

本报告使用三种状态：

| 状态 | 含义 |
| --- | --- |
| 已实现 | 运行路径和数据模型存在，不等于已经完成真机或公开发布验收 |
| 有自动化证据 | 当前源码已执行对应测试、静态检查或隔离 smoke，并记录结果 |
| 待外部验收 | 需要真实 OS、设备、签名、网络、时长或人工观察，源码测试不可替代 |

本轮最终证据：

- Backend：177 passed、1 skipped；Ruff 通过；
- Desktop：74 passed；TypeScript typecheck、Vite production build 通过；
- Rust sidecar：10 passed；原生构建通过；
- Flutter：108 passed；`flutter analyze` 0 issues；Android debug APK 构建通过；
- 真实 Chrome production bundle → 随机 loopback backend：32/32 UI→backend E2E；
- 冻结 Helper 随机 loopback 启动：1 passed；未使用或中断用户 8787；
- macOS DMG 与 Android APK 的大小、SHA-256、签名和平台元数据见第 12 节与 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS)。

## 3. 数据库与迁移

### 已实现

- `LATEST_SCHEMA_VERSION=10`；
- v1–v10 只追加迁移；
- migration 10 `immutable_artifact_history` 创建 `artifacts` 与 `artifact_unit_bindings`；
- 新库建立当前 schema，旧库按序升级；
- 数据库版本高于应用时拒绝启动；
- 迁移失败事务回滚；
- SQLite 启用 foreign keys、WAL、busy timeout 与 `synchronous=NORMAL`。

### 自动化范围

- 迁移顺序、幂等、失败回滚与旧行保留；
- 旧 schema 增加 workspace/device、网页快照和 Artifact history；
- Artifact 新表的列、唯一约束、外键和索引；
- 备份/恢复后的 schema 与完整迁移历史验证。

### 边界

早期隔离目录的受控旧库迁移报告是历史证据，不等于已经用非空真实用户工作区完成 v10 人工灾难恢复。本轮正式运营前仍应补一次完整 v10 冷备份→新目录恢复→应用路径检查→再次备份的人工演练。

## 4. Capture、Asset 与 Inbox

| 能力 | 状态 | 关键边界 |
| --- | --- | --- |
| Quick note / 文本 / 表格 | 已实现 + 自动化 | `clientCaptureId` 幂等；Note 与 Source 是不同阶段 |
| 文件/PDF/DOCX/EPUB/HTML | 已实现 + 自动化 | 原始字节 Asset、SHA-256、流式上限、提取限制 |
| 图片/扫描 PDF OCR | 已实现 + 自动化 | 本地 Vision/Tesseract；页/区域/Provider/置信度；失败 degraded、原件保留 |
| Link 网页快照 | 已实现 + 自动化 | 不可变响应 Asset、URL provenance、SSRF/DNS rebinding/redirect/size/type/timeout 防护 |
| Web search | 已实现，需 Provider | 在线结果保存不自动抓取每个 URL 快照 |
| Inbox 归类与审核 | 已实现 + 自动化 | 归类不等于接受 AI 内容 |
| 原件下载 | 已实现 + 自动化 | 路径必须在受控数据目录，缺失 fail closed |

OCR 是当前运行路径，不应再写成“图片仅保存原件”。macOS Apple Vision helper 随桌面 Helper 构建；非 macOS Tesseract 依赖运行环境已有对应命令。OCR 仍是有界同步处理，没有后台可恢复 Job。

Asset 与 Recording 使用共享 `StorageBudget`：默认总配额 20 GiB，并保留至少 1 GiB 可用磁盘；可通过 `STORAGE_QUOTA_BYTES` / `STORAGE_MIN_FREE_BYTES` 调整。单请求超限返回 413，配额、最低剩余空间或底层 ENOSPC/EDQUOT 返回 507。自动化覆盖 Content-Length 预检、未知长度流增量预留、并发 reservation、失败回滚、临时文件清理、数据库—文件一致性以及受控根目录/祖先/嵌套路径的 symlink fail-closed。

## 5. Recording 与 SenseVoice

### 已实现

- RecordingSession / RecordingChunk durable lifecycle；
- 有界 chunk、sequence、SHA-256、byte-size ledger 与幂等重试；
- capturing/completed/failed 与 `.part` / final file 协调；
- transcript、duration、moments、recording context、knowledge base target 的 revision checkpoint；
- 桌面未确认实时 chunk 先进入 IndexedDB；持久化失败时停止，不回退内存；
- 桌面录音工作区支持暂停、恢复、完成、应用内最小化和再次展开；
- 桌面已有音频先完整 staged 为 app-owned chunks，完成 staging 后可从服务端确认 offset 恢复，无需重选文件；
- 移动实时 PCM 写 durable WAV，并行馈送 live transcription client；
- 移动活动录音有 recovery record，重启后恢复为 local-only 文件；
- 本机 SenseVoice adapter、健康检测、实时 WebSocket 与可选在线回退。
- macOS 最终包的 Info.plist 含 `NSMicrophoneUsageDescription`，签名 entitlement 含 `com.apple.security.device.audio-input=true`；首次录音授权等待最多 15 秒，退出等待时清理状态，并提供系统设置指引与 Retry。

### 自动化范围

- chunk 顺序、重复、大小、哈希、完成回滚；
- 服务重启后的 session / checkpoint / 文件协调；
- 桌面 IndexedDB chunk 身份、workspace 绑定、import manifest 与恢复规划；
- 移动 PCM/WAV、draft、outbox 与 live client；
- SenseVoice unavailable/segment failure 的独立失败语义。
- 桌面麦克风授权等待超时、清理、系统设置提示与重试状态。

本轮已把当前候选安装到 `/Applications/Gunther.app` 并成功启动主程序、Bundled Helper 与带认证的本机 API；健康响应确认连接到 `sensevoice-small`，SenseVoice `127.0.0.1:8765` 本身也返回健康。此前安装版实际触发了 macOS TCC 麦克风提示，但用户未授权；本轮原生检查时 Mac 处于锁屏，因此这些观察不能证明最终包已经捕获真实音频、完成菜单栏人工走查或跑通麦克风→SenseVoice 端到端转写。

### 待外部验收

- 真实连续长时麦克风；
- 强杀、休眠、锁屏、断网、磁盘压力和 STT 故障注入组合；
- 用户在最终 macOS 包中实际点击允许后的录音、音质与实时转写；
- Android/iOS 麦克风权限与实际音频质量；
- 物理手机经 HTTPS gateway 到本机 SenseVoice 的端到端链路；
- 系统后台/锁屏持续录音。

稀疏生成的长时 WAV 边界测试不能写成真实长时麦克风，更不能据此承诺“零丢失”。如果桌面崩溃发生在导入文件初始完整 staging 之前，仍需要用户重选原文件；这一边界不影响 staging 完成后的 offset 续传能力。

## 6. Mobile Share Sheet 与 durable outbox

### 已实现

- outbox v3 覆盖 quickNote/source/link/web/document/photo/audio；
- payload manifest 使用锁与原子替换；
- 原件流式复制到 app-owned 路径，保存大小和 SHA-256；
- v1/v2 安全迁移到 v3，损坏/缺失时 fail closed；
- target `workspaceId + connectionProfileId` 在上传前绑定，已绑定不可替换；
- uploading 中断恢复为 retryable，服务 ACK 前不删除 staged original；
- Android `ACTION_SEND` / `ACTION_SEND_MULTIPLE`；
- iOS Share Extension、App Group、冷启动唤醒与前台 re-fetch；
- 文本、URL、单/多文件、单/多图片与音频；
- native manifest 损坏时保留原件，native 只在 outbox 提交后 ACK；
- `systemShareId` 去重；
- Android 拍照进程中断结果恢复并汇入 outbox。

### 自动化/静态证据

- Dart outbox、迁移、cleanup journal、target binding、profile switch、幂等和异常路径；
- Android manifest/Kotlin share contract；
- iOS target、entitlements、Info.plist、Swift ingress 与 project embedding contract。

### 待外部验收

- Android/iOS 实际从浏览器、相册、文件 App 分享；
- 多文件、大文件、Provider 慢读、取消、系统回收与空间不足；
- 安装后 App Group、URL scheme、权限与主 App 唤醒；
- 物理手机离线、重启、换网后 outbox 恢复。

因此可以写“Share Sheet 已实现并有源码/自动化边界”，不能写“已完成真机交付”。

## 7. Evidence、Approval 与知识隔离

### 已实现

- Session 必须属于 Library；
- 可选 selected Source scope；
- 回答保存不可变 citation snapshot；
- scope 内没有可用匹配 Assertion 时，从保存的原始 Source text 检索有限相关段落，并以 `assertionId=null`、`status=provisional` 的来源引用回答；
- 空 Library 不检索其他 Library 的 Source；
- 无证据和无关问题保留证据缺口；
- Proposal 由用户 accept/hold/reject；
- accept 幂等物化 trusted KnowledgeUnit revision；
- Assertion 也有 provisional/verified/disputed 审核状态；
- workspace 写请求与移动 principal 绑定。

“Source 已归类”“Assertion verified”“Proposal accepted”是三个不同动作。产品与文档不应把加入 Library 描述成 AI 内容已经可信。

安装版已针对一个真实课程录音 Source 提问并返回 1 个 grounded citation；该引用来自保存的原始 source text，`assertionId=null`。这证明“无结构化 Assertion → 有界原文检索 → 持久 citation snapshot”的实际路径可达，但不替代更多课程、语言和长文材料上的检索质量评测。

## 8. Artifact / Outputs v10

### 已实现

- Library 范围 list/detail/create；
- workspace + knowledge-base 双边界；
- 只接受同一 Library 的 trusted KnowledgeUnits；
- 固定 accepted unit IDs 与每个 head revision snapshot；
- 保存 Proposal/Session/Message provenance 与 evidence count；
- content hash、manifest hash 和 binding hash；
- `clientRequestId` 幂等；
- lineage、version number 与 supersedes；
- 只有当前 head 可生成下一版，旧 head/并发冲突为 409；
- 桌面 Outputs 历史、重新打开、pinned provenance、生成新版本、409 刷新恢复；
- 从持久 Artifact 复制/下载 Markdown；
- Library workbook v3 导出包含 Artifact 详情。

### 自动化范围

- 创建、重放、意图冲突、workspace/Library 越界；
- 不可信/外库/重复/超限 units；
- immutable version、stale head 与并发唯一约束；
- content/manifest/binding tamper detection；
- 桌面历史选择、pinned revisions、下一版请求与冲突刷新；
- backup→restore 后 Artifact 逐对象一致。

### 尚未实现

- Artifact archive / delete policy；
- 全局 Search 结果；
- PDF/DOCX 正式 renderer；
- 完整的版本 diff 与 evidence coverage UI。

Outputs 已不是临时 Markdown；但 Markdown 仍只是 Artifact 内容/导出格式，不是主数据库。

## 9. 安全边界

### Desktop sidecar

- 只绑定 loopback；
- 端口预绑定与单实例数据目录锁；
- 每次启动生成 256-bit token；
- 以 `backend-ready/backend-auth-token.<launchNonce>` 私有文件原子发布；
- HTTP token 与 Origin 校验；WebSocket/媒体受限 query fallback；
- token 退出时删除，不进入业务备份。

### Mobile gateway

- 独立私网 HTTPS listener；
- 持久私有 CA / leaf；
- 一次性、短时配对；
- device bearer、scope、撤销与管理面隔离；
- secure storage；
- 系统 trust / private CA / pinned fingerprint；
- workspace/profile 目标固定。

### 待外部验收

安全 gateway 的代码和自动化不能替代跨物理手机的发现、配对、证书变化、撤销、换网和长时间实时录音验收。不能为省事把 8787 改为无认证 LAN 服务。

## 10. Backup / verify / safe restore

### 已实现

- 一致性 SQLite backup；
- Asset / Recording 流式复制与大小/SHA-256 manifest；
- quick check、foreign keys、schema/迁移；
- 数据库引用文件与孤立业务文件检查；
- Recording ledger / 文件哈希检查；
- Artifact content/manifest/binding 完整性；
- 全部验证后原子发布；
- verify-only；
- restore 到不存在的新目录；目标存在、symlink、位于备份内或复制差异时拒绝；
- 恢复后可再次 backup / verify；
- tamper detection。

### 当前证据边界

自动化已覆盖非空 Asset、完成态 Recording 与 Artifact 的完整闭环。普通业务备份不包含 `.env`、日志、sidecar token 或 mobile gateway 私钥；恢复到新目录后可能产生新的 gateway 证书身份，需要撤销旧设备并重新配对。

仍需真实非空 v10 用户工作区的人工演练、加密异地副本、定期恢复抽检和应用内恢复向导。

## 11. Desktop UI 与产品路径

### 已实现

- Home / Libraries / Inbox / Search；
- 全局 Capture；
- Quick note 编辑和归类；
- Library Sources / Ask / Review / Outputs；
- 录音独立工作区和应用内最小化；
- Source 原件、网页快照与 OCR 状态；
- Output 历史与 provenance；
- mobile gateway 配对入口。

### UI 验收边界

当前源码的 production bundle 已在真实 Chrome 中连接一次性随机 loopback 后端，完成 32/32 项 UI→backend E2E，覆盖 Home、统一 Capture、Inbox、新建 Library、Source、Ask/Review/accepted KnowledgeUnit 与持久 Outputs。另完成 1280×800、900×640 与 390×844 的人工响应式走查。浏览器验收仍不是 Tauri 壳、原生文件/麦克风权限或真实设备 E2E。

## 12. 发布候选与平台边界

| 平台 | 当前结论 | 最终产物信息 |
| --- | --- | --- |
| macOS | Tauri + bundled Helper 工程候选；App/Helper arm64，`LSMinimumSystemVersion=11.0`；audio-input entitlement 与麦克风用途说明存在；ad-hoc 深度签名、DMG verify 与只读挂载通过；正式分发仍需 Developer ID/notarization/干净机器 | `Gunther_0.1.0_macOS_arm64.dmg`；33,831,087 bytes；SHA-256 `dd0b8a7aee22447a0ae70dd2262c4b958da7b1e4241ae9c837517eda186815d9` |
| Android | Flutter debug 候选；Share ingress/outbox/录音与安全连接已进入代码；V2 Debug 签名不是正式 release/AAB | `Gunther_0.1.0_Android_debug.apk`；178,653,480 bytes；SHA-256 `e4325238000a8682e99f8f37d3dc51d5e9970d37682a5c327b46f207181434c1` |
| iOS | 源码含 Runner 与 Share Extension；Swift parse 与 plist/entitlement/project 静态检查通过；没有完整 Xcode，未 build/archive/sign | 无 iOS 二进制候选 |

Android package 为 `com.gunther.gunther_mobile`，版本 `0.1.0 (1)`，`minSdk=24`、`targetSdk/compileSdk=36`，包含 arm64-v8a、armeabi-v7a、x86_64；V2 debug signer 证书 SHA-256 为 `5721c03bf2a2ec7acd33c0e27fc03bd0290395bcde1993a040d7fe039e7aeffb`。两份候选的可机器校验清单见 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS)。

## 13. 正式发布阻断项

- macOS Developer ID/notarization、Android release keystore/AAB、iOS archive/sign；
- macOS 首次录音仍需用户主动点击并在 TCC 提示中允许；当前用户未授权，最终包的真实麦克风采集尚未验收；
- Android/iOS 真机或模拟器安装、启动、权限、Share Sheet、相机、文件与录音；
- 当前 Web Research Provider 未配置，没有把“入口已实现”写成“在线研究已经实机返回结果”；
- 安全 gateway 跨物理手机验收；
- 真实连续长时麦克风与强杀/休眠/锁屏/断网/磁盘压力/STT 故障注入；
- 真实非空 v10 数据人工恢复演练；
- 系统后台/锁屏持续录音（若决定成为产品承诺）；
- 发布隐私说明、Provider 数据流说明和用户可理解的备份/证书恢复提示。

最终工程证据：Backend 177 passed/1 skipped；Desktop 74 passed；Rust 10 passed；Flutter 108 passed、analyze 0 issues；Chrome UI→backend 32/32；冻结 Helper 随机 loopback 启动 1 passed。候选哈希以 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS) 为准。

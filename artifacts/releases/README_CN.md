# Gunther v0.1.0 工程候选交付说明

日期：2026-08-30  
对应数据库：schema v10

本目录提供与当前源码一致的 macOS Apple Silicon 与 Android 工程候选。两份文件均已重新构建并校验，但它们不是已经完成商店签名、公证、真机安装与公开分发验收的正式发行版。

## 交付文件

| 平台 | 文件 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| macOS Apple Silicon | `Gunther_0.1.0_macOS_arm64.dmg` | 33,831,087 bytes | `dd0b8a7aee22447a0ae70dd2262c4b958da7b1e4241ae9c837517eda186815d9` |
| Android debug | `Gunther_0.1.0_Android_debug.apk` | 178,653,480 bytes | `e4325238000a8682e99f8f37d3dc51d5e9970d37682a5c327b46f207181434c1` |

在本目录执行以下命令可重新校验两份文件：

```bash
shasum -a 256 -c SHA256SUMS
```

## 与当前源码一致的回归证据

- Backend：177 passed、1 skipped；唯一跳过项是可选的 frozen release helper 测试。
- Desktop：74 passed；TypeScript typecheck 与 Vite production build 通过。
- Rust sidecar：10 passed；最终原生构建通过。
- Flutter：108 passed；`flutter analyze` 为 0 issues；Android debug APK 构建通过。
- 真实 Chrome 加载 production bundle，并连接一次性随机 loopback 后端，完成 32/32 项 UI→backend E2E；没有使用或中断用户正在运行的 8787 服务。
- 安装版针对真实课程录音 Source 的 Ask 返回 1 个 grounded citation；当该 Source 没有结构化 Assertion 时，引用来自保存的原始 source text，`assertionId=null`。
- 正式 restore CLI 已通过自动化闭环：先 verify，只恢复到不存在的新目录，恢复后再次验证数据库、Asset、Recording ledger/file 与 Artifact 完整性。

这些测试覆盖源码、浏览器产品路径和冻结 Helper 的工程边界，不等同于 Tauri 原生权限、移动真机或长时间运行验收。

## macOS 候选

DMG 已通过 `hdiutil verify` 和只读挂载检查，其中包含 `Gunther.app` 与 `/Applications` 链接。App 主程序与包内 `GuntherBackend` Helper 都是 arm64，`LSMinimumSystemVersion=11.0`；App/Helper 的 `codesign --verify --deep --strict` 均通过。App 签名 entitlement 含 `com.apple.security.device.audio-input=true`，Info.plist 含 `NSMicrophoneUsageDescription`。冻结 Helper 还在系统分配的随机 loopback 端口完成了真实启动测试（1 passed），没有访问 8787。

当前候选已安装到 `/Applications/Gunther.app` 并成功启动；主程序、Bundled Helper、`127.0.0.1:8787` 本机监听和带认证的 `/api/health` 均正常，健康响应识别到 `sensevoice-small`。安装时保留了此前版本的可恢复副本。由于本轮原生检查时 Mac 处于锁屏，菜单栏和独立 Capture 窗口的人工点击验收仍以源码测试和解锁后的用户走查为边界。

桌面 sidecar 的关键安全边界包括：

- 只绑定 loopback，并在发布就绪凭据前预绑定端口；
- 同一数据目录有 OS 单实例锁；
- 每次启动生成新的 256-bit token，并以 `backend-ready/backend-auth-token.<launchNonce>` 私有文件发布；
- token、Origin、workspace 与管理面权限都经过校验；
- 数据目录、Asset 与 Recording 路径遇到 symlink 或异常文件类型时 fail closed。

此前安装版已实际触发 macOS TCC 麦克风提示，但用户未授权。最终版在请求授权时最多等待 15 秒，结束等待会清理状态，并提供系统设置指引和 Retry；首次录音仍必须由用户主动点击并在系统提示中允许，应用不会绕过 TCC。上述权限声明与提示证据不能替代最终包的真实麦克风采集验收。

本包使用 ad-hoc 签名。它**没有** Apple Developer ID 签名或 notarization，也没有完成干净机器 Gatekeeper 验收，因此不能作为公开互联网分发包。SenseVoice 是外部本机服务，不包含在 DMG 中；本轮已确认本机 `127.0.0.1:8765` 健康，但由于用户未授予麦克风权限，未把健康检查表述为完整的麦克风→SenseVoice 端到端验收。

## Android 候选

APK 元数据：

- package：`com.gunther.gunther_mobile`；
- version：`0.1.0 (1)`；
- `minSdk=24`、`targetSdk=36`、`compileSdk=36`；
- ABI：arm64-v8a、armeabi-v7a、x86_64；
- APK Signature Scheme v2：通过；
- Android Debug 证书 SHA-256：`5721c03bf2a2ec7acd33c0e27fc03bd0290395bcde1993a040d7fe039e7aeffb`。

移动端已经实现 durable outbox v3、workspace/profile 固定、Android `ACTION_SEND` / `ACTION_SEND_MULTIPLE`、iOS Share Extension、录音恢复记录以及安全 HTTPS 配对。Share Sheet 已进入源码与自动化/静态检查范围，不能再表述为“未实现”；当前缺少的是 Android/iOS 真机或模拟器运行验收。Debug 签名不适用于商店发布，尚无 release keystore 或 AAB。

## 数据完整性与存储预算

当前 `LATEST_SCHEMA_VERSION=10`，migration 10 为 `immutable_artifact_history`。Asset 下载、Recording chunk ledger、Artifact snapshot/provenance/bindings、backup/verify/restore 都会交叉校验大小和 SHA-256；检测到不一致时拒绝继续。

Asset 与 Recording 共享存储预算：默认总配额 20 GiB，并保留至少 1 GiB 可用磁盘，可通过 `STORAGE_QUOTA_BYTES` 与 `STORAGE_MIN_FREE_BYTES` 调整。超出单请求限制返回 413，配额或磁盘空间不足返回 507；未知长度流、失败回滚、临时文件清理和 symlink 防护都有自动化覆盖。

正式恢复命令只接受不存在的新目标目录，不覆盖当前数据：

```bash
npm run verify:backup -- /备份目录/gunther-backup-...
npm run restore:data -- /备份目录/gunther-backup-... --data-dir /新的不存在目录
```

当前 v10 自动化恢复证据覆盖非空 Asset、完成态 Recording 与不可变 Artifact。早期 v6→v9 旧库迁移仅保留为历史兼容性证据，不是当前交付依赖，详见[受控恢复演练](../validation/restore-drill/README_CN.md)。

## 已知边界

- 没有 Android/iOS 真机或模拟器安装、权限、Share Sheet、拍照、录音、离线恢复或跨设备配对证据。
- 当前机器没有完整 Xcode，因此 iOS 未 build/archive/sign；只完成 Swift parse 与 plist/entitlement/project 静态检查。
- 没有 Apple Developer ID/notarization、Android release keystore/AAB，也没有 iOS 分发签名。
- macOS 首次麦克风授权仍需要用户点击允许；当前用户未授权，最终包的真实录音与音质尚未验收。
- 系统后台/锁屏持续录音尚未实现；应用内最小化不能等同于 OS 后台录音。
- OCR 已实现 Apple Vision Helper 与 Tesseract fallback；当前仍是有界同步处理，没有可恢复异步 OCR Job/retry。
- 没有真实连续 4 小时麦克风、强杀、休眠/锁屏、断网、磁盘压力或完整故障注入证据。
- SenseVoice 必须作为外部本机服务单独部署和配置。
- 当前 Web Research Provider 未配置；入口和失败边界已实现，但没有本轮在线研究结果的实机证据。
- Artifact 尚无归档、全局 Search、PDF/DOCX renderer 与完整版本 diff。
- restore CLI 已完成安全自动化闭环，但仍没有应用内恢复向导、真实用户数据人工演练或加密异地备份策略。

完整能力、证据口径与限制见[验收报告](../../docs/ACCEPTANCE_REPORT_V3_CN.md)。

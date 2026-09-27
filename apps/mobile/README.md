# Gunther Mobile

Gunther Mobile 是本地知识工作区的随身采集与轻量整理端。它与桌面共享同一个 workspace 和后端事实，不建立独立知识数据库。

一级导航只有：

- **Home**；
- **Libraries**；
- **Inbox**。

Capture 是全局动作，支持 Quick note、Document、Photo、Link、Recording 和 Web research。系统 Share Sheet 是同等级入口。Recording 因持续时间和恢复状态较多而使用专门页面，但不高于其他来源。

## 运行

仓库根目录：

```bash
npm run mobile:get
npm run mobile:analyze
npm run mobile:test
cd apps/mobile && flutter run
```

最终回归：Flutter 108 passed，`flutter analyze` 0 issues。Android debug APK 构建通过；iOS 因缺少完整 Xcode 未构建，只完成 Swift parse 与 plist/entitlement/project 静态检查。

## 开发 API

默认：

- Android emulator：`http://10.0.2.2:8787/api/`；
- iOS simulator：`http://127.0.0.1:8787/api/`。

也可为独立开发环境传入：

```bash
flutter run \
  --dart-define=GUNTHER_API_URL=https://YOUR-SECURE-BACKEND/api/ \
  --dart-define=GUNTHER_API_TOKEN=YOUR-DEVICE-TOKEN
```

这些 compile-time 值只用于开发桥接。正式物理手机应在应用内通过一次性配对获得 HTTPS connection profile、device bearer 与 CA/fingerprint 信任材料。不要把桌面 loopback 8787 改成无认证 LAN 服务。

## Capture 与 durable outbox v3

outbox 覆盖：

```text
quickNote / source / link / web / document / photo / audio
```

可靠性行为：

- 网络请求前先创建 durable entry；
- payload manifest 在文件锁内原子替换；
- Document/Photo/Audio 流式复制到 app-owned 文件，记录大小与 SHA-256；
- 文件/图片上限 512 MB，音频上限 2 GB；
- 每次读取 staged original 前复核受控路径、普通文件、大小和哈希；
- uploading 中断恢复为 retryable；
- 服务器明确确认后才进入清理流程；
- cleanup journal 让“服务已确认但 App 在删除文件前退出”仍可继续安全清理；
- manifest 或 cleanup journal 损坏时 fail closed；隔离损坏元数据需要用户确认；
- v1/v2 只在校验安全后迁移到 v3，异常时保留原清单与文件。

### Workspace / profile 边界

每个 outbox entry 同时绑定 `targetWorkspaceId + connectionProfileId`：

- 有已验证连接时，采集同步固定当前目标；
- 系统分享发生时尚未配对，可先保持 unassigned；
- unassigned entry 只能在第一次上传前绑定；
- 一旦绑定，切换 profile 不能改变目标；
- 异步等待、重试或 App 重启不能把内容写入另一 workspace。

## Share Sheet

### Android

原生 Kotlin ingress 处理冷启动和运行中的：

- `ACTION_SEND`；
- `ACTION_SEND_MULTIPLE`；
- HTTP/HTTPS URL；
- plain text；
- 单/多文件；
- 单/多图片；
- audio。

Provider 内容先复制到应用受控目录。主 Flutter App 读取 native manifest，将内容交给 outbox；outbox 成功提交后 native 才 ACK。

### iOS

源码包含真实 Share Extension target：

- App Group 共享 staging；
- text、URL、file URL、单/多图片与文件；
- cold-start 保存；
- `gunther://share` 唤醒；
- 主 App 前台 re-fetch；
- native queue 损坏时拒绝新工作并保留原件。

`systemShareId` 防止 native item 重复进入 outbox。Share Sheet 不依赖先下载远端 URL；URL 作为 Link 交由安全网页快照 API 处理。

### 运行验收边界

Android/iOS 原生代码、manifest/project/entitlement 与 Dart coordinator 有自动化或静态证据，但仍需真实设备/模拟器从浏览器、相册、文件 App 进行分享，验证多文件、取消、慢 Provider、系统回收、空间不足和权限行为。

## Photo 与 OCR

- 相机/相册原件先进入 outbox；
- Android `image_picker` 进程中断结果通过独立恢复队列交给 outbox；
- 服务端保存 Asset 后执行本地 OCR；
- 图片与扫描 PDF 可产生页/region/Provider/置信度 provenance；
- OCR unavailable/limit/partial failure 时原件仍安全；Capture 响应标记 degraded，Source 正文保留 provenance 与处理说明。

OCR 在后端执行，不在手机端重复运行；完全离线时先保留原件，连接恢复后再上传处理。

## Recording

### 当前实现

- 前台 PCM 采集并写 durable WAV；
- pause / resume / stop；
- PCM 并行进入 live transcription client；
- 音频保存与实时 STT 是独立失败边界；
- transcript、duration、moments、lecture/meeting/memo 与 Library target；
- 8 MB 上传分片、SHA-256 与 RecordingSession ledger；
- active recording 原子 recovery record；
- 重启后恢复为 local-only 文件并允许重试，不声称录音仍在后台继续；
- 应用内最小化与再次展开；
- 完成后创建 Source 并进入 Inbox/Library。

### 诚实边界

- “应用内最小化”不是 iOS/Android 系统后台或锁屏持续录音；
- 没有 `UIBackgroundModes` 录音承诺；
- 需要物理手机验证麦克风权限、音质、蓝牙/路由变化和网络切换；
- 需要真实连续长时麦克风、强杀、休眠/锁屏、断网、磁盘压力和 STT 故障注入；
- 自动化 PCM/WAV 和稀疏长时文件边界不能证明真实长时“零丢失”。

## 安全连接

移动端实现：

- connection profile catalog；
- 一次性 pairing bundle；
- device bearer；
- revoke / auth expired / incompatible / certificate changed 状态；
- token 与 CA 只进入 secure storage，不进入普通 profile、日志或 UI；
- system trust、private CA 或 pinned SHA-256 fingerprint；
- live transcription 与普通 API 使用同一已验证 profile transport。

桌面 Helper 提供独立私网 HTTPS gateway；打包 loopback sidecar 仍只监听本机。代码和自动化不等于跨物理手机验收，正式使用前必须验证发现、配对、证书变化、撤销、换网、离线与恢复。

## iOS 工程边界

源码包含 Runner、Share Extension、App Group、用途说明、scheme/project/storyboard 与无 alpha 的 AppIcon。AppIcon alpha 风险已经修复：图标为 RGB，1024 图标不含 alpha。

如果当前机器没有完整 Xcode、iPhoneOS SDK 与 simulator，就不能声称：

- `flutter build ios`；
- archive / sign；
- simulator / real-device install；
- Share Extension 实际加载；
- 权限与录音运行验收。

源码级 Swift/plist/project 检查不能替代这些门槛。

## Android 工程边界

源码声明网络与录音权限，并限制 cleartext 开发地址。当前 debug APK 已构建并校验，但不是 release keystore/AAB 或商店候选。

当前工程候选已记录：

- 文件：`artifacts/releases/Gunther_0.1.0_Android_debug.apk`；
- package/version：`com.gunther.gunther_mobile` / `0.1.0 (1)`；
- SDK：min 24、target/compile 36；
- ABI：arm64-v8a、armeabi-v7a、x86_64；
- 签名：APK Signature Scheme v2、Android Debug；证书 SHA-256 `5721c03bf2a2ec7acd33c0e27fc03bd0290395bcde1993a040d7fe039e7aeffb`；
- 大小：178,653,480 bytes；文件 SHA-256 `e4325238000a8682e99f8f37d3dc51d5e9970d37682a5c327b46f207181434c1`。

尚缺模拟器/真机安装、启动、Capture/Share/Recording/离线恢复日志；这些运行证据不能由 debug 构建替代。

## 目录

```text
lib/
  core/                         config/theme
  data/models/                  Capture、Source、Connection DTO
  data/services/                outbox、share、recording、secure connection
  data/repositories/            API 与 profile-aware repository
  features/capture/             Capture launcher/workspace
  features/home/                Home
  features/libraries/           Libraries
  features/inbox/               Inbox
  features/connection/          pairing / profiles
android/                        Kotlin share ingress 与 manifest
ios/Runner/                     主 App ingress
ios/ShareExtension/             iOS Share Extension
test/                           Widget、service、native contract tests
```

## 已知未完成

- 系统后台/锁屏持续录音；
- Android/iOS 真实运行级验收；
- 完整 Xcode iOS build/archive/sign；
- 正式 Android release signing/AAB；
- 跨物理手机安全 gateway 验收；
- 全离线时 Home/Libraries/Inbox 的完整只读降级体验；
- 从共享 OpenAPI 自动生成 Dart contracts。

# 本机构建与安装（2026-11-28）

基于 main 的 `56aee4eb67092e9f1b91a76b2f54a61df8613007` 构建。构建包含此前本地桌面布局改动、长标题/来源行导致表格拉宽的修复、空听写自动收起，以及移动录音写入队列的 `Future<void>` 类型修复。

## 当前安装包

| 平台 | 文件 | 版本 | 大小 |
| --- | --- | --- | ---: |
| macOS Apple Silicon | `Gunther_0.1.3_aarch64.dmg` | 0.1.3 | 1,171,865,539 bytes |
| Android | `Gunther_0.1.0_Android_debug.apk` | 0.1.0 (1) | 156,307,906 bytes |

在本目录运行 `shasum -a 256 -c SHA256SUMS` 校验两份文件。

桌面应用已安装到 `/Applications/Gunther.app` 并启动。签名校验、安装包校验、带认证的后端健康检查均通过；已核对安装后配置文件哈希不变；已确认打开原 workspace，安全移动网关正在运行。安装前后数据库均通过 `PRAGMA quick_check`，92 张表的行数完全一致，共 134,705 行。数据库 schema 为 v34。

macOS 安装包采用本地 ad-hoc 签名。安装卷包含 Gunther 应用与 Applications 快捷方式，已移除多余的隐藏卷图标。

## Android 安装与配对

1. 将 APK 传到手机，打开文件并允许所用文件管理器安装应用。
2. Mac 与手机连接同一私有网络。在桌面 Settings → Devices 中生成配对码，并复制全部连接详情。
3. 在手机的 workspace connection 中导入配对详情并完成连接。桌面应用需保持运行才能同步。

APK 为可侧载的 debug 构建，使用现有 Android Debug 证书签名；APK Signature Scheme v2 校验通过。包名 `com.gunther.gunther_mobile`，最低 Android 7.0（API 24），target/compile SDK 36，包含 arm64-v8a、armeabi-v7a 和 x86_64。

签名证书 SHA-256：`5721c03bf2a2ec7acd33c0e27fc03bd0290395bcde1993a040d7fe039e7aeffb`。

手机上的安装、权限、分享入口、录音和跨设备同步仍需在真实手机上验证。

## 本次验证

- Desktop：93 个关联 dictation/report 测试通过；TypeScript 与 Vite production build 通过。
- 报告布局：Chromium 与 WebKit 各 31 个场景通过，覆盖窄栏、宽栏、双倍字号、长标题/来源、表格滚动、时间线与目录；真实保存报告已用 WebKit 验证。
- 本次更新涉及的 Backend dictation / realtime / transcription：68 个测试通过。
- Rust：59 个单元测试通过，1 个图标导出工具测试按声明忽略；release 构建通过。原生窗口测试验证空听写收起、不写剪贴板及下一次正常打开。
- Flutter：analysis 无问题，117 个测试通过，Android APK 构建通过。
- 冻结 Helper：隔离 workspace 的启动、私有 token 与健康检查通过；Studio 打包检查通过。

已删除旧应用备份与重复构建产物，当前安装包集中在本目录。原有 workspace 数据备份保留。安装前另创建并验证了一份 workspace 备份，位置为本次任务的私有临时目录。

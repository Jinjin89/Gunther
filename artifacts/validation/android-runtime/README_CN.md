# Android 运行级验收环境盘点

日期：2026-08-30

## 结论

当前机器具备 Android SDK、`adb` 与 `emulator` 二进制，但没有已创建的 AVD、Android system image、`avdmanager` 或已连接设备。因此本轮不能提供 APK 安装、启动、权限、录音、导航、离线恢复或截图证据；未把静态检查冒充运行验收，也没有下载大型系统镜像。

## 权威检查结果

```text
emulator -list-avds                 → 0 个 AVD
Android SDK system-images/          → 不存在
avdmanager                          → 不存在
adb devices -l                      → 无设备
```

临时启动的 `adb` 服务已停止；没有创建、擦除或修改 AVD，也没有操作真实手机。

## 已有 APK 静态证据

```text
file: artifacts/releases/Gunther_0.1.0_Android_debug.apk
size: 178653480 bytes
SHA-256: e4325238000a8682e99f8f37d3dc51d5e9970d37682a5c327b46f207181434c1
package: com.gunther.gunther_mobile
version: 0.1.0 (1)
SDK: minSdk 24, targetSdk 36, compileSdk 36
ABI: arm64-v8a, armeabi-v7a, x86_64
permissions: android.permission.INTERNET, android.permission.RECORD_AUDIO
signature: APK Signature Scheme v2, Android Debug (1 signer)
signer certificate SHA-256: 5721c03bf2a2ec7acd33c0e27fc03bd0290395bcde1993a040d7fe039e7aeffb
```

与该候选对应的 Flutter 回归为 108 passed，`flutter analyze` 为 0 issues，debug APK 构建通过。Android `ACTION_SEND` / `ACTION_SEND_MULTIPLE`、durable outbox v3 与 Share ingress 已进入源码和自动化范围；本页缺少的是系统分享、权限和录音的真实运行证据，不是功能“未实现”。Debug 证书不适用于商店发布，当前也没有 release keystore 或 AAB。

## 继续条件

需要先通过 Android Studio 安装匹配本机架构的 Android system image，并创建一个专用测试 AVD；或者连接一台明确授权用于验收的 Android 设备。大型系统镜像下载与真实设备操作均不在本轮自动执行范围内。

# iOS 源码与构建环境验收

日期：2026-08-30

## 构建环境结论

当前机器只有 Apple Command Line Tools，没有完整 Xcode、iPhoneOS SDK、`simctl` 或可用 iOS Simulator。因此没有执行或声称 `flutter build ios`、archive、sign、安装或真机运行。

```text
xcode-select -p                       → /Library/Developer/CommandLineTools
xcodebuild -version                   → requires Xcode
xcrun --sdk iphoneos --show-sdk-path  → SDK iphoneos cannot be located
xcrun simctl list devices available   → simctl unavailable
```

## 已通过的源码级检查

- Flutter analyze：0 issue；
- Flutter tests：108/108；
- `xcrun swiftc -parse`：AppDelegate、SceneDelegate、ShareViewController、ShareIngressQueue 通过；
- `plutil -lint`：Runner/Share 的 Info.plist、entitlements 与 project.pbxproj 通过；
- scheme、workspace XML、Main / LaunchScreen storyboard：XML 通过；
- AppIcon manifest 的 15 个文件尺寸与引用一致；
- iOS deployment target 15.0 与当前四个原生插件最低版本兼容；
- 相机、麦克风、照片与本地网络用途说明存在；ATS 仅允许 local networking；
- 未声明 `UIBackgroundModes`，与“尚未交付后台 / 锁屏持续录音”边界一致。

iOS Share Extension、App Group ingress、URL scheme 唤醒与主 App re-fetch 已进入源码和静态检查范围。这里仍不能声称 Share Sheet 已在模拟器或真机加载、分享成功；缺失的是运行验收，不是 Share Sheet 源码实现。

## AppIcon alpha 修复

验收发现全部 15 个 AppIcon PNG 携带近乎不透明的 alpha 通道，1024×1024 marketing icon 也包含该通道。发布上传可能因此被拒绝。

已使用 `scripts/strip_png_alpha.py` 将 15 个图标原子转换为三通道 RGB PNG。工具只接受 alpha 最小值不低于 254 的图像，并逐个验证：

- 尺寸不变；
- 每一个 RGB sample 的 SHA-256 不变；
- 输出 bands 精确为 `R, G, B`；
- 1024×1024 图标经 `sips -g hasAlpha` 返回 `no`。

该修复只移除文件通道，没有重新设计或重绘图标。

## 剩余门槛

安装完整 Xcode 15+ 后仍必须实际完成 simulator build、archive、签名、权限弹窗、录音、网络、配对和真机测试。源码级检查不能替代这些运行证据。

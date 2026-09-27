import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private let shareQueue = GuntherShareIngressQueue()
  private var shareChannel: FlutterMethodChannel?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: "com.gunther.mobile/share_ingress",
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self else { return result(FlutterError(code: "unavailable", message: nil, details: nil)) }
      do {
        switch call.method {
        case "getPendingShares":
          result(try self.shareQueue.pending().map { $0.dartValue() })
        case "acknowledgeShares":
          guard let ids = call.arguments as? [String], ids.allSatisfy({ self.safeShareID($0) }) else {
            return result(FlutterError(code: "invalid_ids", message: "Share acknowledgement ids were invalid.", details: nil))
          }
          try self.shareQueue.acknowledge(Set(ids))
          result(nil)
        case "getShareDiagnostics":
          result(try self.shareQueue.diagnostics())
        case "quarantineDamagedShareQueue":
          let arguments = call.arguments as? [String: Any]
          try self.shareQueue.quarantineDamagedQueue(
            userConfirmed: arguments?["userConfirmed"] as? Bool == true
          )
          result(nil)
        default:
          result(FlutterMethodNotImplemented)
        }
      } catch {
        result(FlutterError(code: "share_queue_error", message: error.localizedDescription, details: nil))
      }
    }
    shareChannel = channel
  }

  func notifySharedItemsAvailable() {
    shareChannel?.invokeMethod("sharedItemsAvailable", arguments: nil)
  }

  private func safeShareID(_ value: String) -> Bool {
    guard (8...180).contains(value.count) else { return false }
    return value.range(of: "^[A-Za-z0-9._-]+$", options: .regularExpression) != nil
  }
}

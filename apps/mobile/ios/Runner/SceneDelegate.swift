import Flutter
import UIKit

class SceneDelegate: FlutterSceneDelegate {
  override func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
    super.scene(scene, openURLContexts: URLContexts)
    if URLContexts.contains(where: { $0.url.scheme == "gunther" && $0.url.host == "share" }) {
      (UIApplication.shared.delegate as? AppDelegate)?.notifySharedItemsAvailable()
    }
  }

  override func sceneWillEnterForeground(_ scene: UIScene) {
    super.sceneWillEnterForeground(scene)
    (UIApplication.shared.delegate as? AppDelegate)?.notifySharedItemsAvailable()
  }
}

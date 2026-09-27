import CryptoKit
import Foundation

struct GuntherPendingShare: Codable {
  let id: String
  let kind: String
  let text: String?
  let path: String?
  let fileName: String?
  let mediaType: String?
  let sizeBytes: Int64?

  func dartValue() -> [String: Any] {
    var value: [String: Any] = ["id": id, "kind": kind]
    if let text { value["text"] = text }
    if let path { value["path"] = path }
    if let fileName { value["fileName"] = fileName }
    if let mediaType { value["mediaType"] = mediaType }
    if let sizeBytes { value["sizeBytes"] = sizeBytes }
    return value
  }
}

enum GuntherShareIngressError: LocalizedError {
  case appGroupUnavailable
  case fileTooLarge
  case stagingTimedOut
  case invalidSource
  case confirmationRequired
  case queueHealthy

  var errorDescription: String? {
    switch self {
    case .appGroupUnavailable:
      return "Gunther shared storage is not available. The original was not changed."
    case .fileTooLarge:
      return "This Share Sheet item exceeds the 100 MB extension limit. The original was not changed; use Capture inside Gunther for larger files."
    case .stagingTimedOut:
      return "This Share Sheet item could not be copied within 20 seconds. The original was not changed; use Capture inside Gunther instead."
    case .invalidSource:
      return "The shared provider did not supply a readable original."
    case .confirmationRequired:
      return "Explicit confirmation is required."
    case .queueHealthy:
      return "The native share queue is healthy."
    }
  }
}

final class GuntherShareIngressQueue {
  static let appGroup = "group.com.gunther.guntherMobile"
  // Share extensions have a short, memory-constrained lifecycle. In-app
  // capture retains the larger 512 MB / 2 GB limits; extension copying is
  // intentionally capped so iOS can fail visibly before terminating it.
  static let maximumExtensionBytes: Int64 = 100 * 1024 * 1024
  static let maximumExtensionSeconds: TimeInterval = 20

  private let manager = FileManager.default
  private let encoder = JSONEncoder()
  private let decoder = JSONDecoder()

  private var root: URL {
    get throws {
      guard let container = manager.containerURL(
        forSecurityApplicationGroupIdentifier: Self.appGroup
      ) else { throw GuntherShareIngressError.appGroupUnavailable }
      let root = container.appendingPathComponent("share-ingress", isDirectory: true)
      try manager.createDirectory(at: root, withIntermediateDirectories: true)
      return root
    }
  }

  func pending() throws -> [GuntherPendingShare] {
    try coordinated { try readUnlocked(root: $0) }
  }

  func merge(_ items: [GuntherPendingShare]) throws {
    guard !items.isEmpty else { return }
    try coordinated { root in
      var byID = Dictionary(uniqueKeysWithValues: try readUnlocked(root: root).map { ($0.id, $0) })
      for item in items where byID[item.id] == nil { byID[item.id] = item }
      try writeUnlocked(Array(byID.values), root: root)
    }
  }

  func acknowledge(_ ids: Set<String>) throws {
    guard !ids.isEmpty else { return }
    try coordinated { root in
      let filesRoot = root.appendingPathComponent("files", isDirectory: true).standardizedFileURL.path + "/"
      var kept: [GuntherPendingShare] = []
      var acknowledgedFiles: [URL] = []
      for item in try readUnlocked(root: root) {
        guard ids.contains(item.id) else { kept.append(item); continue }
        if let path = item.path {
          let file = URL(fileURLWithPath: path).standardizedFileURL
          if file.path.hasPrefix(filesRoot) { acknowledgedFiles.append(file) }
        }
      }
      // Manifest acknowledgement commits before cleanup so an interrupted
      // write never points at a native original that was already deleted.
      try writeUnlocked(kept, root: root)
      for file in acknowledgedFiles { try? manager.removeItem(at: file) }
    }
  }

  func diagnostics() throws -> [String: Any] {
    let root = try root
    let quarantine = root.appendingPathComponent("quarantine", isDirectory: true)
    let quarantinedCount = (try? manager.contentsOfDirectory(
      at: quarantine,
      includingPropertiesForKeys: nil
    ).count) ?? 0
    do {
      return [
        "healthy": true,
        "pendingCount": try pending().count,
        "quarantinedQueueCount": quarantinedCount,
      ]
    } catch {
      return [
        "healthy": false,
        "pendingCount": 0,
        "quarantinedQueueCount": quarantinedCount,
        "issueCode": "native_manifest_corrupt",
      ]
    }
  }

  func quarantineDamagedQueue(userConfirmed: Bool) throws {
    guard userConfirmed else { throw GuntherShareIngressError.confirmationRequired }
    try coordinated { root in
      let manifest = root.appendingPathComponent("pending.json")
      guard manager.fileExists(atPath: manifest.path) else {
        throw GuntherShareIngressError.queueHealthy
      }
      do {
        _ = try readUnlocked(root: root)
        throw GuntherShareIngressError.queueHealthy
      } catch GuntherShareIngressError.queueHealthy {
        throw GuntherShareIngressError.queueHealthy
      } catch {
        let quarantine = root.appendingPathComponent("quarantine", isDirectory: true)
        try manager.createDirectory(at: quarantine, withIntermediateDirectories: true)
        let destination = quarantine.appendingPathComponent(
          "pending-\(Int(Date().timeIntervalSince1970 * 1_000))-\(UUID().uuidString).json"
        )
        // Preserve the exact damaged index and every staged original.
        try manager.moveItem(at: manifest, to: destination)
        try writeUnlocked([], root: root)
      }
    }
  }

  func textItem(_ text: String, suggestedKind: String? = nil) -> GuntherPendingShare {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let isWebURL: Bool = {
      guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased() else { return false }
      return (scheme == "http" || scheme == "https") && url.host != nil
    }()
    let kind = suggestedKind == "url" && isWebURL ? "url" : (isWebURL ? "url" : "text")
    let id = "ios-" + String(digest(Data("\(kind)\u{0}\(trimmed)".utf8)).prefix(40))
    return GuntherPendingShare(
      id: String(id), kind: kind, text: trimmed, path: nil,
      fileName: nil, mediaType: nil, sizeBytes: nil
    )
  }

  func copyFile(
    from source: URL,
    fileName proposedName: String,
    mediaType: String,
    position: Int
  ) throws -> GuntherPendingShare {
    guard source.isFileURL else { throw GuntherShareIngressError.invalidSource }
    if let size = try? source.resourceValues(forKeys: [.fileSizeKey]).fileSize,
       Int64(size) > Self.maximumExtensionBytes {
      throw GuntherShareIngressError.fileTooLarge
    }
    let root = try root
    let files = root.appendingPathComponent("files", isDirectory: true)
    try manager.createDirectory(at: files, withIntermediateDirectories: true)
    let temporary = files.appendingPathComponent(".incoming-\(UUID().uuidString).part")
    let input = try FileHandle(forReadingFrom: source)
    manager.createFile(atPath: temporary.path, contents: nil)
    let output = try FileHandle(forWritingTo: temporary)
    var hasher = SHA256()
    var total: Int64 = 0
    let maximumBytes = Self.maximumExtensionBytes
    let deadline = Date().addingTimeInterval(Self.maximumExtensionSeconds)
    do {
      while let data = try input.read(upToCount: 1024 * 1024), !data.isEmpty {
        if Date() >= deadline { throw GuntherShareIngressError.stagingTimedOut }
        total += Int64(data.count)
        if total > maximumBytes { throw GuntherShareIngressError.fileTooLarge }
        hasher.update(data: data)
        try output.write(contentsOf: data)
      }
      try output.synchronize()
      try input.close()
      try output.close()
      let fileName = safeFileName(proposedName)
      let contentHash = hasher.finalize().map { String(format: "%02x", $0) }.joined()
      let seed = Data("\(contentHash)\u{0}\(position)\u{0}\(fileName)\u{0}\(mediaType)".utf8)
      let id = "ios-" + String(digest(seed).prefix(40))
      let ext = URL(fileURLWithPath: fileName).pathExtension.lowercased()
      let storedName = ext.range(of: "^[a-z0-9]{1,12}$", options: .regularExpression) == nil
        ? String(id) : "\(id).\(ext)"
      let destination = files.appendingPathComponent(storedName)
      if !manager.fileExists(atPath: destination.path) {
        try manager.moveItem(at: temporary, to: destination)
      } else {
        try manager.removeItem(at: temporary)
      }
      return GuntherPendingShare(
        id: String(id), kind: "file", text: nil, path: destination.path,
        fileName: fileName, mediaType: mediaType, sizeBytes: total
      )
    } catch {
      try? input.close(); try? output.close(); try? manager.removeItem(at: temporary)
      throw error
    }
  }

  private func coordinated<T>(_ operation: (URL) throws -> T) throws -> T {
    let root = try root
    let coordinator = NSFileCoordinator(filePresenter: nil)
    var result: Result<T, Error>!
    var coordinationError: NSError?
    coordinator.coordinate(writingItemAt: root, options: .forMerging, error: &coordinationError) { url in
      result = Result { try operation(url) }
    }
    if let coordinationError { throw coordinationError }
    return try result.get()
  }

  private func readUnlocked(root: URL) throws -> [GuntherPendingShare] {
    let manifest = root.appendingPathComponent("pending.json")
    guard manager.fileExists(atPath: manifest.path) else { return [] }
    let items = try decoder.decode([GuntherPendingShare].self, from: Data(contentsOf: manifest))
    for item in items {
      let safeID = item.id.range(of: "^[A-Za-z0-9._-]{8,180}$", options: .regularExpression) != nil
      let validText = (item.kind == "text" || item.kind == "url") && !(item.text?.isEmpty ?? true)
      let validFile = item.kind == "file" && item.path != nil && item.fileName != nil
        && (item.mediaType?.contains("/") ?? false) && (item.sizeBytes ?? -1) >= 0
      guard safeID && (validText || validFile) else { throw GuntherShareIngressError.invalidSource }
    }
    return items
  }

  private func writeUnlocked(_ items: [GuntherPendingShare], root: URL) throws {
    let manifest = root.appendingPathComponent("pending.json")
    try encoder.encode(items).write(to: manifest, options: [.atomic, .completeFileProtectionUnlessOpen])
  }

  private func digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }

  private func safeFileName(_ value: String) -> String {
    let leaf = URL(fileURLWithPath: value).lastPathComponent
    let forbidden = CharacterSet.controlCharacters.union(CharacterSet(charactersIn: "/\\"))
    let safe = leaf.unicodeScalars.map { forbidden.contains($0) ? "_" : String($0) }.joined()
    return String(safe.prefix(180)).isEmpty ? "shared-file" : String(safe.prefix(180))
  }
}

import Social
import UniformTypeIdentifiers

final class ShareViewController: SLComposeServiceViewController {
  private let queue = GuntherShareIngressQueue()

  override func isContentValid() -> Bool { true }

  override func didSelectPost() {
    do {
      // Refuse new work before copying provider bytes if the shared manifest
      // is unreadable. Existing app-owned originals remain untouched.
      _ = try queue.pending()
    } catch {
      extensionContext?.cancelRequest(withError: error)
      return
    }
    let attachments = (extensionContext?.inputItems as? [NSExtensionItem] ?? [])
      .flatMap { $0.attachments ?? [] }
    let group = DispatchGroup()
    let lock = NSLock()
    var staged: [GuntherPendingShare] = []
    var stagingError: Error?
    var position = 0

    for provider in attachments {
      let currentPosition = position
      position += 1
      if let type = provider.registeredTypeIdentifiers.first(where: { identifier in
        guard let value = UTType(identifier) else { return false }
        return value.conforms(to: .item) && !value.conforms(to: .plainText) && !value.conforms(to: .url)
      }) {
        group.enter()
        provider.loadFileRepresentation(forTypeIdentifier: type) { [queue] url, providerError in
          defer { group.leave() }
          guard let url else {
            lock.lock()
            if stagingError == nil {
              stagingError = providerError ?? GuntherShareIngressError.invalidSource
            }
            lock.unlock()
            return
          }
          let name = provider.suggestedName ?? url.lastPathComponent
          let mediaType = UTType(type)?.preferredMIMEType ?? "application/octet-stream"
          do {
            let item = try queue.copyFile(from: url, fileName: name, mediaType: mediaType, position: currentPosition)
            lock.lock(); staged.append(item); lock.unlock()
          } catch {
            lock.lock(); if stagingError == nil { stagingError = error }; lock.unlock()
          }
        }
      } else if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
        group.enter()
        provider.loadItem(forTypeIdentifier: UTType.url.identifier) { [queue] value, providerError in
          defer { group.leave() }
          if let url = value as? URL, url.isFileURL {
            let name = provider.suggestedName ?? url.lastPathComponent
            let mediaType = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
              ?? "application/octet-stream"
            do {
              let item = try queue.copyFile(
                from: url, fileName: name, mediaType: mediaType, position: currentPosition
              )
              lock.lock(); staged.append(item); lock.unlock()
            } catch {
              lock.lock(); if stagingError == nil { stagingError = error }; lock.unlock()
            }
            return
          }
          let text = (value as? URL)?.absoluteString ?? (value as? String)
          if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lock.lock(); staged.append(queue.textItem(text, suggestedKind: "url")); lock.unlock()
          } else {
            lock.lock()
            if stagingError == nil {
              stagingError = providerError ?? GuntherShareIngressError.invalidSource
            }
            lock.unlock()
          }
        }
      } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
        group.enter()
        provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) { [queue] value, providerError in
          defer { group.leave() }
          if let text = value as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lock.lock(); staged.append(queue.textItem(text)); lock.unlock()
          } else {
            lock.lock()
            if stagingError == nil {
              stagingError = providerError ?? GuntherShareIngressError.invalidSource
            }
            lock.unlock()
          }
        }
      } else {
        lock.lock()
        if stagingError == nil { stagingError = GuntherShareIngressError.invalidSource }
        lock.unlock()
      }
    }

    let composed = contentText.trimmingCharacters(in: .whitespacesAndNewlines)
    if !composed.isEmpty { staged.append(queue.textItem(composed)) }
    group.notify(queue: .global(qos: .userInitiated)) { [weak self] in
      guard let self else { return }
      if staged.isEmpty && stagingError == nil {
        stagingError = GuntherShareIngressError.invalidSource
      }
      do {
        try self.queue.merge(staged)
      } catch {
        DispatchQueue.main.async {
          self.extensionContext?.cancelRequest(withError: error)
        }
        return
      }
      if let stagingError {
        DispatchQueue.main.async {
          self.extensionContext?.cancelRequest(withError: stagingError)
        }
        return
      }
      DispatchQueue.main.async {
        if let url = URL(string: "gunther://share") {
          self.extensionContext?.open(url, completionHandler: nil)
        }
        self.extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
      }
    }
  }

  override func configurationItems() -> [Any]! { [] }
}

package com.gunther.gunther_mobile

import android.content.Intent
import android.database.Cursor
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.system.Os
import android.webkit.MimeTypeMap
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference

class MainActivity : FlutterActivity() {
    companion object {
        private const val CHANNEL = "com.gunther.mobile/share_ingress"
        private const val MAX_ASSET_BYTES = 512L * 1024L * 1024L
        private const val MAX_AUDIO_BYTES = 2L * 1024L * 1024L * 1024L
        private val shareExecutor = Executors.newSingleThreadExecutor()
        private val shareStagingState = AtomicReference("idle")
    }

    private var shareChannel: MethodChannel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        stageShareIntentAsync(intent)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        shareChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL).also { channel ->
            channel.setMethodCallHandler { call, result ->
                when (call.method) {
                    "getPendingShares" -> {
                        shareExecutor.execute {
                            try {
                                val pending = readPending().map { it.toDartMap() }
                                runOnUiThread { result.success(pending) }
                            } catch (exception: Exception) {
                                runOnUiThread {
                                    result.error("share_queue_corrupt", exception.message, null)
                                }
                            }
                        }
                    }
                    "getShareStagingState" -> result.success(shareStagingState.get())
                    "getShareDiagnostics" -> {
                        shareExecutor.execute {
                            val diagnostics = shareDiagnostics()
                            runOnUiThread { result.success(diagnostics) }
                        }
                    }
                    "quarantineDamagedShareQueue" -> {
                        val confirmed = (call.arguments as? Map<*, *>)?.get("userConfirmed") == true
                        if (!confirmed) {
                            result.error("confirmation_required", "Explicit confirmation is required.", null)
                        } else {
                            shareExecutor.execute {
                                try {
                                    quarantineDamagedShareQueue()
                                    runOnUiThread { result.success(null) }
                                } catch (exception: Exception) {
                                    runOnUiThread {
                                        result.error("share_queue_repair_failed", exception.message, null)
                                    }
                                }
                            }
                        }
                    }
                    "acknowledgeShares" -> {
                        val ids = (call.arguments as? List<*>)?.filterIsInstance<String>()
                        if (ids == null || ids.any { !isSafeId(it) }) {
                            result.error("invalid_ids", "Share acknowledgement ids were invalid.", null)
                        } else {
                            shareExecutor.execute {
                                try {
                                    acknowledge(ids.toSet())
                                    runOnUiThread { result.success(null) }
                                } catch (exception: Exception) {
                                    runOnUiThread {
                                        result.error("ack_failed", exception.message, null)
                                    }
                                }
                            }
                        }
                    }
                    else -> result.notImplemented()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        stageShareIntentAsync(intent)
    }

    private fun stageShareIntentAsync(incoming: Intent?) {
        if (incoming == null || (incoming.action != Intent.ACTION_SEND && incoming.action != Intent.ACTION_SEND_MULTIPLE)) return
        updateShareStagingState("processing")
        shareExecutor.execute {
            val state = stageShareIntent(incoming)
            runOnUiThread {
                updateShareStagingState(state)
                if (state == "completed" || state == "partial_failure") {
                    shareChannel?.invokeMethod("sharedItemsAvailable", null)
                }
            }
        }
    }

    private fun updateShareStagingState(state: String) {
        shareStagingState.set(state)
        shareChannel?.invokeMethod("shareStagingState", mapOf("state" to state))
    }

    private fun stageShareIntent(incoming: Intent?): String {
        if (incoming == null || (incoming.action != Intent.ACTION_SEND && incoming.action != Intent.ACTION_SEND_MULTIPLE)) {
            return "failed"
        }
        return try {
            // Refuse to create new originals when the existing manifest cannot
            // be trusted. This keeps corruption strictly fail-closed.
            readPending()
            var acceptedAny = false
            var failedAny = false
            streamUris(incoming).forEachIndexed { index, uri ->
                try {
                    val staged = stageContentUri(uri, incoming.type, index)
                    if (staged == null) {
                        failedAny = true
                    } else {
                        staged.let {
                            mergePending(listOf(it))
                            acceptedAny = true
                        }
                    }
                } catch (_: Exception) {
                    // A broken provider item does not discard siblings that
                    // were already committed to the native queue.
                    failedAny = true
                }
            }
            val sharedText = incoming.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim()
            if (!sharedText.isNullOrEmpty()) {
                mergePending(listOf(stageText(sharedText)))
                acceptedAny = true
            }
            if (!acceptedAny) return "failed"
            // The durable queue is now authoritative through acknowledgement.
            incoming.action = null
            if (failedAny) "partial_failure" else "completed"
        } catch (_: Exception) {
            "failed"
        }
    }

    @Suppress("DEPRECATION")
    private fun streamUris(intent: Intent): List<Uri> {
        val result = mutableListOf<Uri>()
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let(result::addAll)
        } else {
            (intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri)?.let(result::add)
        }
        intent.clipData?.let { clip ->
            for (index in 0 until clip.itemCount) {
                clip.getItemAt(index).uri?.let { if (!result.contains(it)) result.add(it) }
            }
        }
        return result
    }

    private fun stageText(text: String): PendingShare {
        val kind = try {
            val uri = Uri.parse(text)
            if ((uri.scheme == "http" || uri.scheme == "https") && !uri.host.isNullOrBlank()) "url" else "text"
        } catch (_: Exception) { "text" }
        val id = "android-${sha256("$kind\u0000$text".toByteArray()).take(40)}"
        return PendingShare(id = id, kind = kind, text = text)
    }

    private fun stageContentUri(uri: Uri, fallbackType: String?, position: Int): PendingShare? {
        if (uri.scheme != "content" && uri.scheme != "file") return null
        val displayName = safeDisplayName(queryDisplayName(uri) ?: "shared-file-$position")
        val mediaType = contentResolver.getType(uri)
            ?: fallbackType?.takeIf { it.contains('/') && it != "*/*" }
            ?: mediaTypeFromName(displayName)
        val maximumBytes = if (mediaType.startsWith("audio/")) MAX_AUDIO_BYTES else MAX_ASSET_BYTES
        val files = File(ingressRoot(), "files").apply { mkdirs() }
        val temporary = File(files, ".incoming-${UUID.randomUUID()}.part")
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        try {
            contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        total += read
                        if (total > maximumBytes) throw IllegalArgumentException("Shared file exceeds its capture limit.")
                        digest.update(buffer, 0, read)
                        output.write(buffer, 0, read)
                    }
                    output.fd.sync()
                }
            } ?: return null
            val contentHash = digest.digest().joinToString("") { "%02x".format(it) }
            val id = "android-${sha256("$contentHash\u0000$position\u0000$displayName\u0000$mediaType".toByteArray()).take(40)}"
            val extension = displayName.substringAfterLast('.', "").lowercase()
                .takeIf { it.matches(Regex("[a-z0-9]{1,12}")) }
            val destination = File(files, if (extension == null) id else "$id.$extension")
            if (!destination.exists()) {
                if (!temporary.renameTo(destination)) {
                    temporary.copyTo(destination, overwrite = false)
                    // copyTo closes its stream but does not promise durable
                    // media. Sync the fallback destination before any manifest
                    // can refer to it.
                    FileOutputStream(destination, true).use { it.fd.sync() }
                }
            }
            return PendingShare(id, "file", path = destination.absolutePath, fileName = displayName, mediaType = mediaType, sizeBytes = total)
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }

    private fun mergePending(incoming: List<PendingShare>) {
        val merged = linkedMapOf<String, PendingShare>()
        readPending().forEach { merged[it.id] = it }
        incoming.forEach { merged.putIfAbsent(it.id, it) }
        writePending(merged.values.toList())
    }

    private fun acknowledge(ids: Set<String>) {
        if (ids.isEmpty()) return
        val filesRoot = File(ingressRoot(), "files").canonicalPath + File.separator
        val kept = mutableListOf<PendingShare>()
        val acknowledgedFiles = mutableListOf<File>()
        readPending().forEach { item ->
            if (!ids.contains(item.id)) kept.add(item)
            else item.path?.let { path ->
                val file = File(path).canonicalFile
                if (file.path.startsWith(filesRoot)) acknowledgedFiles.add(file)
            }
        }
        // Commit acknowledgement before best-effort cleanup. A crash may leave
        // an orphan for later maintenance, but can never leave a manifest entry
        // whose authoritative native file was already destroyed.
        writePending(kept)
        acknowledgedFiles.forEach { it.delete() }
    }

    private fun shareDiagnostics(): Map<String, Any?> {
        val quarantine = File(ingressRoot(), "quarantine")
        val quarantinedCount = quarantine.listFiles()?.count { it.isFile } ?: 0
        return try {
            val state = shareStagingState.get()
            mapOf(
                "healthy" to true,
                "pendingCount" to readPending().size,
                "quarantinedQueueCount" to quarantinedCount,
                "issueCode" to when (state) {
                    "partial_failure" -> "native_staging_partial_failure"
                    "failed" -> "native_staging_failed"
                    else -> null
                },
            )
        } catch (_: Exception) {
            mapOf(
                "healthy" to false,
                "pendingCount" to 0,
                "quarantinedQueueCount" to quarantinedCount,
                "issueCode" to "native_manifest_corrupt",
            )
        }
    }

    private fun quarantineDamagedShareQueue() {
        val root = ingressRoot()
        val manifest = File(root, "pending.json")
        if (!manifest.exists()) throw IllegalStateException("The native share queue is healthy.")
        try {
            readPending()
            throw IllegalStateException("The native share queue is healthy.")
        } catch (exception: Exception) {
            if (exception.message == "The native share queue is healthy.") throw exception
        }
        val quarantine = File(root, "quarantine").apply { mkdirs() }
        val destination = File(
            quarantine,
            "pending-${System.currentTimeMillis()}-${UUID.randomUUID()}.json",
        )
        // The exact damaged bytes are retained. No staged original is touched.
        Os.rename(manifest.path, destination.path)
        writePending(emptyList())
    }

    private fun readPending(): List<PendingShare> {
        val manifest = File(ingressRoot(), "pending.json")
        if (!manifest.exists()) return emptyList()
        return try {
            val array = JSONArray(manifest.readText(Charsets.UTF_8))
            buildList {
                for (index in 0 until array.length()) {
                    val value = PendingShare.fromJson(array.optJSONObject(index))
                        ?: throw IllegalStateException("The native share queue contains an invalid item at index $index.")
                    add(value)
                }
            }
        } catch (exception: Exception) {
            // Fail closed: callers must never turn a damaged manifest into an
            // empty queue and orphan app-owned originals by overwriting it.
            throw IllegalStateException(
                "The native share queue is damaged and was preserved for recovery.",
                exception,
            )
        }
    }

    private fun writePending(items: List<PendingShare>) {
        val root = ingressRoot()
        val destination = File(root, "pending.json")
        val temporary = File(root, "pending.json.tmp")
        val array = JSONArray()
        items.forEach { array.put(it.toJson()) }
        FileOutputStream(temporary).use { output ->
            output.write(array.toString().toByteArray(Charsets.UTF_8))
            output.fd.sync()
        }
        // rename(2) atomically replaces the previous manifest on Android's
        // app-private filesystem. If it fails, retain both files and surface
        // the error instead of falling back to a torn in-place copy.
        Os.rename(temporary.path, destination.path)
    }

    private fun ingressRoot(): File = File(filesDir, "share-ingress").apply { mkdirs() }

    private fun queryDisplayName(uri: Uri): String? {
        var cursor: Cursor? = null
        return try {
            cursor = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            if (cursor != null && cursor.moveToFirst()) cursor.getString(0) else null
        } catch (_: Exception) { null } finally { cursor?.close() }
    }

    private fun safeDisplayName(value: String): String {
        val leaf = value.substringAfterLast('/').substringAfterLast('\\')
        return leaf.replace(Regex("[\\u0000-\\u001f\\u007f]"), "_").take(180).ifBlank { "shared-file" }
    }

    private fun mediaTypeFromName(name: String): String {
        val extension = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension) ?: "application/octet-stream"
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun isSafeId(value: String): Boolean = value.length in 8..180 && value.matches(Regex("[A-Za-z0-9._-]+"))
}

private data class PendingShare(
    val id: String,
    val kind: String,
    val text: String? = null,
    val path: String? = null,
    val fileName: String? = null,
    val mediaType: String? = null,
    val sizeBytes: Long? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id); put("kind", kind)
        text?.let { put("text", it) }; path?.let { put("path", it) }
        fileName?.let { put("fileName", it) }; mediaType?.let { put("mediaType", it) }
        sizeBytes?.let { put("sizeBytes", it) }
    }

    fun toDartMap(): Map<String, Any> = buildMap {
        put("id", id); put("kind", kind)
        text?.let { put("text", it) }; path?.let { put("path", it) }
        fileName?.let { put("fileName", it) }; mediaType?.let { put("mediaType", it) }
        sizeBytes?.let { put("sizeBytes", it) }
    }

    companion object {
        fun fromJson(json: JSONObject?): PendingShare? {
            if (json == null) return null
            val id = json.optString("id"); val kind = json.optString("kind")
            if (id.length !in 8..180 || !id.matches(Regex("[A-Za-z0-9._-]+")) || kind !in setOf("text", "url", "file")) return null
            val item = PendingShare(
                id, kind,
                json.optString("text").takeIf { it.isNotEmpty() },
                json.optString("path").takeIf { it.isNotEmpty() },
                json.optString("fileName").takeIf { it.isNotEmpty() },
                json.optString("mediaType").takeIf { it.isNotEmpty() },
                if (json.has("sizeBytes")) json.optLong("sizeBytes") else null,
            )
            if (kind in setOf("text", "url") && item.text.isNullOrBlank()) return null
            if (kind == "file" && (item.path.isNullOrBlank() || item.fileName.isNullOrBlank() || item.mediaType?.contains('/') != true || item.sizeBytes == null || item.sizeBytes < 0)) return null
            return item
        }
    }
}

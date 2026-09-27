import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/capture.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/features/capture/presentation/capture_view_model.dart';

class CaptureFlowPage extends StatelessWidget {
  const CaptureFlowPage({
    required this.kind,
    required this.viewModel,
    required this.onCaptured,
    required this.onMinimizeRecording,
    super.key,
  });

  final CaptureKind kind;
  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;
  final VoidCallback onMinimizeRecording;

  @override
  Widget build(BuildContext context) {
    return switch (kind) {
      CaptureKind.quickNote => QuickNoteCapturePage(
        viewModel: viewModel,
        onCaptured: onCaptured,
      ),
      CaptureKind.link => LinkCapturePage(
        viewModel: viewModel,
        onCaptured: onCaptured,
      ),
      CaptureKind.web => WebCapturePage(
        viewModel: viewModel,
        onCaptured: onCaptured,
      ),
      CaptureKind.recording => RecordingWorkspacePage(
        viewModel: viewModel,
        onCaptured: onCaptured,
        onMinimize: onMinimizeRecording,
      ),
      CaptureKind.document || CaptureKind.photo => NativeCapturePage(
        kind: kind,
        viewModel: viewModel,
        onCaptured: onCaptured,
      ),
    };
  }
}

class QuickNoteCapturePage extends StatefulWidget {
  const QuickNoteCapturePage({
    required this.viewModel,
    required this.onCaptured,
    super.key,
  });

  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;

  @override
  State<QuickNoteCapturePage> createState() => _QuickNoteCapturePageState();
}

class _QuickNoteCapturePageState extends State<QuickNoteCapturePage> {
  final _title = TextEditingController();
  final _content = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    _content.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final content = _content.text.trim();
    if (content.isEmpty) return;
    final success = await widget.viewModel.saveQuickNote(
      QuickNoteDraft(
        title: _title.text.trim().isEmpty
            ? 'Untitled note'
            : _title.text.trim(),
        content: content,
      ),
    );
    if (!success || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return _CaptureScaffold(
      title: 'Quick note',
      child: AnimatedBuilder(
        animation: widget.viewModel,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const _OrganizeLaterBanner(),
            const SizedBox(height: 18),
            TextField(
              controller: _title,
              decoration: const InputDecoration(labelText: 'Title (optional)'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('quick-note-content'),
              controller: _content,
              minLines: 8,
              maxLines: 16,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: 'What do you want to remember?',
                alignLabelWithHint: true,
              ),
            ),
            _CaptureError(viewModel: widget.viewModel),
            const SizedBox(height: 18),
            FilledButton(
              key: const Key('save-quick-note'),
              onPressed: widget.viewModel.saving ? null : _save,
              child: Text(
                widget.viewModel.saving ? 'Saving…' : 'Save to Inbox',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class LinkCapturePage extends StatefulWidget {
  const LinkCapturePage({
    required this.viewModel,
    required this.onCaptured,
    super.key,
  });

  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;

  @override
  State<LinkCapturePage> createState() => _LinkCapturePageState();
}

class _LinkCapturePageState extends State<LinkCapturePage> {
  final _title = TextEditingController();
  final _url = TextEditingController();
  final _notes = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    _url.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    late final WebSnapshotDraft draft;
    try {
      draft = WebSnapshotDraft(
        url: _url.text,
        title: _title.text,
        notes: _notes.text,
      );
    } on FormatException catch (exception) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(exception.message.toString())));
      return;
    }
    final success = await widget.viewModel.saveWebSnapshot(draft);
    if (!success || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return _CaptureScaffold(
      title: 'Save a webpage',
      child: AnimatedBuilder(
        animation: widget.viewModel,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const _OrganizeLaterBanner(),
            const SizedBox(height: 18),
            TextField(
              key: const Key('web-snapshot-url'),
              controller: _url,
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: 'URL',
                hintText: 'https://example.com/article',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('web-snapshot-title'),
              controller: _title,
              decoration: const InputDecoration(labelText: 'Title (optional)'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('web-snapshot-notes'),
              controller: _notes,
              minLines: 3,
              maxLines: 6,
              decoration: const InputDecoration(
                labelText: 'Why are you saving this? (optional)',
                alignLabelWithHint: true,
              ),
            ),
            _CaptureError(viewModel: widget.viewModel),
            const SizedBox(height: 18),
            FilledButton(
              key: const Key('save-web-snapshot'),
              onPressed: widget.viewModel.saving ? null : _save,
              child: Text(
                widget.viewModel.saving ? 'Saving…' : 'Save snapshot to Inbox',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class NativeCapturePage extends StatefulWidget {
  const NativeCapturePage({
    required this.kind,
    required this.viewModel,
    required this.onCaptured,
    super.key,
  });

  final CaptureKind kind;
  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;

  @override
  State<NativeCapturePage> createState() => _NativeCapturePageState();
}

class _NativeCapturePageState extends State<NativeCapturePage> {
  Future<void> _finish(Future<bool> operation) async {
    final captured = await operation;
    if (!captured || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final isPhoto = widget.kind == CaptureKind.photo;
    return _CaptureScaffold(
      title: isPhoto ? 'Photo' : 'Document',
      child: AnimatedBuilder(
        animation: widget.viewModel,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const _OrganizeLaterBanner(),
            const SizedBox(height: 18),
            _CapabilityNotice(
              icon: isPhoto
                  ? Icons.photo_camera_outlined
                  : Icons.file_open_outlined,
              title: isPhoto
                  ? 'Preserve the original photo'
                  : 'Preserve the original file',
              body: isPhoto
                  ? 'Take a photo or choose one from the gallery. Gunther uploads the original to Inbox; OCR is not enabled yet.'
                  : 'Choose a document, PDF, ebook, or other file. Gunther streams the original to the local backend before extracting supported text.',
            ),
            const SizedBox(height: 18),
            if (isPhoto) ...[
              FilledButton.icon(
                key: const Key('take-photo'),
                onPressed: widget.viewModel.saving
                    ? null
                    : () => _finish(
                        widget.viewModel.capturePhoto(
                          PhotoCaptureSource.camera,
                        ),
                      ),
                icon: const Icon(Icons.camera_alt_outlined),
                label: Text(
                  widget.viewModel.saving ? 'Uploading…' : 'Take photo',
                ),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                key: const Key('choose-photo'),
                onPressed: widget.viewModel.saving
                    ? null
                    : () => _finish(
                        widget.viewModel.capturePhoto(
                          PhotoCaptureSource.gallery,
                        ),
                      ),
                icon: const Icon(Icons.photo_library_outlined),
                label: const Text('Choose from gallery'),
              ),
            ] else
              FilledButton.icon(
                key: const Key('choose-document'),
                onPressed: widget.viewModel.saving
                    ? null
                    : () => _finish(widget.viewModel.captureDocument()),
                icon: const Icon(Icons.attach_file),
                label: Text(
                  widget.viewModel.saving ? 'Uploading…' : 'Choose document',
                ),
              ),
            _CaptureError(viewModel: widget.viewModel),
            if (widget.viewModel.captureStatus != null) ...[
              const SizedBox(height: 12),
              Text(widget.viewModel.captureStatus!),
            ],
            const SizedBox(height: 12),
            TextButton(
              onPressed: widget.viewModel.saving
                  ? null
                  : () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (context) => ManualTextImportPage(
                          title: isPhoto
                              ? 'Paste recognized text'
                              : 'Paste document text',
                          kind: isPhoto ? 'image' : 'file',
                          viewModel: widget.viewModel,
                          onCaptured: widget.onCaptured,
                        ),
                      ),
                    ),
              child: const Text('Paste text instead'),
            ),
          ],
        ),
      ),
    );
  }
}

class RecordingWorkspacePage extends StatefulWidget {
  const RecordingWorkspacePage({
    required this.viewModel,
    required this.onCaptured,
    required this.onMinimize,
    super.key,
  });

  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;
  final VoidCallback onMinimize;

  @override
  State<RecordingWorkspacePage> createState() => _RecordingWorkspacePageState();
}

class _RecordingWorkspacePageState extends State<RecordingWorkspacePage> {
  late final TextEditingController _title;
  late final TextEditingController _transcript;

  @override
  void initState() {
    super.initState();
    _title = TextEditingController(text: widget.viewModel.recordingTitle);
    _transcript = TextEditingController(text: widget.viewModel.transcript);
    widget.viewModel.addListener(_syncTranscriptFromViewModel);
  }

  @override
  void didUpdateWidget(covariant RecordingWorkspacePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.viewModel, widget.viewModel)) {
      oldWidget.viewModel.removeListener(_syncTranscriptFromViewModel);
      widget.viewModel.addListener(_syncTranscriptFromViewModel);
      _syncTranscriptFromViewModel();
    }
  }

  void _syncTranscriptFromViewModel() {
    final updated = widget.viewModel.transcript;
    if (_transcript.text == updated) return;
    final selection = _transcript.selection;
    final wasAtEnd =
        selection.isValid && selection.extentOffset == _transcript.text.length;
    final offset = wasAtEnd
        ? updated.length
        : selection.extentOffset.clamp(0, updated.length).toInt();
    _transcript.value = TextEditingValue(
      text: updated,
      selection: TextSelection.collapsed(offset: offset),
    );
  }

  @override
  void dispose() {
    widget.viewModel.removeListener(_syncTranscriptFromViewModel);
    _title.dispose();
    _transcript.dispose();
    super.dispose();
  }

  void _minimize() {
    widget.viewModel.minimizeRecording();
    widget.onMinimize();
  }

  Future<void> _saveToInbox() async {
    final saved = await widget.viewModel.saveRecordingToInbox();
    if (!saved || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      onPopInvokedWithResult: (didPop, _) {
        if (didPop && widget.viewModel.recordingIsBusy) {
          widget.viewModel.minimizeRecording();
        }
      },
      child: _CaptureScaffold(
        key: const Key('recording-workspace'),
        title: 'Recording',
        bottomBar: AnimatedBuilder(
          animation: widget.viewModel,
          builder: (context, _) => _RecordingControls(
            viewModel: widget.viewModel,
            onStart: () => widget.viewModel.startRecording(_title.text),
            onMinimize: _minimize,
          ),
        ),
        child: AnimatedBuilder(
          animation: widget.viewModel,
          builder: (context, _) {
            final viewModel = widget.viewModel;
            final canConfigure = !viewModel.recordingIsBusy;
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const _OrganizeLaterBanner(),
                const SizedBox(height: 16),
                Row(
                  children: [
                    DecoratedBox(
                      key: const Key('recording-status'),
                      decoration: BoxDecoration(
                        color: _phaseColor(context, viewModel.recordingPhase),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 6,
                        ),
                        child: Text(
                          _phaseLabel(viewModel.recordingPhase),
                          style: Theme.of(context).textTheme.labelMedium,
                        ),
                      ),
                    ),
                    const Spacer(),
                    Text(
                      _formatRecordingTime(viewModel.recordingSeconds),
                      key: const Key('recording-clock'),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                TextField(
                  key: const Key('recording-title'),
                  controller: _title,
                  enabled:
                      canConfigure &&
                      viewModel.recordingPhase !=
                          RecordingCapturePhase.completed,
                  decoration: const InputDecoration(
                    labelText: 'Recording title',
                  ),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  key: const Key('recording-context'),
                  initialValue: viewModel.recordingContext,
                  decoration: const InputDecoration(labelText: 'Context'),
                  items: const [
                    DropdownMenuItem(value: 'memo', child: Text('Voice memo')),
                    DropdownMenuItem(value: 'lecture', child: Text('Lecture')),
                    DropdownMenuItem(value: 'meeting', child: Text('Meeting')),
                  ],
                  onChanged: canConfigure || viewModel.recordingIsCapturing
                      ? (value) {
                          if (value != null) {
                            viewModel.updateRecordingContext(value);
                          }
                        }
                      : null,
                ),
                const SizedBox(height: 16),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.storage_outlined),
                            const SizedBox(width: 9),
                            Expanded(
                              child: Text(
                                'Foreground audio capture',
                                style: Theme.of(context).textTheme.titleMedium,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Text(viewModel.recordingNotice),
                        const Divider(height: 24),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(Icons.subtitles_outlined, size: 20),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                viewModel.recordingTranscriptionStatus,
                                key: const Key(
                                  'recording-transcription-status',
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'In-app minimization is supported. OS background and lock-screen continuity are not yet claimed.',
                        ),
                      ],
                    ),
                  ),
                ),
                if (viewModel.recordingPhase ==
                    RecordingCapturePhase.uploading) ...[
                  const SizedBox(height: 12),
                  LinearProgressIndicator(
                    key: const Key('recording-upload-progress'),
                    value: viewModel.recordingUploadProgress,
                  ),
                  const SizedBox(height: 6),
                  Text(
                    '${(viewModel.recordingUploadProgress * 100).round()}% uploaded',
                    textAlign: TextAlign.center,
                  ),
                ],
                _CaptureError(viewModel: viewModel),
                const SizedBox(height: 20),
                Text(
                  'Transcript',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 4),
                const Text(
                  'Live results appear here when a verified service is available. You can edit them at any time; the full audio stays independent.',
                ),
                if (viewModel.liveTranscriptPreview.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(
                        'Listening: ${viewModel.liveTranscriptPreview}',
                        key: const Key('recording-live-preview'),
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 10),
                TextField(
                  key: const Key('recording-transcript'),
                  controller: _transcript,
                  onChanged: viewModel.updateTranscript,
                  minLines: 7,
                  maxLines: 16,
                  decoration: const InputDecoration(
                    hintText:
                        'Type or paste a transcript while audio is safely recorded…',
                    alignLabelWithHint: true,
                  ),
                ),
                if (viewModel.moments.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Text(
                    'Marked moments',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (
                        var index = 0;
                        index < viewModel.moments.length;
                        index++
                      )
                        InputChip(
                          label: Text(
                            '${_formatRecordingTime(viewModel.moments[index].seconds)} · '
                            '${viewModel.moments[index].label}',
                          ),
                          onDeleted: () => viewModel.removeMoment(index),
                        ),
                    ],
                  ),
                ],
                if (viewModel.recordingPhase ==
                        RecordingCapturePhase.completed &&
                    !viewModel.recordingFiledToInbox) ...[
                  const SizedBox(height: 18),
                  FilledButton.icon(
                    key: const Key('save-recording-inbox'),
                    onPressed: viewModel.saving ? null : _saveToInbox,
                    icon: const Icon(Icons.inbox_outlined),
                    label: Text(
                      viewModel.saving ? 'Saving…' : 'Keep recording in Inbox',
                    ),
                  ),
                ],
                if (!viewModel.recordingIsBusy) ...[
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    key: const Key('import-audio'),
                    onPressed: viewModel.importAudio,
                    icon: const Icon(Icons.audio_file_outlined),
                    label: const Text('Import existing audio'),
                  ),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

class _RecordingControls extends StatelessWidget {
  const _RecordingControls({
    required this.viewModel,
    required this.onStart,
    required this.onMinimize,
  });

  final CaptureViewModel viewModel;
  final VoidCallback onStart;
  final VoidCallback onMinimize;

  @override
  Widget build(BuildContext context) {
    switch (viewModel.recordingPhase) {
      case RecordingCapturePhase.idle:
      case RecordingCapturePhase.failed:
        return FilledButton.icon(
          key: const Key('start-recording'),
          onPressed: onStart,
          icon: const Icon(Icons.mic_none),
          label: const Text('Start foreground recording'),
        );
      case RecordingCapturePhase.starting:
        return FilledButton.icon(
          onPressed: null,
          icon: const Icon(Icons.mic_none),
          label: const Text('Starting…'),
        );
      case RecordingCapturePhase.recording:
        return Wrap(
          alignment: WrapAlignment.center,
          spacing: 8,
          runSpacing: 8,
          children: [
            OutlinedButton.icon(
              key: const Key('mark-recording-moment'),
              onPressed: viewModel.markMoment,
              icon: const Icon(Icons.bookmark_add_outlined),
              label: const Text('Mark'),
            ),
            OutlinedButton.icon(
              key: const Key('pause-recording'),
              onPressed: viewModel.pauseRecording,
              icon: const Icon(Icons.pause),
              label: const Text('Pause'),
            ),
            FilledButton.icon(
              key: const Key('stop-recording'),
              onPressed: viewModel.stopRecording,
              icon: const Icon(Icons.stop),
              label: const Text('Finish'),
            ),
            TextButton.icon(
              key: const Key('minimize-recording'),
              onPressed: onMinimize,
              icon: const Icon(Icons.minimize),
              label: const Text('Minimize in app'),
            ),
          ],
        );
      case RecordingCapturePhase.paused:
        return Wrap(
          alignment: WrapAlignment.center,
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              key: const Key('resume-recording'),
              onPressed: viewModel.resumeRecording,
              icon: const Icon(Icons.play_arrow),
              label: const Text('Resume'),
            ),
            FilledButton.icon(
              key: const Key('stop-recording'),
              onPressed: viewModel.stopRecording,
              icon: const Icon(Icons.stop),
              label: const Text('Finish'),
            ),
            TextButton.icon(
              key: const Key('minimize-recording'),
              onPressed: onMinimize,
              icon: const Icon(Icons.minimize),
              label: const Text('Minimize in app'),
            ),
          ],
        );
      case RecordingCapturePhase.stopping:
        return const FilledButton(
          onPressed: null,
          child: Text('Finalizing local audio…'),
        );
      case RecordingCapturePhase.uploading:
        return TextButton.icon(
          key: const Key('minimize-recording'),
          onPressed: onMinimize,
          icon: const Icon(Icons.minimize),
          label: const Text('Upload in compact view'),
        );
      case RecordingCapturePhase.localOnly:
        return FilledButton.icon(
          key: const Key('retry-recording-upload'),
          onPressed: viewModel.retryRecordingUpload,
          icon: const Icon(Icons.sync),
          label: const Text('Retry verified upload'),
        );
      case RecordingCapturePhase.completed:
        return OutlinedButton.icon(
          key: const Key('new-recording'),
          onPressed: viewModel.recordingFiledToInbox
              ? viewModel.beginAnotherRecording
              : null,
          icon: const Icon(Icons.add),
          label: Text(
            viewModel.recordingFiledToInbox
                ? 'Prepare another recording'
                : 'Keep in Inbox before starting another',
          ),
        );
    }
  }
}

class ManualTextImportPage extends StatefulWidget {
  const ManualTextImportPage({
    required this.title,
    required this.kind,
    required this.viewModel,
    required this.onCaptured,
    super.key,
  });

  final String title;
  final String kind;
  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;

  @override
  State<ManualTextImportPage> createState() => _ManualTextImportPageState();
}

class _ManualTextImportPageState extends State<ManualTextImportPage> {
  final _sourceTitle = TextEditingController();
  final _content = TextEditingController();

  @override
  void dispose() {
    _sourceTitle.dispose();
    _content.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_sourceTitle.text.trim().isEmpty || _content.text.trim().length < 3) {
      return;
    }
    final success = await widget.viewModel.saveSource(
      SourceDraft(
        title: _sourceTitle.text.trim(),
        kind: widget.kind,
        content: _content.text.trim(),
      ),
    );
    if (!success || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return _CaptureScaffold(
      title: widget.title,
      child: AnimatedBuilder(
        animation: widget.viewModel,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const _OrganizeLaterBanner(),
            const SizedBox(height: 18),
            TextField(
              controller: _sourceTitle,
              decoration: const InputDecoration(labelText: 'Source title'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _content,
              minLines: 10,
              maxLines: 20,
              decoration: const InputDecoration(
                labelText: 'Text content',
                alignLabelWithHint: true,
              ),
            ),
            _CaptureError(viewModel: widget.viewModel),
            const SizedBox(height: 18),
            FilledButton(
              onPressed: widget.viewModel.saving ? null : _save,
              child: Text(
                widget.viewModel.saving ? 'Saving…' : 'Save to Inbox',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class WebCapturePage extends StatefulWidget {
  const WebCapturePage({
    required this.viewModel,
    required this.onCaptured,
    super.key,
  });

  final CaptureViewModel viewModel;
  final Future<void> Function() onCaptured;

  @override
  State<WebCapturePage> createState() => _WebCapturePageState();
}

class _WebCapturePageState extends State<WebCapturePage> {
  final _query = TextEditingController();

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    if (_query.text.trim().isEmpty) return;
    await widget.viewModel.searchWeb(_query.text.trim());
  }

  Future<void> _save() async {
    final success = await widget.viewModel.saveWebResult();
    if (!success || !mounted) return;
    await widget.onCaptured();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return _CaptureScaffold(
      title: 'Web research',
      child: AnimatedBuilder(
        animation: widget.viewModel,
        builder: (context, _) {
          final result = widget.viewModel.webResult;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _query,
                textInputAction: TextInputAction.search,
                onSubmitted: (_) => _search(),
                decoration: const InputDecoration(
                  labelText: 'What do you want to find?',
                  prefixIcon: Icon(Icons.search),
                ),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: widget.viewModel.searching ? null : _search,
                child: Text(
                  widget.viewModel.searching ? 'Searching…' : 'Search the web',
                ),
              ),
              _CaptureError(viewModel: widget.viewModel),
              if (result != null) ...[
                const SizedBox(height: 20),
                if (result.mode != 'openai')
                  _CapabilityNotice(
                    icon: Icons.info_outline,
                    title: 'Online search is not configured',
                    body:
                        result.message ??
                        'Connect a supported web-search provider in the backend.',
                  )
                else ...[
                  Text('Answer', style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 8),
                  Text(result.answer),
                  const SizedBox(height: 18),
                  Text(
                    'Sources',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  ...result.sources.map(
                    (source) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.link),
                      title: Text(source.title),
                      subtitle: Text(
                        source.url,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  const _OrganizeLaterBanner(),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: widget.viewModel.saving ? null : _save,
                    icon: const Icon(Icons.inbox_outlined),
                    label: Text(
                      widget.viewModel.saving
                          ? 'Saving…'
                          : 'Save result to Inbox',
                    ),
                  ),
                ],
              ],
            ],
          );
        },
      ),
    );
  }
}

class _CaptureScaffold extends StatelessWidget {
  const _CaptureScaffold({
    required this.title,
    required this.child,
    this.bottomBar,
    super.key,
  });

  final String title;
  final Widget child;
  final Widget? bottomBar;

  @override
  Widget build(BuildContext context) {
    final bar = bottomBar;
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      bottomNavigationBar: bar == null
          ? null
          : Material(
              color: Theme.of(context).colorScheme.surface,
              elevation: 8,
              child: SafeArea(
                top: false,
                minimum: const EdgeInsets.fromLTRB(16, 10, 16, 10),
                child: bar,
              ),
            ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 36),
          children: [child],
        ),
      ),
    );
  }
}

class _OrganizeLaterBanner extends StatelessWidget {
  const _OrganizeLaterBanner();

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Theme.of(
          context,
        ).colorScheme.primaryContainer.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(12),
      ),
      child: const Padding(
        padding: EdgeInsets.all(12),
        child: Row(
          children: [
            Icon(Icons.inbox_outlined, size: 20),
            SizedBox(width: 9),
            Expanded(
              child: Text(
                'This will be preserved in Inbox. Organize it later.',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CaptureError extends StatelessWidget {
  const _CaptureError({required this.viewModel});

  final CaptureViewModel viewModel;

  @override
  Widget build(BuildContext context) {
    if (viewModel.error == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Text(
        viewModel.error!,
        style: TextStyle(color: Theme.of(context).colorScheme.error),
      ),
    );
  }
}

class _CapabilityNotice extends StatelessWidget {
  const _CapabilityNotice({
    required this.icon,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Icon(icon, size: 40),
            const SizedBox(height: 12),
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(body, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}

String _phaseLabel(RecordingCapturePhase phase) => switch (phase) {
  RecordingCapturePhase.idle => 'READY',
  RecordingCapturePhase.starting => 'STARTING',
  RecordingCapturePhase.recording => 'RECORDING',
  RecordingCapturePhase.paused => 'PAUSED',
  RecordingCapturePhase.stopping => 'FINALIZING',
  RecordingCapturePhase.uploading => 'UPLOADING',
  RecordingCapturePhase.completed => 'SAVED',
  RecordingCapturePhase.localOnly => 'ON DEVICE',
  RecordingCapturePhase.failed => 'NEEDS ATTENTION',
};

Color _phaseColor(BuildContext context, RecordingCapturePhase phase) {
  final colors = Theme.of(context).colorScheme;
  return switch (phase) {
    RecordingCapturePhase.recording => colors.errorContainer,
    RecordingCapturePhase.paused => colors.tertiaryContainer,
    RecordingCapturePhase.localOnly ||
    RecordingCapturePhase.failed => colors.errorContainer,
    RecordingCapturePhase.completed => colors.primaryContainer,
    _ => colors.surfaceContainerHighest,
  };
}

String _formatRecordingTime(int seconds) {
  final hours = seconds ~/ 3600;
  final minutes = (seconds % 3600) ~/ 60;
  final remainder = seconds % 60;
  return '${hours.toString().padLeft(2, '0')}:'
      '${minutes.toString().padLeft(2, '0')}:'
      '${remainder.toString().padLeft(2, '0')}';
}

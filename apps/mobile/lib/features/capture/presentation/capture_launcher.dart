import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/capture.dart';

class CaptureLauncher extends StatelessWidget {
  const CaptureLauncher({required this.onSelected, super.key});

  final ValueChanged<CaptureKind> onSelected;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Capture',
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                          const SizedBox(height: 4),
                          const Text(
                            'Choose the source. Every option starts equally.',
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      tooltip: 'Close',
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                DecoratedBox(
                  decoration: BoxDecoration(
                    color: Theme.of(
                      context,
                    ).colorScheme.primaryContainer.withValues(alpha: 0.45),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    child: Row(
                      children: [
                        Icon(Icons.inbox_outlined, size: 20),
                        SizedBox(width: 9),
                        Expanded(
                          child: Text('Destination: Inbox · Organize later'),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                GridView.count(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  crossAxisCount: 2,
                  mainAxisSpacing: 10,
                  crossAxisSpacing: 10,
                  childAspectRatio: 1.55,
                  children: CaptureKind.values
                      .map(
                        (kind) => _CaptureCard(
                          kind: kind,
                          onTap: () => onSelected(kind),
                        ),
                      )
                      .toList(growable: false),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CaptureCard extends StatelessWidget {
  const _CaptureCard({required this.kind, required this.onTap});

  final CaptureKind kind;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        key: Key('capture-${kind.name}'),
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(13),
          child: Row(
            children: [
              Icon(kind.icon, color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      kind.label,
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      kind.helper,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

extension CaptureKindPresentation on CaptureKind {
  String get label => switch (this) {
    CaptureKind.quickNote => 'Quick note',
    CaptureKind.document => 'Document',
    CaptureKind.photo => 'Photo',
    CaptureKind.link => 'Link',
    CaptureKind.recording => 'Recording',
    CaptureKind.web => 'Web',
  };

  String get helper => switch (this) {
    CaptureKind.quickNote => 'Write it down',
    CaptureKind.document => 'Files and PDFs',
    CaptureKind.photo => 'Camera or scan',
    CaptureKind.link => 'Snapshot a webpage',
    CaptureKind.recording => 'Audio workspace',
    CaptureKind.web => 'Research online',
  };

  IconData get icon => switch (this) {
    CaptureKind.quickNote => Icons.edit_note,
    CaptureKind.document => Icons.description_outlined,
    CaptureKind.photo => Icons.photo_camera_outlined,
    CaptureKind.link => Icons.link,
    CaptureKind.recording => Icons.graphic_eq,
    CaptureKind.web => Icons.travel_explore,
  };
}

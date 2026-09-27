import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/features/inbox/presentation/inbox_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';

class InboxView extends StatelessWidget {
  const InboxView({
    required this.viewModel,
    required this.onChanged,
    super.key,
  });

  final InboxViewModel viewModel;
  final Future<void> Function() onChanged;

  Future<void> _chooseLibrary(BuildContext context, InboxItem item) async {
    if (viewModel.libraries.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Create a library before filing this item.'),
        ),
      );
      return;
    }
    final selected = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (context) =>
          _LibraryPicker(item: item, libraries: viewModel.libraries),
    );
    if (selected == null) return;
    final success = await viewModel.file(item, selected);
    if (success) await onChanged();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.items.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.items.isEmpty) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }

        return Column(
          children: [
            SizedBox(
              height: 54,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                children: [
                  _FilterChip(
                    label: 'All',
                    selected: viewModel.filter == null,
                    onTap: () => viewModel.setFilter(null),
                  ),
                  _FilterChip(
                    label: 'Unfiled',
                    selected: viewModel.filter == 'unfiled',
                    onTap: () => viewModel.setFilter('unfiled'),
                  ),
                  _FilterChip(
                    label: 'Review',
                    selected: viewModel.filter == 'needs_review',
                    onTap: () => viewModel.setFilter('needs_review'),
                  ),
                  _FilterChip(
                    label: 'Held',
                    selected: viewModel.filter == 'held',
                    onTap: () => viewModel.setFilter('held'),
                  ),
                ],
              ),
            ),
            if (viewModel.error != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.errorContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Text(viewModel.error!),
                  ),
                ),
              ),
            Expanded(
              child: RefreshIndicator(
                onRefresh: viewModel.load,
                child: viewModel.items.isEmpty
                    ? const _EmptyInbox()
                    : ListView.builder(
                        key: const PageStorageKey('inbox-list'),
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 112),
                        itemCount: viewModel.items.length,
                        itemBuilder: (context, index) {
                          final item = viewModel.items[index];
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: _InboxCard(
                              item: item,
                              filing: viewModel.filingId == item.id,
                              onFile: item.canBeFiled
                                  ? () => _chooseLibrary(context, item)
                                  : null,
                            ),
                          );
                        },
                      ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _FilterChip extends StatelessWidget {
  const _FilterChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: ChoiceChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => onTap(),
      ),
    );
  }
}

class _InboxCard extends StatelessWidget {
  const _InboxCard({
    required this.item,
    required this.filing,
    required this.onFile,
  });

  final InboxItem item;
  final bool filing;
  final VoidCallback? onFile;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                _TypeIcon(item: item),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        item.title,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        _stateLabel(item.state),
                        style: Theme.of(context).textTheme.labelMedium
                            ?.copyWith(
                              color: Theme.of(context).colorScheme.primary,
                            ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            if (item.preview.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(item.preview, maxLines: 3, overflow: TextOverflow.ellipsis),
            ],
            if (item.knowledgeBases.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(
                item.knowledgeBases.map((library) => library.title).join(' · '),
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ],
            if (item.assertionCount > 0) ...[
              const SizedBox(height: 8),
              Text('${item.assertionCount} claims need review'),
            ],
            if (onFile != null) ...[
              const SizedBox(height: 14),
              Align(
                alignment: Alignment.centerRight,
                child: FilledButton.tonalIcon(
                  onPressed: filing ? null : onFile,
                  icon: const Icon(Icons.drive_file_move_outline),
                  label: Text(filing ? 'Filing…' : 'Add to library'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _TypeIcon extends StatelessWidget {
  const _TypeIcon({required this.item});

  final InboxItem item;

  @override
  Widget build(BuildContext context) {
    final icon = switch (item.sourceKind) {
      'recording' => Icons.graphic_eq,
      'link' => Icons.link,
      'paper' || 'file' => Icons.description_outlined,
      _ =>
        item.itemType == 'knowledge_suggestion'
            ? Icons.auto_awesome_outlined
            : Icons.notes_outlined,
    };
    return CircleAvatar(
      backgroundColor: Theme.of(context).colorScheme.primaryContainer,
      child: Icon(icon),
    );
  }
}

class _LibraryPicker extends StatelessWidget {
  const _LibraryPicker({required this.item, required this.libraries});

  final InboxItem item;
  final List<KnowledgeBaseSummary> libraries;

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.55,
      minChildSize: 0.35,
      maxChildSize: 0.9,
      builder: (context, scrollController) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 18, 16, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Add to library',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 4),
            Text(item.title, maxLines: 1, overflow: TextOverflow.ellipsis),
            const SizedBox(height: 14),
            Expanded(
              child: ListView.builder(
                controller: scrollController,
                itemCount: libraries.length,
                itemBuilder: (context, index) {
                  final library = libraries[index];
                  return ListTile(
                    key: Key('file-to-${library.id}'),
                    leading: const Icon(Icons.menu_book_outlined),
                    title: Text(library.title),
                    subtitle: Text('${library.sourceCount} sources'),
                    onTap: () => Navigator.of(context).pop(library.id),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EmptyInbox extends StatelessWidget {
  const _EmptyInbox();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 60, 24, 112),
      children: const [
        Icon(Icons.inbox_outlined, size: 44),
        SizedBox(height: 12),
        Text('Nothing waiting here', textAlign: TextAlign.center),
        SizedBox(height: 4),
        Text(
          'New captures appear here when you choose Organize later.',
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

String _stateLabel(String state) => switch (state) {
  'unfiled' => 'Ready to organize',
  'needs_review' => 'Needs review',
  'held' => 'Held for later',
  _ => state,
};

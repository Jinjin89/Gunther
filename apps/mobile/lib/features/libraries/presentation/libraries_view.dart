import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/features/libraries/presentation/libraries_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';

class LibrariesView extends StatelessWidget {
  const LibrariesView({
    required this.viewModel,
    required this.onChanged,
    super.key,
  });

  final LibrariesViewModel viewModel;
  final Future<void> Function() onChanged;

  Future<void> _openCreate(BuildContext context) async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (context) => _CreateLibrarySheet(viewModel: viewModel),
    );
    if (created == true) await onChanged();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.libraries.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.libraries.isEmpty) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }

        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView(
            key: const PageStorageKey('libraries-list'),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 112),
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Stable homes for the subjects you keep building.',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  const SizedBox(width: 12),
                  OutlinedButton.icon(
                    key: const Key('new-library-button'),
                    onPressed: () => _openCreate(context),
                    icon: const Icon(Icons.add),
                    label: const Text('New'),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              if (viewModel.libraries.isEmpty)
                const _EmptyLibraries()
              else
                ...viewModel.libraries.map(
                  (library) => Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: _LibraryCard(library: library),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }
}

class _LibraryCard extends StatelessWidget {
  const _LibraryCard({required this.library});

  final KnowledgeBaseSummary library;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => Navigator.of(context).push<void>(
          MaterialPageRoute(
            builder: (context) => _LibraryDetailPage(library: library),
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      library.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  const Icon(Icons.chevron_right),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                library.subtitle,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _LibraryMetric('${library.sourceCount} sources'),
                  _LibraryMetric('${library.sessionCount} conversations'),
                  if (library.pendingProposalCount > 0)
                    _LibraryMetric('${library.pendingProposalCount} to review'),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LibraryMetric extends StatelessWidget {
  const _LibraryMetric(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        child: Text(label, style: Theme.of(context).textTheme.labelMedium),
      ),
    );
  }
}

class _LibraryDetailPage extends StatelessWidget {
  const _LibraryDetailPage({required this.library});

  final KnowledgeBaseSummary library;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(library.title)),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            library.eyebrow.toUpperCase(),
            style: Theme.of(context).textTheme.labelMedium,
          ),
          const SizedBox(height: 8),
          Text(
            library.subtitle,
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 22),
          Text(
            'Guiding question',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 6),
          Text(library.question),
          const SizedBox(height: 22),
          Text('About', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(library.description),
          const SizedBox(height: 28),
          const Card(
            child: Padding(
              padding: EdgeInsets.all(18),
              child: Text(
                'Deep reading, Ask, and Outputs remain desktop-first. Mobile '
                'keeps this space focused on capture and continuation.',
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _CreateLibrarySheet extends StatefulWidget {
  const _CreateLibrarySheet({required this.viewModel});

  final LibrariesViewModel viewModel;

  @override
  State<_CreateLibrarySheet> createState() => _CreateLibrarySheetState();
}

class _CreateLibrarySheetState extends State<_CreateLibrarySheet> {
  final _formKey = GlobalKey<FormState>();
  final _title = TextEditingController();
  final _question = TextEditingController();
  final _description = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    _question.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final success = await widget.viewModel.create(
      KnowledgeBaseDraft(
        title: _title.text.trim(),
        subtitle: _question.text.trim(),
        question: _question.text.trim(),
        description: _description.text.trim(),
      ),
    );
    if (success && mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.viewModel,
      builder: (context, _) => Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          20,
          20,
          MediaQuery.viewInsetsOf(context).bottom + 20,
        ),
        child: Form(
          key: _formKey,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'New library',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 8),
                const Text(
                  'A library is a lasting subject, not a temporary capture session.',
                ),
                const SizedBox(height: 18),
                TextFormField(
                  controller: _title,
                  decoration: const InputDecoration(labelText: 'Library name'),
                  validator: _required,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _question,
                  decoration: const InputDecoration(
                    labelText: 'Guiding question',
                  ),
                  validator: _threeCharacters,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _description,
                  minLines: 3,
                  maxLines: 5,
                  decoration: const InputDecoration(
                    labelText: 'What belongs here?',
                  ),
                  validator: _threeCharacters,
                ),
                if (widget.viewModel.error != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    widget.viewModel.error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ],
                const SizedBox(height: 18),
                FilledButton(
                  onPressed: widget.viewModel.creating ? null : _submit,
                  child: Text(
                    widget.viewModel.creating ? 'Creating…' : 'Create library',
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

String? _required(String? value) =>
    value == null || value.trim().isEmpty ? 'This field is required' : null;

String? _threeCharacters(String? value) =>
    value == null || value.trim().length < 3
    ? 'Add at least 3 characters'
    : null;

class _EmptyLibraries extends StatelessWidget {
  const _EmptyLibraries();

  @override
  Widget build(BuildContext context) {
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(28),
        child: Column(
          children: [
            Icon(Icons.auto_stories_outlined, size: 36),
            SizedBox(height: 10),
            Text('Create a library when a lasting subject emerges.'),
          ],
        ),
      ),
    );
  }
}

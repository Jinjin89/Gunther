import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/features/sources/presentation/sources_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';

class SourcesView extends StatelessWidget {
  const SourcesView({required this.viewModel, super.key});

  final SourcesViewModel viewModel;

  Future<void> _openComposer(BuildContext context) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (context) => SourceComposer(viewModel: viewModel),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.sources.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.sources.isEmpty) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }

        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 28),
            children: [
              FilledButton.icon(
                onPressed: () => _openComposer(context),
                icon: const Icon(Icons.add),
                label: const Text('Add knowledge source'),
              ),
              const SizedBox(height: 18),
              Text(
                'Preserved inputs',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 10),
              if (viewModel.sources.isEmpty)
                const _EmptySources()
              else
                ...viewModel.sources.map((source) => _SourceCard(source)),
            ],
          ),
        );
      },
    );
  }
}

class SourceComposer extends StatefulWidget {
  const SourceComposer({required this.viewModel, super.key});

  final SourcesViewModel viewModel;

  @override
  State<SourceComposer> createState() => _SourceComposerState();
}

class _SourceComposerState extends State<SourceComposer> {
  final _formKey = GlobalKey<FormState>();
  final _titleController = TextEditingController();
  final _contentController = TextEditingController();
  String _kind = 'note';

  @override
  void dispose() {
    _titleController.dispose();
    _contentController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final success = await widget.viewModel.importSource(
      SourceDraft(
        title: _titleController.text.trim(),
        kind: _kind,
        content: _contentController.text.trim(),
      ),
    );
    if (success && mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.viewModel,
      builder: (context, _) {
        return Padding(
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
                    'Add knowledge',
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 18),
                  TextFormField(
                    controller: _titleController,
                    decoration: const InputDecoration(
                      labelText: 'Source title',
                    ),
                    validator: (value) => value == null || value.trim().isEmpty
                        ? 'Enter a source title'
                        : null,
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: _kind,
                    decoration: const InputDecoration(labelText: 'Source type'),
                    items: const [
                      DropdownMenuItem(value: 'note', child: Text('Note')),
                      DropdownMenuItem(value: 'paper', child: Text('Paper')),
                      DropdownMenuItem(value: 'table', child: Text('Table')),
                      DropdownMenuItem(value: 'course', child: Text('Course')),
                      DropdownMenuItem(
                        value: 'recording',
                        child: Text('Recording transcript'),
                      ),
                    ],
                    onChanged: (value) =>
                        setState(() => _kind = value ?? 'note'),
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _contentController,
                    minLines: 6,
                    maxLines: 12,
                    decoration: const InputDecoration(
                      labelText: 'Content',
                      hintText: 'CD3D -> marker_of -> T cell',
                      alignLabelWithHint: true,
                    ),
                    validator: (value) =>
                        value == null || value.trim().length < 3
                        ? 'Add some source content'
                        : null,
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
                    onPressed: widget.viewModel.importing ? null : _submit,
                    child: Text(
                      widget.viewModel.importing
                          ? 'Extracting knowledge…'
                          : 'Import and extract',
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _SourceCard extends StatelessWidget {
  const _SourceCard(this.source);

  final SourceSummary source;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Card(
        child: ListTile(
          leading: const CircleAvatar(child: Icon(Icons.description_outlined)),
          title: Text(source.title),
          subtitle: Text(
            '${source.kind} · ${source.assertionCount} assertions · ${source.entityCount} entities',
          ),
        ),
      ),
    );
  }
}

class _EmptySources extends StatelessWidget {
  const _EmptySources();

  @override
  Widget build(BuildContext context) {
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(28),
        child: Column(
          children: [
            Icon(Icons.library_add_outlined, size: 34),
            SizedBox(height: 10),
            Text('Your source library is ready to grow.'),
          ],
        ),
      ),
    );
  }
}

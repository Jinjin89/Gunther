import 'package:flutter/material.dart';
import 'package:gunther_mobile/features/home/presentation/home_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';

class HomeView extends StatelessWidget {
  const HomeView({
    required this.viewModel,
    required this.onOpenLibraries,
    required this.onOpenInbox,
    super.key,
  });

  final HomeViewModel viewModel;
  final VoidCallback onOpenLibraries;
  final VoidCallback onOpenInbox;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.overview == null) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.overview == null) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }

        final overview = viewModel.overview;
        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView(
            key: const PageStorageKey('home-list'),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 112),
            children: [
              Text(
                'Everything you capture, ready when you are.',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Capture first. Organize when the right library is clear.',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 24),
              Row(
                children: [
                  Expanded(
                    child: _SummaryCard(
                      label: 'Libraries',
                      value: viewModel.knowledgeBases.length,
                      icon: Icons.collections_bookmark_outlined,
                      onTap: onOpenLibraries,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _SummaryCard(
                      label: 'Inbox',
                      value: viewModel.inboxItems.length,
                      icon: Icons.inbox_outlined,
                      onTap: onOpenInbox,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 28),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Expanded(
                    child: Text(
                      'Your libraries',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  TextButton(
                    onPressed: onOpenLibraries,
                    child: const Text('See all'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              if (viewModel.knowledgeBases.isEmpty)
                const _HomeEmptyCard(
                  icon: Icons.auto_stories_outlined,
                  title: 'No libraries yet',
                  body:
                      'Captured items can wait safely in Inbox until you '
                      'create one.',
                )
              else
                ...viewModel.knowledgeBases
                    .take(3)
                    .map(
                      (library) => Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Card(
                          child: ListTile(
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 8,
                            ),
                            leading: CircleAvatar(
                              backgroundColor: Theme.of(
                                context,
                              ).colorScheme.primaryContainer,
                              child: const Icon(Icons.menu_book_outlined),
                            ),
                            title: Text(library.title),
                            subtitle: Text(
                              '${library.sourceCount} sources · ${library.status}',
                            ),
                            trailing: const Icon(Icons.chevron_right),
                            onTap: onOpenLibraries,
                          ),
                        ),
                      ),
                    ),
              const SizedBox(height: 24),
              Text(
                'Knowledge health',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 10),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(18),
                  child: Row(
                    children: [
                      Icon(
                        Icons.verified_outlined,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '${overview?.counts.assertions ?? 0} traceable claims',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 3),
                            Text(
                              '${overview?.counts.provisional ?? 0} still need review',
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({
    required this.label,
    required this.value,
    required this.icon,
    required this.onTap,
  });

  final String label;
  final int value;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 16),
              Text('$value', style: Theme.of(context).textTheme.headlineMedium),
              Text(label, style: Theme.of(context).textTheme.labelLarge),
            ],
          ),
        ),
      ),
    );
  }
}

class _HomeEmptyCard extends StatelessWidget {
  const _HomeEmptyCard({
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
        child: Row(
          children: [
            Icon(icon, size: 30),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 4),
                  Text(body),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

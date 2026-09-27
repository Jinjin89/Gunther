import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/features/overview/presentation/overview_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';
import 'package:gunther_mobile/shared/widgets/assertion_statement.dart';

class OverviewView extends StatelessWidget {
  const OverviewView({required this.viewModel, super.key});

  final OverviewViewModel viewModel;

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
        if (overview == null) return const SizedBox.shrink();
        final counts = overview.counts;
        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 28),
            children: [
              Card(
                color: Theme.of(context).colorScheme.primary,
                child: Padding(
                  padding: const EdgeInsets.all(22),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'YOUR KNOWLEDGE, CONNECTED',
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: Theme.of(context).colorScheme.primaryContainer,
                          letterSpacing: 1.2,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text(
                        'Grow ideas into a map you can trust.',
                        style: Theme.of(context).textTheme.headlineSmall
                            ?.copyWith(
                              color: Theme.of(context).colorScheme.onPrimary,
                              fontWeight: FontWeight.w600,
                            ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              GridView.count(
                crossAxisCount: 2,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: 10,
                crossAxisSpacing: 10,
                childAspectRatio: 1.55,
                children: [
                  _StatCard(
                    label: 'Sources',
                    value: counts.sources,
                    icon: Icons.book_outlined,
                  ),
                  _StatCard(
                    label: 'Entities',
                    value: counts.entities,
                    icon: Icons.bubble_chart_outlined,
                  ),
                  _StatCard(
                    label: 'Assertions',
                    value: counts.assertions,
                    icon: Icons.hub_outlined,
                  ),
                  _StatCard(
                    label: 'To review',
                    value: counts.provisional,
                    icon: Icons.inbox_outlined,
                  ),
                ],
              ),
              const SizedBox(height: 22),
              Text(
                'Recent assertions',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 10),
              ...overview.recentAssertions.map(
                (assertion) => _AssertionCard(assertion),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _StatCard extends StatelessWidget {
  const _StatCard({
    required this.label,
    required this.value,
    required this.icon,
  });

  final String label;
  final int value;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          children: [
            Icon(icon, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 11),
            Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '$value',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                Text(label, style: Theme.of(context).textTheme.labelMedium),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _AssertionCard extends StatelessWidget {
  const _AssertionCard(this.assertion);

  final Assertion assertion;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AssertionStatement(assertion: assertion),
              const SizedBox(height: 9),
              Text(
                '${assertion.status} · ${assertion.sourceTitle}',
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

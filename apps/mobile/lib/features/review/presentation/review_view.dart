import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/features/review/presentation/review_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';
import 'package:gunther_mobile/shared/widgets/assertion_statement.dart';

class ReviewView extends StatelessWidget {
  const ReviewView({required this.viewModel, super.key});

  final ReviewViewModel viewModel;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.assertions.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.assertions.isEmpty) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }
        if (viewModel.assertions.isEmpty) return const _EmptyReview();

        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView.builder(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 28),
            itemCount: viewModel.assertions.length,
            itemBuilder: (context, index) {
              final assertion = viewModel.assertions[index];
              return _ReviewCard(assertion: assertion, viewModel: viewModel);
            },
          ),
        );
      },
    );
  }
}

class _ReviewCard extends StatelessWidget {
  const _ReviewCard({required this.assertion, required this.viewModel});

  final Assertion assertion;
  final ReviewViewModel viewModel;

  @override
  Widget build(BuildContext context) {
    final working = viewModel.updatingId == assertion.id;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AssertionStatement(assertion: assertion),
              const SizedBox(height: 14),
              DecoratedBox(
                decoration: BoxDecoration(
                  color: Theme.of(
                    context,
                  ).colorScheme.primaryContainer.withValues(alpha: 0.35),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        assertion.evidence.isEmpty
                            ? 'No evidence excerpt'
                            : assertion.evidence.first.quote,
                      ),
                      const SizedBox(height: 6),
                      Text(
                        '${assertion.sourceTitle} · ${assertion.evidence.isEmpty ? '' : assertion.evidence.first.locator}',
                        style: Theme.of(context).textTheme.labelSmall,
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: working
                          ? null
                          : () => viewModel.updateStatus(
                              assertion.id,
                              'disputed',
                            ),
                      icon: const Icon(Icons.report_outlined),
                      label: const Text('Dispute'),
                    ),
                  ),
                  const SizedBox(width: 9),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: working
                          ? null
                          : () => viewModel.updateStatus(
                              assertion.id,
                              'verified',
                            ),
                      icon: const Icon(Icons.check),
                      label: const Text('Verify'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _EmptyReview extends StatelessWidget {
  const _EmptyReview();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.task_alt, size: 40),
          SizedBox(height: 10),
          Text('Inbox clear'),
          SizedBox(height: 4),
          Text('No provisional assertions need review.'),
        ],
      ),
    );
  }
}

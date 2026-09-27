import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/assertion.dart';

class AssertionStatement extends StatelessWidget {
  const AssertionStatement({required this.assertion, super.key});

  final Assertion assertion;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 7,
      runSpacing: 7,
      children: [
        _EntityChip(label: assertion.subject.label),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.arrow_forward, size: 14),
            const SizedBox(width: 4),
            Text(
              assertion.predicate.replaceAll('_', ' '),
              style: theme.textTheme.labelSmall,
            ),
          ],
        ),
        _EntityChip(label: assertion.object.label),
      ],
    );
  }
}

class _EntityChip extends StatelessWidget {
  const _EntityChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Theme.of(
          context,
        ).colorScheme.primaryContainer.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
        child: Text(label, style: Theme.of(context).textTheme.labelMedium),
      ),
    );
  }
}

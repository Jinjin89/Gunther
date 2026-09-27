import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:gunther_mobile/data/models/knowledge_graph.dart';
import 'package:gunther_mobile/features/map/presentation/map_view_model.dart';
import 'package:gunther_mobile/shared/widgets/app_error_view.dart';

class MapView extends StatelessWidget {
  const MapView({required this.viewModel, super.key});

  final MapViewModel viewModel;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: viewModel,
      builder: (context, _) {
        if (viewModel.loading && viewModel.graph == null) {
          return const Center(child: CircularProgressIndicator());
        }
        if (viewModel.error != null && viewModel.graph == null) {
          return AppErrorView(
            message: viewModel.error!,
            onRetry: viewModel.load,
          );
        }

        final graph = viewModel.graph;
        if (graph == null || graph.nodes.isEmpty) return const _EmptyMap();
        return RefreshIndicator(
          onRefresh: viewModel.load,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 28),
            children: [
              Card(
                clipBehavior: Clip.antiAlias,
                child: SizedBox(
                  height: 360,
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      return CustomPaint(
                        size: Size(constraints.maxWidth, 360),
                        painter: KnowledgeGraphPainter(
                          graph: graph,
                          colorScheme: Theme.of(context).colorScheme,
                        ),
                      );
                    },
                  ),
                ),
              ),
              const SizedBox(height: 20),
              Text(
                'Associations',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 10),
              ...graph.edges.map((edge) => _EdgeCard(edge: edge, graph: graph)),
            ],
          ),
        );
      },
    );
  }
}

class KnowledgeGraphPainter extends CustomPainter {
  KnowledgeGraphPainter({required this.graph, required this.colorScheme});

  final KnowledgeGraph graph;
  final ColorScheme colorScheme;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final orbit = math.min(size.width, size.height) / 2 - 52;
    final positions = <String, Offset>{};

    for (var index = 0; index < graph.nodes.length; index++) {
      final angle = index / graph.nodes.length * math.pi * 2 - math.pi / 2;
      positions[graph.nodes[index].id] =
          center + Offset(math.cos(angle), math.sin(angle)) * orbit;
    }

    final edgePaint = Paint()
      ..color = colorScheme.outlineVariant
      ..strokeWidth = 1.4;
    for (final edge in graph.edges) {
      final source = positions[edge.source];
      final target = positions[edge.target];
      if (source != null && target != null)
        canvas.drawLine(source, target, edgePaint);
    }

    for (final node in graph.nodes) {
      final position = positions[node.id]!;
      final fill = switch (node.type) {
        'Gene' => const Color(0xFFE3ECF7),
        'CellType' => const Color(0xFFF8EAD8),
        'LearningUnit' => const Color(0xFFEEE7F5),
        _ => const Color(0xFFDFEEE4),
      };
      canvas.drawCircle(position, 31, Paint()..color = fill);
      canvas.drawCircle(
        position,
        31,
        Paint()
          ..color = colorScheme.primary.withValues(alpha: 0.55)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.3,
      );
      final label = node.label.length > 12
          ? '${node.label.substring(0, 11)}…'
          : node.label;
      final textPainter = TextPainter(
        text: TextSpan(
          text: label,
          style: TextStyle(
            color: colorScheme.onSurface,
            fontSize: 10,
            fontWeight: FontWeight.w600,
          ),
        ),
        textDirection: TextDirection.ltr,
        textAlign: TextAlign.center,
      )..layout(maxWidth: 58);
      textPainter.paint(
        canvas,
        position - Offset(textPainter.width / 2, textPainter.height / 2),
      );
    }
  }

  @override
  bool shouldRepaint(covariant KnowledgeGraphPainter oldDelegate) {
    return oldDelegate.graph != graph || oldDelegate.colorScheme != colorScheme;
  }
}

class _EdgeCard extends StatelessWidget {
  const _EdgeCard({required this.edge, required this.graph});

  final KnowledgeEdge edge;
  final KnowledgeGraph graph;

  @override
  Widget build(BuildContext context) {
    final source = graph.nodes.firstWhere((node) => node.id == edge.source);
    final target = graph.nodes.firstWhere((node) => node.id == edge.target);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Card(
        child: ListTile(
          title: Text('${source.label} → ${target.label}'),
          subtitle: Text(edge.label.replaceAll('_', ' ')),
          trailing: Icon(
            edge.status == 'verified'
                ? Icons.verified_outlined
                : Icons.pending_outlined,
            color: edge.status == 'verified'
                ? Theme.of(context).colorScheme.primary
                : null,
          ),
        ),
      ),
    );
  }
}

class _EmptyMap extends StatelessWidget {
  const _EmptyMap();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.hub_outlined, size: 40),
          SizedBox(height: 10),
          Text('Your knowledge map is ready to grow.'),
        ],
      ),
    );
  }
}

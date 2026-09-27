class KnowledgeNode {
  const KnowledgeNode({
    required this.id,
    required this.label,
    required this.type,
    required this.assertionCount,
  });

  factory KnowledgeNode.fromJson(Map<String, Object?> json) {
    return KnowledgeNode(
      id: json['id']! as String,
      label: json['label']! as String,
      type: json['type']! as String,
      assertionCount: json['assertionCount']! as int,
    );
  }

  final String id;
  final String label;
  final String type;
  final int assertionCount;
}

class KnowledgeEdge {
  const KnowledgeEdge({
    required this.id,
    required this.source,
    required this.target,
    required this.label,
    required this.status,
  });

  factory KnowledgeEdge.fromJson(Map<String, Object?> json) {
    return KnowledgeEdge(
      id: json['id']! as String,
      source: json['source']! as String,
      target: json['target']! as String,
      label: json['label']! as String,
      status: json['status']! as String,
    );
  }

  final String id;
  final String source;
  final String target;
  final String label;
  final String status;
}

class KnowledgeGraph {
  const KnowledgeGraph({required this.nodes, required this.edges});

  factory KnowledgeGraph.fromJson(Map<String, Object?> json) {
    return KnowledgeGraph(
      nodes: (json['nodes']! as List<Object?>)
          .map((item) => KnowledgeNode.fromJson(item! as Map<String, Object?>))
          .toList(growable: false),
      edges: (json['edges']! as List<Object?>)
          .map((item) => KnowledgeEdge.fromJson(item! as Map<String, Object?>))
          .toList(growable: false),
    );
  }

  final List<KnowledgeNode> nodes;
  final List<KnowledgeEdge> edges;
}

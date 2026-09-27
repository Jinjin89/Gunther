class SourceSummary {
  const SourceSummary({
    required this.id,
    required this.title,
    required this.kind,
    required this.createdAt,
    required this.assertionCount,
    required this.entityCount,
  });

  factory SourceSummary.fromJson(Map<String, Object?> json) {
    return SourceSummary(
      id: json['id']! as String,
      title: json['title']! as String,
      kind: json['kind']! as String,
      createdAt: DateTime.parse(json['createdAt']! as String),
      assertionCount: json['assertionCount']! as int,
      entityCount: json['entityCount']! as int,
    );
  }

  final String id;
  final String title;
  final String kind;
  final DateTime createdAt;
  final int assertionCount;
  final int entityCount;
}

class SourceDraft {
  const SourceDraft({
    required this.title,
    required this.kind,
    required this.content,
    this.knowledgeBaseId,
  });

  final String title;
  final String kind;
  final String content;
  final String? knowledgeBaseId;

  Map<String, Object?> toJson() => {
    'title': title,
    'kind': kind,
    'content': content,
    if (knowledgeBaseId != null) 'knowledgeBaseId': knowledgeBaseId,
  };
}

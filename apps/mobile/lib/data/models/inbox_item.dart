class InboxKnowledgeBaseRef {
  const InboxKnowledgeBaseRef({required this.id, required this.title});

  factory InboxKnowledgeBaseRef.fromJson(Map<String, Object?> json) {
    return InboxKnowledgeBaseRef(
      id: json['id']! as String,
      title: json['title']! as String,
    );
  }

  final String id;
  final String title;
}

class InboxItem {
  const InboxItem({
    required this.id,
    required this.itemType,
    required this.state,
    required this.title,
    required this.preview,
    required this.knowledgeBases,
    required this.assertionCount,
    required this.createdAt,
    required this.updatedAt,
    this.sourceKind,
    this.sourceId,
    this.noteId,
    this.proposalId,
    this.proposalStatus,
  });

  factory InboxItem.fromJson(Map<String, Object?> json) {
    final knowledgeBases = json['knowledgeBases'] as List<Object?>? ?? const [];
    return InboxItem(
      id: json['id']! as String,
      itemType: json['itemType']! as String,
      state: json['state']! as String,
      title: json['title']! as String,
      preview: json['preview']?.toString() ?? '',
      sourceKind: json['sourceKind'] as String?,
      knowledgeBases: knowledgeBases
          .map(
            (item) =>
                InboxKnowledgeBaseRef.fromJson(item! as Map<String, Object?>),
          )
          .toList(growable: false),
      sourceId: json['sourceId'] as String?,
      noteId: json['noteId'] as String?,
      proposalId: json['proposalId'] as String?,
      proposalStatus: json['proposalStatus'] as String?,
      assertionCount: json['assertionCount'] as int? ?? 0,
      createdAt: DateTime.parse(json['createdAt']! as String),
      updatedAt: DateTime.parse(json['updatedAt']! as String),
    );
  }

  final String id;
  final String itemType;
  final String state;
  final String title;
  final String preview;
  final String? sourceKind;
  final List<InboxKnowledgeBaseRef> knowledgeBases;
  final String? sourceId;
  final String? noteId;
  final String? proposalId;
  final String? proposalStatus;
  final int assertionCount;
  final DateTime createdAt;
  final DateTime updatedAt;

  bool get canBeFiled =>
      state == 'unfiled' && (sourceId != null || noteId != null);
}

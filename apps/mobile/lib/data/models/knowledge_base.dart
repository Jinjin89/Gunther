class KnowledgeBaseSummary {
  const KnowledgeBaseSummary({
    required this.id,
    required this.title,
    required this.eyebrow,
    required this.subtitle,
    required this.question,
    required this.description,
    required this.color,
    required this.status,
    required this.sourceCount,
    required this.sessionCount,
    required this.pendingProposalCount,
    required this.createdAt,
    required this.updatedAt,
  });

  factory KnowledgeBaseSummary.fromJson(Map<String, Object?> json) {
    return KnowledgeBaseSummary(
      id: json['id']! as String,
      title: json['title']! as String,
      eyebrow: json['eyebrow']! as String,
      subtitle: json['subtitle']! as String,
      question: json['question']! as String,
      description: json['description']! as String,
      color: json['color']! as String,
      status: json['status']! as String,
      sourceCount: json['sourceCount']! as int,
      sessionCount: json['sessionCount']! as int,
      pendingProposalCount: json['pendingProposalCount']! as int,
      createdAt: DateTime.parse(json['createdAt']! as String),
      updatedAt: DateTime.parse(json['updatedAt']! as String),
    );
  }

  final String id;
  final String title;
  final String eyebrow;
  final String subtitle;
  final String question;
  final String description;
  final String color;
  final String status;
  final int sourceCount;
  final int sessionCount;
  final int pendingProposalCount;
  final DateTime createdAt;
  final DateTime updatedAt;
}

class KnowledgeBaseDraft {
  const KnowledgeBaseDraft({
    required this.title,
    required this.question,
    required this.description,
    this.eyebrow = 'Personal knowledge',
    this.subtitle = 'A field worth shaping',
    this.color = 'green',
  });

  final String title;
  final String eyebrow;
  final String subtitle;
  final String question;
  final String description;
  final String color;

  Map<String, Object?> toJson() => {
    'title': title,
    'eyebrow': eyebrow,
    'subtitle': subtitle,
    'question': question,
    'description': description,
    'color': color,
  };
}

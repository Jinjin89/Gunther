class EntityReference {
  const EntityReference({
    required this.id,
    required this.label,
    required this.type,
  });

  factory EntityReference.fromJson(Map<String, Object?> json) {
    return EntityReference(
      id: json['id']! as String,
      label: json['label']! as String,
      type: json['type']! as String,
    );
  }

  final String id;
  final String label;
  final String type;
}

class Evidence {
  const Evidence({
    required this.id,
    required this.quote,
    required this.locator,
  });

  factory Evidence.fromJson(Map<String, Object?> json) {
    return Evidence(
      id: json['id']! as String,
      quote: json['quote']! as String,
      locator: json['locator']! as String,
    );
  }

  final String id;
  final String quote;
  final String locator;
}

class Assertion {
  const Assertion({
    required this.id,
    required this.predicate,
    required this.confidence,
    required this.status,
    required this.subject,
    required this.object,
    required this.sourceTitle,
    required this.evidence,
  });

  factory Assertion.fromJson(Map<String, Object?> json) {
    final source = json['source']! as Map<String, Object?>;
    final rawEvidence = json['evidence']! as List<Object?>;
    return Assertion(
      id: json['id']! as String,
      predicate: json['predicate']! as String,
      confidence: (json['confidence']! as num).toDouble(),
      status: json['status']! as String,
      subject: EntityReference.fromJson(
        json['subject']! as Map<String, Object?>,
      ),
      object: EntityReference.fromJson(json['object']! as Map<String, Object?>),
      sourceTitle: source['title']! as String,
      evidence: rawEvidence
          .map((item) => Evidence.fromJson(item! as Map<String, Object?>))
          .toList(growable: false),
    );
  }

  final String id;
  final String predicate;
  final double confidence;
  final String status;
  final EntityReference subject;
  final EntityReference object;
  final String sourceTitle;
  final List<Evidence> evidence;
}

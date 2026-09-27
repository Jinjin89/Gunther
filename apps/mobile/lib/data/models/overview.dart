import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/data/models/source.dart';

class OverviewCounts {
  const OverviewCounts({
    required this.sources,
    required this.entities,
    required this.assertions,
    required this.provisional,
  });

  factory OverviewCounts.fromJson(Map<String, Object?> json) {
    return OverviewCounts(
      sources: json['sources']! as int,
      entities: json['entities']! as int,
      assertions: json['assertions']! as int,
      provisional: json['provisional']! as int,
    );
  }

  final int sources;
  final int entities;
  final int assertions;
  final int provisional;
}

class Overview {
  const Overview({
    required this.counts,
    required this.recentSources,
    required this.recentAssertions,
  });

  factory Overview.fromJson(Map<String, Object?> json) {
    return Overview(
      counts: OverviewCounts.fromJson(json['counts']! as Map<String, Object?>),
      recentSources: (json['recentSources']! as List<Object?>)
          .map((item) => SourceSummary.fromJson(item! as Map<String, Object?>))
          .toList(growable: false),
      recentAssertions: (json['recentAssertions']! as List<Object?>)
          .map((item) => Assertion.fromJson(item! as Map<String, Object?>))
          .toList(growable: false),
    );
  }

  final OverviewCounts counts;
  final List<SourceSummary> recentSources;
  final List<Assertion> recentAssertions;
}

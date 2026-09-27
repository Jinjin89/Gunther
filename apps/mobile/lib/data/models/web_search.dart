class WebSearchSource {
  const WebSearchSource({required this.title, required this.url, this.snippet});

  factory WebSearchSource.fromJson(Map<String, Object?> json) {
    return WebSearchSource(
      title: json['title']! as String,
      url: json['url']! as String,
      snippet: json['snippet'] as String?,
    );
  }

  final String title;
  final String url;
  final String? snippet;
}

class WebSearchResult {
  const WebSearchResult({
    required this.query,
    required this.answer,
    required this.sources,
    required this.mode,
    this.message,
  });

  factory WebSearchResult.fromJson(Map<String, Object?> json) {
    final sources = json['sources'] as List<Object?>? ?? const [];
    return WebSearchResult(
      query: json['query']! as String,
      answer: json['answer']?.toString() ?? '',
      sources: sources
          .map(
            (item) => WebSearchSource.fromJson(item! as Map<String, Object?>),
          )
          .toList(growable: false),
      mode: json['mode']! as String,
      message: json['message'] as String?,
    );
  }

  final String query;
  final String answer;
  final List<WebSearchSource> sources;
  final String mode;
  final String? message;
}

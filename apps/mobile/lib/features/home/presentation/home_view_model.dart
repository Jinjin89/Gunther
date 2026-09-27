import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/models/overview.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class HomeViewModel extends ChangeNotifier {
  HomeViewModel(this._repository);

  final KnowledgeRepository _repository;

  Overview? overview;
  List<KnowledgeBaseSummary> knowledgeBases = const [];
  List<InboxItem> inboxItems = const [];
  String? error;
  bool loading = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait<Object?>([
        _repository.getOverview(),
        _repository.getKnowledgeBases(),
        _repository.getInbox(),
      ]);
      overview = results[0]! as Overview;
      knowledgeBases = results[1]! as List<KnowledgeBaseSummary>;
      inboxItems = results[2]! as List<InboxItem>;
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }
}

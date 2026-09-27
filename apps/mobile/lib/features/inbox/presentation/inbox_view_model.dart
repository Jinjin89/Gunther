import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/inbox_item.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class InboxViewModel extends ChangeNotifier {
  InboxViewModel(this._repository);

  final KnowledgeRepository _repository;

  List<InboxItem> items = const [];
  List<KnowledgeBaseSummary> libraries = const [];
  String? filter;
  String? error;
  String? filingId;
  bool loading = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait<Object?>([
        _repository.getInbox(state: filter),
        _repository.getKnowledgeBases(),
      ]);
      items = results[0]! as List<InboxItem>;
      libraries = results[1]! as List<KnowledgeBaseSummary>;
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> setFilter(String? state) async {
    filter = state;
    await load();
  }

  Future<bool> file(InboxItem item, String knowledgeBaseId) async {
    if (!item.canBeFiled) return false;
    filingId = item.id;
    error = null;
    notifyListeners();
    try {
      if (item.sourceId != null) {
        await _repository.fileSource(item.sourceId!, knowledgeBaseId);
      } else if (item.noteId != null) {
        await _repository.fileQuickNote(item.noteId!, knowledgeBaseId);
      }
      await load();
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      filingId = null;
      notifyListeners();
    }
  }
}

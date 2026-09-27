import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/knowledge_base.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class LibrariesViewModel extends ChangeNotifier {
  LibrariesViewModel(this._repository);

  final KnowledgeRepository _repository;

  List<KnowledgeBaseSummary> libraries = const [];
  String? error;
  bool loading = false;
  bool creating = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      libraries = await _repository.getKnowledgeBases();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<bool> create(KnowledgeBaseDraft draft) async {
    creating = true;
    error = null;
    notifyListeners();
    try {
      final created = await _repository.createKnowledgeBase(draft);
      libraries = [created, ...libraries];
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      creating = false;
      notifyListeners();
    }
  }
}

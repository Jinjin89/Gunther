import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/source.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class SourcesViewModel extends ChangeNotifier {
  SourcesViewModel(this._repository);

  final KnowledgeRepository _repository;
  List<SourceSummary> sources = const [];
  String? error;
  bool loading = false;
  bool importing = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      sources = await _repository.getSources();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<bool> importSource(SourceDraft draft) async {
    importing = true;
    error = null;
    notifyListeners();
    try {
      await _repository.importSource(draft);
      await load();
      return true;
    } on Object catch (exception) {
      error = exception.toString();
      return false;
    } finally {
      importing = false;
      notifyListeners();
    }
  }
}

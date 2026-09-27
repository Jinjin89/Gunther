import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/overview.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class OverviewViewModel extends ChangeNotifier {
  OverviewViewModel(this._repository);

  final KnowledgeRepository _repository;
  Overview? overview;
  String? error;
  bool loading = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      overview = await _repository.getOverview();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }
}

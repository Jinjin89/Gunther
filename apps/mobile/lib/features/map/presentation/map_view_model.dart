import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/knowledge_graph.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class MapViewModel extends ChangeNotifier {
  MapViewModel(this._repository);

  final KnowledgeRepository _repository;
  KnowledgeGraph? graph;
  String? error;
  bool loading = false;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      graph = await _repository.getGraph();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }
}

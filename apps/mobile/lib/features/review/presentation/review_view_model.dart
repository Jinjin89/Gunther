import 'package:flutter/foundation.dart';
import 'package:gunther_mobile/data/models/assertion.dart';
import 'package:gunther_mobile/data/repositories/knowledge_repository.dart';

class ReviewViewModel extends ChangeNotifier {
  ReviewViewModel(this._repository);

  final KnowledgeRepository _repository;
  List<Assertion> assertions = const [];
  String? error;
  bool loading = false;
  String? updatingId;

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      assertions = await _repository.getProvisionalAssertions();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> updateStatus(String id, String status) async {
    updatingId = id;
    error = null;
    notifyListeners();
    try {
      await _repository.updateAssertionStatus(id, status);
      assertions = assertions.where((assertion) => assertion.id != id).toList();
    } on Object catch (exception) {
      error = exception.toString();
    } finally {
      updatingId = null;
      notifyListeners();
    }
  }
}

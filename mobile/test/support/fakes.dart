import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:pickarecipe/src/core/token_store.dart';

/// In-memory stand-in for the keystore.
class FakeSecureStore implements SecureKeyValueStore {
  final Map<String, String> values = <String, String>{};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;

  @override
  Future<void> delete(String key) async => values.remove(key);
}

/// A single canned HTTP reply.
class FakeReply {
  const FakeReply(this.statusCode, [this.body = const <String, dynamic>{}]);

  final int statusCode;
  final Map<String, dynamic> body;
}

/// Dio adapter that replays queued replies per path and records every request,
/// so tests can assert on headers and call ordering without a live server.
class FakeAdapter implements HttpClientAdapter {
  FakeAdapter(this._queues);

  final Map<String, List<FakeReply>> _queues;
  final List<RequestOptions> requests = <RequestOptions>[];

  int callsTo(String path) =>
      requests.where((RequestOptions r) => r.path == path).length;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);

    final List<FakeReply>? queue = _queues[options.path];
    if (queue == null || queue.isEmpty) {
      throw StateError('No fake reply queued for ${options.path}');
    }
    // The last reply is reused once exhausted, so a test only has to queue the
    // transitions it cares about.
    final FakeReply reply =
        queue.length == 1 ? queue.first : queue.removeAt(0);

    return ResponseBody.fromString(
      jsonEncode(reply.body),
      reply.statusCode,
      headers: <String, List<String>>{
        Headers.contentTypeHeader: <String>[Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

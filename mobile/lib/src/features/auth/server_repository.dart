import 'package:dio/dio.dart';

import '../../core/api_client.dart';
import 'auth_repository.dart';

/// Builds a client for an address that is not the configured one yet.
///
/// Separate from the app's main Dio on purpose: a candidate address must be
/// checked before it is saved, and doing that on the shared client would leave
/// it pointing somewhere unverified if the check failed.
typedef ProbeClientFactory = Dio Function(String baseUrl);

Dio createProbeClient(String baseUrl) {
  return Dio(
    BaseOptions(
      baseUrl: baseUrl,
      // Shorter than the app's: this runs with somebody watching a spinner
      // after typing an address, and a wrong one is the common case.
      connectTimeout: const Duration(seconds: 8),
      receiveTimeout: const Duration(seconds: 8),
    ),
  );
}

class ServerRepository {
  const ServerRepository(this._newClient);

  final ProbeClientFactory _newClient;

  /// Asks an address how it wants to be signed into.
  ///
  /// Doubles as the check that the address is a Pick-a-Recipe server at all,
  /// which is why the failure messages talk about the address rather than about
  /// authentication.
  Future<ServerAuthStatus> statusOf(String baseUrl) async {
    final Dio client = _newClient(baseUrl);
    try {
      final Response<Map<String, dynamic>> response =
          await client.get<Map<String, dynamic>>(kAuthStatusPath);
      final Map<String, dynamic>? body = response.data;
      // A reachable host that answers with something else — a router login
      // page, an unrelated app — is not this server.
      if (body == null || !body.containsKey('sso_enabled')) {
        throw const AuthApiException(
          'That address answered, but not like a Pick-a-Recipe server. '
          'Check the address and port.',
        );
      }
      return ServerAuthStatus.fromJson(body);
    } on DioException catch (error) {
      throw AuthApiException(_describe(error, baseUrl));
    } finally {
      client.close();
    }
  }

  String _describe(DioException error, String baseUrl) {
    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return 'No answer from $baseUrl. Check the address, and that your '
            'phone can reach it from this network.';
      case DioExceptionType.badCertificate:
        return 'The HTTPS certificate at $baseUrl was rejected. A self-signed '
            'certificate will not work; use a trusted one, or plain http on a '
            'network you trust.';
      case DioExceptionType.connectionError:
        return 'Could not reach $baseUrl. Check the address and your '
            'connection.';
      case DioExceptionType.badResponse:
        final int? status = error.response?.statusCode;
        if (status == 404) {
          return 'Reached $baseUrl, but it has no Pick-a-Recipe API. Check '
              'whether the address needs a path or a different port.';
        }
        return 'The server at $baseUrl answered with an error ($status).';
      default:
        return 'Could not reach $baseUrl. Check the address and your '
            'connection.';
    }
  }
}

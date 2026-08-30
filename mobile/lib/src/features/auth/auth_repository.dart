import 'package:dio/dio.dart';

import '../../core/api_client.dart';

class MobileIdentity {
  const MobileIdentity({required this.username, required this.isAdmin});

  final String username;
  final bool isAdmin;
}

/// Thrown when the backend refuses a request for a reason worth showing.
class AuthApiException implements Exception {
  const AuthApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Wraps the three `/api/mobile/*` endpoints.
class AuthRepository {
  const AuthRepository(this._dio);

  final Dio _dio;

  /// Asks the backend for an Authentik authorization URL bound to a
  /// single-use nonce. The nonce is minted and validated server-side, so the
  /// app never handles the OIDC client secret.
  Future<Uri> loginUrl({required String redirectUri}) async {
    try {
      final Response<Map<String, dynamic>> response =
          await _dio.get<Map<String, dynamic>>(
        kLoginUrlPath,
        queryParameters: <String, String>{'redirect': redirectUri},
      );
      final String? url = response.data?['auth_url'] as String?;
      if (url == null || url.isEmpty) {
        throw const AuthApiException(
          'The server did not return a sign-in URL.',
        );
      }
      return Uri.parse(url);
    } on DioException catch (error) {
      throw AuthApiException(_describe(error));
    }
  }

  Future<MobileIdentity?> me() async {
    try {
      final Response<Map<String, dynamic>> response =
          await _dio.get<Map<String, dynamic>>(kMobileMePath);
      final String? username = response.data?['username'] as String?;
      if (username == null) return null;
      return MobileIdentity(
        username: username,
        isAdmin: response.data?['is_admin'] as bool? ?? false,
      );
    } on DioException {
      // Includes the 401 the interceptor could not rescue: treat as signed out.
      return null;
    }
  }

  String _describe(DioException error) {
    final int? status = error.response?.statusCode;
    if (status == 503) {
      return 'Mobile sign-in is not enabled on this server.';
    }
    final Object? data = error.response?.data;
    if (data is Map && data['error'] is String) {
      return data['error'] as String;
    }
    if (status != null) {
      return 'The server rejected the sign-in request ($status).';
    }
    return 'Could not reach ${_dio.options.baseUrl}. Check your connection.';
  }
}

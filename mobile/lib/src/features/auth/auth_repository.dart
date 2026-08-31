import 'package:dio/dio.dart';

import '../../core/api_client.dart';
import '../../core/token_store.dart';

class MobileIdentity {
  const MobileIdentity({required this.username, required this.isAdmin});

  final String username;
  final bool isAdmin;
}

/// How a given server expects to be signed into, from `/api/auth/status`.
///
/// Read before any credential is asked for, so the app offers the one way in
/// that instance actually has instead of a guess the server will refuse.
class ServerAuthStatus {
  const ServerAuthStatus({
    required this.localAuthEnabled,
    required this.ssoEnabled,
    required this.setupRequired,
    required this.mobileAuthEnabled,
  });

  factory ServerAuthStatus.fromJson(Map<String, dynamic> json) {
    return ServerAuthStatus(
      localAuthEnabled: json['local_auth_enabled'] as bool? ?? false,
      ssoEnabled: json['sso_enabled'] as bool? ?? false,
      setupRequired: json['setup_required'] as bool? ?? false,
      mobileAuthEnabled: json['mobile_auth_enabled'] as bool? ?? false,
    );
  }

  /// Username and password accounts held by the instance itself.
  final bool localAuthEnabled;

  /// Authentik, reached through the system browser.
  final bool ssoEnabled;

  /// No account exists yet; nobody can sign in until one is made in a browser.
  final bool setupRequired;

  /// JWT_SECRET_KEY is set, without which no app sign-in works at all.
  final bool mobileAuthEnabled;
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

  /// Exchanges a local username and password for a token pair.
  ///
  /// Only servers running `AUTH_MODE=local` accept this; the caller decides
  /// whether to offer it by reading [ServerAuthStatus] first.
  Future<AuthTokens> signInWithPassword({
    required String username,
    required String password,
  }) async {
    try {
      final Response<Map<String, dynamic>> response =
          await _dio.post<Map<String, dynamic>>(
        kPasswordLoginPath,
        data: <String, String>{'username': username, 'password': password},
      );
      final String? access = response.data?['access_token'] as String?;
      final String? refresh = response.data?['refresh_token'] as String?;
      if (access == null || refresh == null) {
        throw const AuthApiException(
          'The server did not return a session. Please try again.',
        );
      }
      return AuthTokens(accessToken: access, refreshToken: refresh);
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

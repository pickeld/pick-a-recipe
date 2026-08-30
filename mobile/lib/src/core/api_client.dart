import 'dart:async';

import 'package:dio/dio.dart';

import 'token_store.dart';

/// Backend paths for the mobile auth handshake.
const String kRefreshPath = '/api/mobile/auth/refresh';
const String kLoginUrlPath = '/api/mobile/auth/login-url';
const String kMobileMePath = '/api/mobile/me';

/// Attaches the bearer token to outgoing requests and renews it once when the
/// backend rejects it, so a 15-minute access token expiring mid-session is
/// invisible to the user.
class AuthInterceptor extends Interceptor {
  AuthInterceptor({
    required this._dio,
    required this._tokenStore,
    required this._onSignedOut,
  });

  static const String _retriedFlag = 'auth.retried';

  final Dio _dio;
  final TokenStore _tokenStore;
  final Future<void> Function() _onSignedOut;

  /// Shared across callers so a burst of parallel 401s spends one refresh
  /// token instead of racing several and invalidating each other.
  Future<AuthTokens?>? _inFlightRefresh;

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    // The refresh call authenticates with the refresh token in its body; an
    // expired access token in the header would only muddy the result.
    if (options.path != kRefreshPath) {
      final AuthTokens? tokens = await _tokenStore.read();
      if (tokens != null) {
        options.headers['Authorization'] = 'Bearer ${tokens.accessToken}';
      }
    }
    handler.next(options);
  }

  @override
  Future<void> onError(
    DioException err,
    ErrorInterceptorHandler handler,
  ) async {
    final RequestOptions request = err.requestOptions;
    final bool retryable = err.response?.statusCode == 401 &&
        request.path != kRefreshPath &&
        request.extra[_retriedFlag] != true;
    if (!retryable) {
      return handler.next(err);
    }

    final AuthTokens? tokens = await _refresh();
    if (tokens == null) {
      await _onSignedOut();
      return handler.next(err);
    }

    request.extra[_retriedFlag] = true;
    request.headers['Authorization'] = 'Bearer ${tokens.accessToken}';
    try {
      // fetch dispatches without re-running interceptors, so the header set
      // above is the one that goes out.
      handler.resolve(await _dio.fetch<dynamic>(request));
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  Future<AuthTokens?> _refresh() {
    return _inFlightRefresh ??= _performRefresh().whenComplete(() {
      _inFlightRefresh = null;
    });
  }

  Future<AuthTokens?> _performRefresh() async {
    final AuthTokens? current = await _tokenStore.read();
    if (current == null) return null;

    try {
      final Response<Map<String, dynamic>> response =
          await _dio.post<Map<String, dynamic>>(
        kRefreshPath,
        data: <String, String>{'refresh_token': current.refreshToken},
      );
      final Map<String, dynamic>? body = response.data;
      final String? access = body?['access_token'] as String?;
      final String? refresh = body?['refresh_token'] as String?;
      if (access == null || refresh == null) {
        await _tokenStore.clear();
        return null;
      }
      final AuthTokens tokens =
          AuthTokens(accessToken: access, refreshToken: refresh);
      await _tokenStore.save(tokens);
      return tokens;
    } on DioException {
      // A rejected refresh token is terminal: drop the pair so the app asks
      // for a fresh sign-in rather than retrying a credential that is gone.
      await _tokenStore.clear();
      return null;
    }
  }
}

Dio createApiClient({
  required String baseUrl,
  required TokenStore tokenStore,
  required Future<void> Function() onSignedOut,
  HttpClientAdapter? adapter,
}) {
  final Dio dio = Dio(
    BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
    ),
  );
  if (adapter != null) {
    dio.httpClientAdapter = adapter;
  }
  dio.interceptors.add(
    AuthInterceptor(
      dio: dio,
      tokenStore: tokenStore,
      onSignedOut: onSignedOut,
    ),
  );
  return dio;
}

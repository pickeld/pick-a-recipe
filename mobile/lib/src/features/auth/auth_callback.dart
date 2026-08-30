import '../../core/token_store.dart';

/// Custom scheme the backend redirects to once it has exchanged the OIDC code.
/// Must stay in step with the intent filter in AndroidManifest.xml and the
/// server's MOBILE_DEEP_LINK_SCHEMES.
const String kAuthCallbackUri = 'par://auth/callback';

const String _scheme = 'par';
const String _host = 'auth';
const String _path = '/callback';

/// Result of the `par://auth/callback` deep link.
sealed class AuthCallback {
  const AuthCallback();
}

class AuthCallbackSuccess extends AuthCallback {
  const AuthCallbackSuccess(this.tokens);

  final AuthTokens tokens;
}

class AuthCallbackFailure extends AuthCallback {
  const AuthCallbackFailure(this.code);

  final String code;

  /// The server sends machine-readable codes, so the wording lives here rather
  /// than being parsed out of a response body.
  String get message => switch (code) {
        'not_authorized' =>
          'Your account is not authorized to use Pick-a-Recipe. Ask an '
              'administrator to add you to the right Authentik group.',
        'server_misconfigured' =>
          'Mobile sign-in is not configured on the server.',
        'token_exchange_failed' =>
          'Could not reach the identity provider. Please try again.',
        'invalid_request' || 'invalid_grant' =>
          'Sign-in did not complete. Please try again.',
        'missing_tokens' =>
          'The sign-in link did not contain any credentials.',
        _ => 'Sign-in failed. Please try again.',
      };
}

/// Parses an incoming deep link.
///
/// Returns null when [uri] is not the auth callback, so unrelated deep links
/// (shared recipe URLs, for instance) fall through to their own handlers.
AuthCallback? parseAuthCallback(Uri uri) {
  if (uri.scheme.toLowerCase() != _scheme) return null;
  if (uri.host.toLowerCase() != _host) return null;
  if (uri.path != _path) return null;

  // Tokens arrive in the fragment rather than the query string: fragments are
  // not sent to servers and do not leak through the Referer header.
  final Map<String, String> params = Uri.splitQueryString(uri.fragment);

  final String? error = params['error'];
  if (error != null && error.isNotEmpty) {
    return AuthCallbackFailure(error);
  }

  final String? access = params['access_token'];
  final String? refresh = params['refresh_token'];
  if (access == null || access.isEmpty || refresh == null || refresh.isEmpty) {
    return const AuthCallbackFailure('missing_tokens');
  }

  return AuthCallbackSuccess(
    AuthTokens(accessToken: access, refreshToken: refresh),
  );
}

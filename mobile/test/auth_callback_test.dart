import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/features/auth/auth_callback.dart';

void main() {
  group('parseAuthCallback', () {
    test('extracts the token pair from the fragment', () {
      final AuthCallback? result = parseAuthCallback(
        Uri.parse(
          'par://auth/callback#access_token=aaa&refresh_token=rrr'
          '&token_type=Bearer&expires_in=900',
        ),
      );

      expect(result, isA<AuthCallbackSuccess>());
      final AuthCallbackSuccess success = result! as AuthCallbackSuccess;
      expect(success.tokens.accessToken, 'aaa');
      expect(success.tokens.refreshToken, 'rrr');
    });

    test('percent-encoded token values survive the round trip', () {
      final AuthCallback? result = parseAuthCallback(
        Uri.parse(
          'par://auth/callback#access_token=a%2Bb%2Fc%3D&refresh_token=r%2Bs',
        ),
      );

      final AuthCallbackSuccess success = result! as AuthCallbackSuccess;
      expect(success.tokens.accessToken, 'a+b/c=');
      expect(success.tokens.refreshToken, 'r+s');
    });

    test('surfaces a server error code with readable copy', () {
      final AuthCallback? result =
          parseAuthCallback(Uri.parse('par://auth/callback#error=not_authorized'));

      expect(result, isA<AuthCallbackFailure>());
      final AuthCallbackFailure failure = result! as AuthCallbackFailure;
      expect(failure.code, 'not_authorized');
      expect(failure.message, contains('not authorized'));
    });

    test('unknown error codes still produce a usable message', () {
      final AuthCallbackFailure failure = parseAuthCallback(
        Uri.parse('par://auth/callback#error=something_new'),
      )! as AuthCallbackFailure;

      expect(failure.message, isNotEmpty);
      expect(failure.message, isNot(contains('something_new')));
    });

    test('a fragment with neither tokens nor error is a failure', () {
      final AuthCallback? result =
          parseAuthCallback(Uri.parse('par://auth/callback'));

      expect((result! as AuthCallbackFailure).code, 'missing_tokens');
    });

    test('half a token pair is rejected rather than half-stored', () {
      final AuthCallback? result = parseAuthCallback(
        Uri.parse('par://auth/callback#access_token=aaa'),
      );

      expect((result! as AuthCallbackFailure).code, 'missing_tokens');
    });

    test('returns null for deep links that are not the auth callback', () {
      // Must stay null so unrelated deep links reach their own handlers.
      expect(parseAuthCallback(Uri.parse('par://recipes/42')), isNull);
      expect(parseAuthCallback(Uri.parse('https://auth/callback')), isNull);
      expect(parseAuthCallback(Uri.parse('par://auth/other')), isNull);
    });

    test('scheme and host matching is case-insensitive', () {
      // Android can hand back a normalised or upper-cased authority.
      final AuthCallback? result = parseAuthCallback(
        Uri.parse('PAR://AUTH/callback#access_token=a&refresh_token=r'),
      );

      expect(result, isA<AuthCallbackSuccess>());
    });

    test('the advertised callback constant is the one that parses', () {
      // Guards against the constant and the matcher drifting apart.
      expect(
        parseAuthCallback(Uri.parse('$kAuthCallbackUri#error=x')),
        isA<AuthCallbackFailure>(),
      );
    });
  });
}

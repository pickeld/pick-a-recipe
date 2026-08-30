import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/core/token_store.dart';

import 'support/fakes.dart';

void main() {
  late FakeSecureStore store;
  late TokenStore tokens;

  setUp(() {
    store = FakeSecureStore();
    tokens = TokenStore(store);
  });

  test('round-trips a token pair', () async {
    await tokens.save(
      const AuthTokens(accessToken: 'aaa', refreshToken: 'rrr'),
    );

    final AuthTokens? read = await tokens.read();
    expect(read?.accessToken, 'aaa');
    expect(read?.refreshToken, 'rrr');
  });

  test('reads null when nothing is stored', () async {
    expect(await tokens.read(), isNull);
  });

  test('a partial pair reads as no session', () async {
    // An access token with no refresh token cannot be renewed, so treating it
    // as a session would strand the user at the first 401.
    store.values['auth.access_token'] = 'aaa';

    expect(await tokens.read(), isNull);
  });

  test('clear removes both halves', () async {
    await tokens.save(
      const AuthTokens(accessToken: 'aaa', refreshToken: 'rrr'),
    );

    await tokens.clear();

    expect(store.values, isEmpty);
    expect(await tokens.read(), isNull);
  });

  test('tokens are not stored under guessable plaintext-ish keys', () async {
    await tokens.save(
      const AuthTokens(accessToken: 'aaa', refreshToken: 'rrr'),
    );

    // Namespaced keys keep them from colliding with other secure-storage users.
    expect(store.values.keys, everyElement(startsWith('auth.')));
  });
}

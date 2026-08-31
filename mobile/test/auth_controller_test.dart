import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/core/api_client.dart';
import 'package:pickarecipe/src/core/providers.dart';
import 'package:pickarecipe/src/core/token_store.dart';
import 'package:pickarecipe/src/features/auth/auth_callback.dart';
import 'package:pickarecipe/src/features/auth/auth_controller.dart';
import 'package:pickarecipe/src/features/auth/auth_state.dart';

import 'support/fakes.dart';

void main() {
  late FakeSecureStore secureStore;
  late List<Uri> launched;

  setUp(() {
    secureStore = FakeSecureStore();
    launched = <Uri>[];
  });

  /// Builds a container wired to a fake transport and a recording launcher.
  ProviderContainer harnessWith(
    FakeAdapter adapter, {
    bool launchSucceeds = true,
  }) {
    final ProviderContainer container = ProviderContainer(
      overrides: [
        secureStoreProvider.overrideWithValue(secureStore),
        urlLauncherProvider.overrideWithValue((Uri url) async {
          launched.add(url);
          return launchSucceeds;
        }),
        dioProvider.overrideWith(
          (Ref ref) => createApiClient(
            baseUrl: 'https://recipes.example.com',
            tokenStore: ref.watch(tokenStoreProvider),
            onSignedOut: () async =>
                ref.read(authControllerProvider.notifier).signOut(),
            adapter: adapter,
          ),
        ),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  ProviderContainer harness(
    Map<String, List<FakeReply>> replies, {
    bool launchSucceeds = true,
  }) {
    return harnessWith(FakeAdapter(replies), launchSucceeds: launchSucceeds);
  }

  AuthController controllerOf(ProviderContainer c) =>
      c.read(authControllerProvider.notifier);
  AuthState stateOf(ProviderContainer c) => c.read(authControllerProvider);

  Future<void> seed(ProviderContainer c) => c.read(tokenStoreProvider).save(
        const AuthTokens(accessToken: 'access-1', refreshToken: 'refresh-1'),
      );

  group('signIn', () {
    test('launches the URL the backend hands back', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kLoginUrlPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'auth_url': 'https://id.example.com/authorize?state=nonce-1',
          }),
        ],
      });

      await controllerOf(c).signIn();

      expect(launched.single.toString(),
          'https://id.example.com/authorize?state=nonce-1');
      // Stays busy: the browser is in front of the user until the deep link.
      expect(stateOf(c).isBusy, isTrue);
      expect(stateOf(c).errorMessage, isNull);
    });

    test('asks for the deep link the manifest actually registers', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kLoginUrlPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'auth_url': 'https://id.example.com/authorize',
          }),
        ],
      });

      await controllerOf(c).signIn();

      expect(kAuthCallbackUri, 'par://auth/callback');
    });

    test('explains a server without mobile auth enabled', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kLoginUrlPath: <FakeReply>[const FakeReply(503)],
      });

      await controllerOf(c).signIn();

      expect(stateOf(c).isBusy, isFalse);
      expect(stateOf(c).errorMessage, contains('not enabled'));
      expect(launched, isEmpty);
    });

    test('reports when no browser could be opened', () async {
      final ProviderContainer c = harness(
        <String, List<FakeReply>>{
          kLoginUrlPath: <FakeReply>[
            const FakeReply(200, <String, dynamic>{
              'auth_url': 'https://id.example.com/authorize',
            }),
          ],
        },
        launchSucceeds: false,
      );

      await controllerOf(c).signIn();

      expect(stateOf(c).isBusy, isFalse);
      expect(stateOf(c).errorMessage, contains('No browser'));
    });

    test('a second tap while busy does not burn another nonce', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kLoginUrlPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'auth_url': 'https://id.example.com/authorize',
          }),
        ],
      });

      await controllerOf(c).signIn();
      await controllerOf(c).signIn();

      expect(launched, hasLength(1));
    });
  });

  group('signInWithPassword', () {
    test('stores the pair the server returns and adopts the identity',
        () async {
      // No browser and no deep link on this path: the server hands the tokens
      // straight back, so sign-in completes in one call.
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'access_token': 'a1',
            'refresh_token': 'r1',
          }),
        ],
        kMobileMePath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'username': 'ada',
            'is_admin': false,
          }),
        ],
      });

      await controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'correct horse battery',
      );

      final AuthState state = stateOf(c);
      expect(state.status, AuthStatus.signedIn);
      expect(state.username, 'ada');
      expect(state.isAdmin, isFalse);
      expect(launched, isEmpty);
      final AuthTokens? stored = await c.read(tokenStoreProvider).read();
      expect(stored?.refreshToken, 'r1');
    });

    test('shows the rejection and stores nothing', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(401, <String, dynamic>{
            'error': 'Incorrect username or password.',
          }),
        ],
      });

      await controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'wrong',
      );

      expect(stateOf(c).status, AuthStatus.signedOut);
      expect(stateOf(c).isBusy, isFalse);
      expect(stateOf(c).errorMessage, contains('Incorrect username'));
      expect(secureStore.values, isEmpty);
    });

    test('passes the throttle message through verbatim', () async {
      // The wait is the useful part; a generic "try again" would leave the user
      // tapping.
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(429, <String, dynamic>{
            'error': 'Too many attempts. Try again in 8 seconds.',
          }),
        ],
      });

      await controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'wrong',
      );

      expect(stateOf(c).errorMessage, contains('8 seconds'));
    });

    test('explains a server that only does single sign-on', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(400, <String, dynamic>{
            'error': 'This server uses single sign-on. Sign in with Authentik.',
          }),
        ],
      });

      await controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'whatever',
      );

      expect(stateOf(c).errorMessage, contains('single sign-on'));
    });

    test('discards a pair the server will not then vouch for', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'access_token': 'a1',
            'refresh_token': 'r1',
          }),
        ],
        kMobileMePath: <FakeReply>[const FakeReply(401)],
        kRefreshPath: <FakeReply>[const FakeReply(401)],
      });

      await controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'correct horse battery',
      );

      expect(stateOf(c).status, AuthStatus.signedOut);
      expect(await c.read(tokenStoreProvider).read(), isNull);
    });

    test('a second submit while busy does not send a second attempt', () async {
      // Otherwise a double tap spends two entries on the server's throttle
      // ladder for one intended sign-in.
      final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
        kPasswordLoginPath: <FakeReply>[
          const FakeReply(401, <String, dynamic>{'error': 'nope'}),
        ],
      });
      final ProviderContainer c = harnessWith(adapter);

      final Future<void> first = controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'wrong',
      );
      final Future<void> second = controllerOf(c).signInWithPassword(
        username: 'ada',
        password: 'wrong',
      );
      await Future.wait(<Future<void>>[first, second]);

      expect(adapter.callsTo(kPasswordLoginPath), 1);
    });
  });

  group('completeSignIn', () {
    test('stores the tokens and adopts the confirmed identity', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kMobileMePath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'username': 'ada',
            'is_admin': true,
          }),
        ],
      });

      await controllerOf(c).completeSignIn(
        Uri.parse('par://auth/callback#access_token=a1&refresh_token=r1'),
      );

      final AuthState state = stateOf(c);
      expect(state.status, AuthStatus.signedIn);
      expect(state.username, 'ada');
      expect(state.isAdmin, isTrue);
      final AuthTokens? stored = await c.read(tokenStoreProvider).read();
      expect(stored?.accessToken, 'a1');
    });

    test('shows the server error and stores nothing', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{});

      await controllerOf(c).completeSignIn(
        Uri.parse('par://auth/callback#error=not_authorized'),
      );

      expect(stateOf(c).status, AuthStatus.signedOut);
      expect(stateOf(c).errorMessage, contains('not authorized'));
      expect(secureStore.values, isEmpty);
    });

    test('discards tokens the server will not vouch for', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kMobileMePath: <FakeReply>[const FakeReply(401)],
        kRefreshPath: <FakeReply>[const FakeReply(401)],
      });

      await controllerOf(c).completeSignIn(
        Uri.parse('par://auth/callback#access_token=a1&refresh_token=r1'),
      );

      expect(stateOf(c).status, AuthStatus.signedOut);
      expect(await c.read(tokenStoreProvider).read(), isNull);
    });

    test('ignores deep links that are not the auth callback', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{});
      final AuthStatus before = stateOf(c).status;

      await controllerOf(c).completeSignIn(Uri.parse('par://recipes/42'));

      expect(stateOf(c).status, before);
      expect(secureStore.values, isEmpty);
    });
  });

  group('restore', () {
    test('with no stored tokens goes straight to signed out', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{});

      await controllerOf(c).restore();

      expect(stateOf(c).status, AuthStatus.signedOut);
    });

    test('revalidates stored tokens against the backend', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kMobileMePath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{'username': 'ada'}),
        ],
      });
      await seed(c);

      await controllerOf(c).restore();

      expect(stateOf(c).status, AuthStatus.signedIn);
      expect(stateOf(c).username, 'ada');
      expect(stateOf(c).isAdmin, isFalse);
    });

    test('drops a session the backend no longer accepts', () async {
      // Covers a revoked or group-removed account: stale tokens must not
      // survive on the device just because they parse.
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kMobileMePath: <FakeReply>[const FakeReply(401)],
        kRefreshPath: <FakeReply>[const FakeReply(401)],
      });
      await seed(c);

      await controllerOf(c).restore();

      expect(stateOf(c).status, AuthStatus.signedOut);
      expect(await c.read(tokenStoreProvider).read(), isNull);
    });

    test('a silent refresh keeps the user signed in', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kMobileMePath: <FakeReply>[
          const FakeReply(401),
          const FakeReply(200, <String, dynamic>{'username': 'ada'}),
        ],
        kRefreshPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'access_token': 'a2',
            'refresh_token': 'r2',
          }),
        ],
      });
      await seed(c);

      await controllerOf(c).restore();

      expect(stateOf(c).status, AuthStatus.signedIn);
      expect(stateOf(c).username, 'ada');
      expect((await c.read(tokenStoreProvider).read())?.accessToken, 'a2');
    });
  });

  test('signOut clears the stored session', () async {
    final ProviderContainer c = harness(<String, List<FakeReply>>{});
    await seed(c);

    await controllerOf(c).signOut();

    expect(stateOf(c).status, AuthStatus.signedOut);
    expect(await c.read(tokenStoreProvider).read(), isNull);
    expect(secureStore.values, isEmpty);
  });
}

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

  /// Builds a container wired to canned HTTP replies and a recording launcher.
  ProviderContainer harness(
    Map<String, List<FakeReply>> replies, {
    bool launchSucceeds = true,
  }) {
    final FakeAdapter adapter = FakeAdapter(replies);
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

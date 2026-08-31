import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/config/app_config.dart';
import 'package:pickarecipe/src/core/api_client.dart';
import 'package:pickarecipe/src/core/providers.dart';
import 'package:pickarecipe/src/core/token_store.dart';
import 'package:pickarecipe/src/features/auth/server_controller.dart';
import 'package:pickarecipe/src/features/auth/server_state.dart';

import 'support/fakes.dart';

/// A server running local accounts, with app sign-in enabled.
const Map<String, dynamic> _localReady = <String, dynamic>{
  'auth_mode': 'local',
  'local_auth_enabled': true,
  'sso_enabled': false,
  'setup_required': false,
  'mobile_auth_enabled': true,
};

void main() {
  late FakeSecureStore secureStore;
  late List<String> probed;

  setUp(() {
    secureStore = FakeSecureStore();
    probed = <String>[];
  });

  /// Wires the probe client to canned replies and records which addresses were
  /// asked, so tests can assert that nothing unexpected was contacted.
  ProviderContainer harness(
    Map<String, List<FakeReply>> replies, {
    String? suggested,
    DioException? failWith,
  }) {
    final ProviderContainer container = ProviderContainer(
      overrides: [
        secureStoreProvider.overrideWithValue(secureStore),
        configProvider.overrideWithValue(
          AppConfig(flavor: 'prod', apiBaseUrlOverride: suggested),
        ),
        probeClientFactoryProvider.overrideWithValue((String baseUrl) {
          probed.add(baseUrl);
          final Dio dio = Dio(BaseOptions(baseUrl: baseUrl));
          dio.httpClientAdapter = failWith != null
              ? ThrowingAdapter(failWith)
              : FakeAdapter(replies);
          return dio;
        }),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  ServerController controllerOf(ProviderContainer c) =>
      c.read(serverControllerProvider.notifier);
  ServerState stateOf(ProviderContainer c) => c.read(serverControllerProvider);

  group('connect', () {
    test('normalises what was typed before contacting anything', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[const FakeReply(200, _localReady)],
      });

      expect(await controllerOf(c).connect('  recipes.example.com/  '), isTrue);

      expect(probed.single, 'https://recipes.example.com');
      expect(stateOf(c).baseUrl, 'https://recipes.example.com');
      expect(await secureStore.read('server.base_url'),
          'https://recipes.example.com');
    });

    test('adopts what the server said about signing in', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{
            'sso_enabled': true,
            'local_auth_enabled': false,
            'setup_required': false,
            'mobile_auth_enabled': true,
          }),
        ],
      });

      await controllerOf(c).connect('recipes.example.com');

      final ServerState state = stateOf(c);
      expect(state.isReady, isTrue);
      expect(state.status!.ssoEnabled, isTrue);
      expect(state.status!.localAuthEnabled, isFalse);
    });

    test('explains input that is not an address, and contacts nothing',
        () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{});

      expect(await controllerOf(c).connect('not a url at all'), isFalse);

      expect(probed, isEmpty);
      expect(stateOf(c).baseUrl, isNull);
      expect(stateOf(c).errorMessage, contains('web address'));
    });

    test('saves nothing when the address cannot be reached', () async {
      // Otherwise a typo would strand the app on an address it can never reach,
      // with no form left on screen to correct it.
      final ProviderContainer c = harness(
        <String, List<FakeReply>>{},
        failWith: DioException.connectionError(
          requestOptions: RequestOptions(path: kAuthStatusPath),
          reason: 'no route to host',
        ),
      );

      expect(await controllerOf(c).connect('recipes.example.com'), isFalse);

      expect(stateOf(c).baseUrl, isNull);
      expect(await secureStore.read('server.base_url'), isNull);
      expect(stateOf(c).errorMessage, contains('recipes.example.com'));
    });

    test('rejects a host that answers with something else', () async {
      // A router admin page or an unrelated app on that port: reachable, but
      // not this server, and every later request would fail confusingly.
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[
          const FakeReply(200, <String, dynamic>{'hello': 'world'}),
        ],
      });

      expect(await controllerOf(c).connect('recipes.example.com'), isFalse);

      expect(stateOf(c).baseUrl, isNull);
      expect(stateOf(c).errorMessage, contains('not like a Pick-a-Recipe'));
    });

    test('a 404 points at the address rather than at authentication', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[const FakeReply(404)],
      });

      await controllerOf(c).connect('example.com');

      expect(stateOf(c).errorMessage, contains('no Pick-a-Recipe API'));
    });
  });

  group('restore', () {
    test('loads the saved address and re-reads its status', () async {
      await secureStore.write('server.base_url', 'https://recipes.example.com');
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[const FakeReply(200, _localReady)],
      });

      expect(await controllerOf(c).restore(), 'https://recipes.example.com');

      expect(stateOf(c).isReady, isTrue);
      expect(stateOf(c).status!.localAuthEnabled, isTrue);
    });

    test('keeps the address when the server is merely down', () async {
      // An instance being unreachable on one launch is not a reason to make
      // somebody type its address again.
      await secureStore.write('server.base_url', 'https://recipes.example.com');
      final ProviderContainer c = harness(
        <String, List<FakeReply>>{},
        failWith: DioException.connectionTimeout(
          timeout: const Duration(seconds: 8),
          requestOptions: RequestOptions(path: kAuthStatusPath),
        ),
      );

      expect(await controllerOf(c).restore(), 'https://recipes.example.com');

      final ServerState state = stateOf(c);
      expect(state.baseUrl, 'https://recipes.example.com');
      expect(state.status, isNull);
      expect(state.isReady, isFalse);
      expect(state.errorMessage, isNotNull);
    });

    test('reports no address on a fresh install', () async {
      final ProviderContainer c = harness(<String, List<FakeReply>>{});

      expect(await controllerOf(c).restore(), isNull);

      expect(probed, isEmpty);
      expect(stateOf(c).baseUrl, isNull);
    });
  });

  group('recheck', () {
    test('picks up a server that has come back', () async {
      await secureStore.write('server.base_url', 'https://recipes.example.com');
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[
          const FakeReply(503),
          const FakeReply(200, _localReady),
        ],
      });

      await controllerOf(c).restore();
      expect(stateOf(c).isReady, isFalse);

      await controllerOf(c).recheck();

      expect(stateOf(c).isReady, isTrue);
      expect(stateOf(c).errorMessage, isNull);
    });
  });

  group('forget', () {
    test('drops the address and the tokens issued by it', () async {
      // Tokens are only meaningful to the server that minted them; carrying
      // them to a different instance would attach a stranger's bearer token to
      // its requests.
      await secureStore.write('server.base_url', 'https://recipes.example.com');
      final ProviderContainer c = harness(<String, List<FakeReply>>{
        kAuthStatusPath: <FakeReply>[const FakeReply(200, _localReady)],
      });
      await c.read(tokenStoreProvider).save(
            const AuthTokens(accessToken: 'a', refreshToken: 'r'),
          );
      await controllerOf(c).restore();

      await controllerOf(c).forget();

      expect(stateOf(c).baseUrl, isNull);
      expect(stateOf(c).status, isNull);
      expect(await c.read(tokenStoreProvider).read(), isNull);
      expect(await secureStore.read('server.base_url'), isNull);
    });
  });

  group('suggestedUrl', () {
    test('carries the dev prefill through, and survives a failed connect',
        () async {
      final ProviderContainer c = harness(
        <String, List<FakeReply>>{},
        suggested: 'http://10.0.2.2:5006',
      );

      expect(stateOf(c).suggestedUrl, 'http://10.0.2.2:5006');

      await controllerOf(c).connect('nonsense');

      expect(stateOf(c).suggestedUrl, 'http://10.0.2.2:5006');
    });
  });
}

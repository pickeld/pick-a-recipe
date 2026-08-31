import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/core/api_client.dart';
import 'package:pickarecipe/src/core/token_store.dart';

import 'support/fakes.dart';

const String _protectedPath = '/api/mobile/me';

void main() {
  late FakeSecureStore secureStore;
  late TokenStore tokenStore;
  late int signedOutCalls;

  setUp(() {
    secureStore = FakeSecureStore();
    tokenStore = TokenStore(secureStore);
    signedOutCalls = 0;
  });

  Dio build(FakeAdapter adapter) => createApiClient(
        baseUrl: 'https://recipes.example.com',
        tokenStore: tokenStore,
        onSignedOut: () async => signedOutCalls++,
        adapter: adapter,
      );

  Future<void> seedTokens({String access = 'old-access'}) => tokenStore.save(
        AuthTokens(accessToken: access, refreshToken: 'refresh-1'),
      );

  test('attaches the bearer token to outgoing requests', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{'username': 'ada'}),
      ],
    });

    await build(adapter).get<Map<String, dynamic>>(_protectedPath);

    expect(
      adapter.requests.single.headers['Authorization'],
      'Bearer old-access',
    );
  });

  test('sends no Authorization header when there is no session', () async {
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{'username': 'ada'}),
      ],
    });

    await build(adapter).get<Map<String, dynamic>>(_protectedPath);

    expect(adapter.requests.single.headers.containsKey('Authorization'), isFalse);
  });

  test('refreshes once on 401 and replays the original request', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[
        const FakeReply(401, <String, dynamic>{'error': 'expired'}),
        const FakeReply(200, <String, dynamic>{'username': 'ada'}),
      ],
      kRefreshPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{
          'access_token': 'new-access',
          'refresh_token': 'refresh-2',
        }),
      ],
    });

    final Response<Map<String, dynamic>> response =
        await build(adapter).get<Map<String, dynamic>>(_protectedPath);

    expect(response.statusCode, 200);
    expect(response.data?['username'], 'ada');
    expect(adapter.callsTo(kRefreshPath), 1);
    expect(adapter.callsTo(_protectedPath), 2);
    // The replay must carry the new token, not the one that just failed.
    expect(
      adapter.requests.last.headers['Authorization'],
      'Bearer new-access',
    );
    // The rotated pair is persisted for the next launch.
    final AuthTokens? stored = await tokenStore.read();
    expect(stored?.accessToken, 'new-access');
    expect(stored?.refreshToken, 'refresh-2');
    expect(signedOutCalls, 0);
  });

  test('the refresh call itself carries no stale bearer header', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[
        const FakeReply(401),
        const FakeReply(200, <String, dynamic>{'username': 'ada'}),
      ],
      kRefreshPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{
          'access_token': 'new-access',
          'refresh_token': 'refresh-2',
        }),
      ],
    });

    await build(adapter).get<Map<String, dynamic>>(_protectedPath);

    final RequestOptions refreshCall = adapter.requests
        .firstWhere((RequestOptions r) => r.path == kRefreshPath);
    expect(refreshCall.headers.containsKey('Authorization'), isFalse);
    expect(refreshCall.data, <String, String>{'refresh_token': 'refresh-1'});
  });

  test('a rejected refresh clears the session and signals sign-out', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[const FakeReply(401)],
      kRefreshPath: <FakeReply>[
        const FakeReply(401, <String, dynamic>{'error': 'invalid refresh'}),
      ],
    });

    await expectLater(
      build(adapter).get<Map<String, dynamic>>(_protectedPath),
      throwsA(isA<DioException>()),
    );

    expect(await tokenStore.read(), isNull);
    expect(signedOutCalls, 1);
    // No second attempt at the original request once the refresh is refused.
    expect(adapter.callsTo(_protectedPath), 1);
  });

  test('a 401 is retried at most once', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      // Still 401 even with a valid new token: must not loop forever.
      _protectedPath: <FakeReply>[const FakeReply(401)],
      kRefreshPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{
          'access_token': 'new-access',
          'refresh_token': 'refresh-2',
        }),
      ],
    });

    await expectLater(
      build(adapter).get<Map<String, dynamic>>(_protectedPath),
      throwsA(isA<DioException>()),
    );

    expect(adapter.callsTo(_protectedPath), 2);
    expect(adapter.callsTo(kRefreshPath), 1);
  });

  test('parallel 401s spend a single refresh token', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[
        const FakeReply(401),
        const FakeReply(401),
        const FakeReply(401),
        const FakeReply(200, <String, dynamic>{'username': 'ada'}),
      ],
      kRefreshPath: <FakeReply>[
        const FakeReply(200, <String, dynamic>{
          'access_token': 'new-access',
          'refresh_token': 'refresh-2',
        }),
      ],
    });
    final Dio dio = build(adapter);

    await Future.wait<void>(<Future<void>>[
      dio.get<Map<String, dynamic>>(_protectedPath),
      dio.get<Map<String, dynamic>>(_protectedPath),
      dio.get<Map<String, dynamic>>(_protectedPath),
    ]);

    // Three concurrent failures, one refresh: rotating the token three times
    // would invalidate the pair the other two are about to use.
    expect(adapter.callsTo(kRefreshPath), 1);
  });

  test('non-401 failures are passed through untouched', () async {
    await seedTokens();
    final FakeAdapter adapter = FakeAdapter(<String, List<FakeReply>>{
      _protectedPath: <FakeReply>[const FakeReply(500)],
    });

    await expectLater(
      build(adapter).get<Map<String, dynamic>>(_protectedPath),
      throwsA(isA<DioException>()),
    );

    expect(adapter.callsTo(kRefreshPath), 0);
    expect(signedOutCalls, 0);
    // A server error must not cost the user their session.
    expect(await tokenStore.read(), isNotNull);
  });
}

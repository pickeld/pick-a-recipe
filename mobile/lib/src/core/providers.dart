import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../config/app_config.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/auth_repository.dart';
import '../features/auth/server_controller.dart';
import '../features/auth/server_repository.dart';
import 'api_client.dart';
import 'server_store.dart';
import 'token_store.dart';

/// Overridden with the real [AppConfig] in main() before runApp.
final configProvider = Provider<AppConfig>(
  (ref) => throw UnimplementedError(
    'configProvider must be overridden in main()',
  ),
);

/// Overridden in tests with an in-memory store.
final secureStoreProvider = Provider<SecureKeyValueStore>(
  (ref) => const KeystoreKeyValueStore(),
);

final tokenStoreProvider = Provider<TokenStore>(
  (ref) => TokenStore(ref.watch(secureStoreProvider)),
);

final serverStoreProvider = Provider<ServerStore>(
  (ref) => ServerStore(ref.watch(secureStoreProvider)),
);

/// Overridden in tests to answer without a network.
final probeClientFactoryProvider = Provider<ProbeClientFactory>(
  (ref) => createProbeClient,
);

final serverRepositoryProvider = Provider<ServerRepository>(
  (ref) => ServerRepository(ref.watch(probeClientFactoryProvider)),
);

/// Indirection so tests can assert which URL would have been opened without
/// launching a real browser.
typedef UrlLauncher = Future<bool> Function(Uri url);

final urlLauncherProvider = Provider<UrlLauncher>(
  (ref) => (Uri url) => launchUrl(url, mode: LaunchMode.externalApplication),
);

final dioProvider = Provider<Dio>((ref) {
  // Rebuilt when the server changes, so switching instances cannot leave a
  // client pointing at the old one. Empty until an address is chosen; the login
  // screen shows the address form in that state and issues no requests.
  final String baseUrl = ref.watch(
    serverControllerProvider.select((state) => state.baseUrl ?? ''),
  );
  final Dio dio = createApiClient(
    baseUrl: baseUrl,
    tokenStore: ref.watch(tokenStoreProvider),
    // A refresh token the server no longer honours ends the session; the
    // router reacts to the state change and shows the login screen.
    onSignedOut: () async =>
        ref.read(authControllerProvider.notifier).signOut(),
  );
  ref.onDispose(dio.close);
  return dio;
});

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(ref.watch(dioProvider)),
);

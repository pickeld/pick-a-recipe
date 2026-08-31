import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../config/app_config.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/auth_repository.dart';
import 'api_client.dart';
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

/// Indirection so tests can assert which URL would have been opened without
/// launching a real browser.
typedef UrlLauncher = Future<bool> Function(Uri url);

final urlLauncherProvider = Provider<UrlLauncher>(
  (ref) => (Uri url) => launchUrl(url, mode: LaunchMode.externalApplication),
);

final dioProvider = Provider<Dio>((ref) {
  return createApiClient(
    baseUrl: ref.watch(configProvider).baseUrl,
    tokenStore: ref.watch(tokenStoreProvider),
    // A refresh token the server no longer honours ends the session; the
    // router reacts to the state change and shows the login screen.
    onSignedOut: () async =>
        ref.read(authControllerProvider.notifier).signOut(),
  );
});

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(ref.watch(dioProvider)),
);

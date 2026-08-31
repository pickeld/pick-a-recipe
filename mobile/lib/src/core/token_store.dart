import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Access/refresh pair issued by the backend's mobile auth endpoints.
class AuthTokens {
  const AuthTokens({required this.accessToken, required this.refreshToken});

  final String accessToken;
  final String refreshToken;
}

/// Narrow contract over secure storage so tests can swap in an in-memory
/// implementation instead of driving a platform channel.
abstract interface class SecureKeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

/// Keystore-backed implementation.
///
/// The [AndroidOptions] defaults encrypt values with AES-GCM under a key
/// wrapped by RSA-OAEP in the Android keystore. The old
/// `encryptedSharedPreferences` flag is deprecated and ignored, so it is
/// deliberately not passed.
class KeystoreKeyValueStore implements SecureKeyValueStore {
  const KeystoreKeyValueStore();

  static const FlutterSecureStorage _storage = FlutterSecureStorage();

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

/// Holds the token pair between launches.
///
/// Never use shared preferences for these: they are readable by anything that
/// gains access to the app's data directory on a rooted device.
class TokenStore {
  const TokenStore(this._store);

  static const String _accessKey = 'auth.access_token';
  static const String _refreshKey = 'auth.refresh_token';

  final SecureKeyValueStore _store;

  /// Both halves are required — a lone access token cannot be renewed, and a
  /// lone refresh token means the pair was written only partially.
  Future<AuthTokens?> read() async {
    final String? access = await _store.read(_accessKey);
    final String? refresh = await _store.read(_refreshKey);
    if (access == null || access.isEmpty) return null;
    if (refresh == null || refresh.isEmpty) return null;
    return AuthTokens(accessToken: access, refreshToken: refresh);
  }

  Future<void> save(AuthTokens tokens) async {
    await _store.write(_accessKey, tokens.accessToken);
    await _store.write(_refreshKey, tokens.refreshToken);
  }

  Future<void> clear() async {
    await _store.delete(_accessKey);
    await _store.delete(_refreshKey);
  }
}

import 'token_store.dart';

/// Which instance this install talks to.
///
/// Held per install rather than compiled in: everyone running Pick-a-Recipe
/// runs their own server, so a published APK cannot know the address. The
/// compile-time value in [AppConfig] only prefills the field for dev builds.
///
/// Stored through [SecureKeyValueStore] for the same reason the tokens are: not
/// because an address is a secret, but because the tokens beside it are, and one
/// storage backend is one thing to get right.
class ServerStore {
  const ServerStore(this._store);

  static const String _key = 'server.base_url';

  final SecureKeyValueStore _store;

  Future<String?> read() async {
    final String? saved = await _store.read(_key);
    if (saved == null || saved.isEmpty) return null;
    return saved;
  }

  Future<void> save(String baseUrl) => _store.write(_key, baseUrl);

  Future<void> clear() => _store.delete(_key);
}

/// Turns what somebody types into a base URL, or returns null if it cannot be
/// one.
///
/// Deliberately forgiving about the parts people leave out — a bare
/// `recipes.example.com` is what most will type — and strict about the parts
/// that would silently break every later request, like a trailing slash that
/// turns into a double slash once a path is appended.
String? normaliseServerUrl(String input) {
  String text = input.trim();
  if (text.isEmpty) return null;

  // Whitespace inside means this is a sentence, not an address. Uri would
  // otherwise percent-encode it into a plausible-looking host that can never
  // resolve, and the failure would be blamed on the network.
  if (RegExp(r'\s').hasMatch(text)) return null;

  // No scheme means https, not "no scheme": defaulting to http would quietly
  // put a password on the wire in clear.
  if (!text.contains('://')) {
    text = 'https://$text';
  }

  final Uri? url = Uri.tryParse(text);
  if (url == null) return null;
  if (url.scheme != 'http' && url.scheme != 'https') return null;
  if (!_isPlausibleHost(url.host)) return null;

  // Rebuilt part by part rather than with replace(), which keeps a query and
  // fragment that would end up glued in front of every request path. Any
  // userinfo is dropped with them: credentials belong in the sign-in form.
  final String path = url.path.replaceAll(RegExp(r'/+$'), '');
  return Uri(
    scheme: url.scheme,
    host: url.host,
    port: url.hasPort ? url.port : null,
    path: path,
  ).toString();
}

final RegExp _hostname = RegExp(r'^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$');
final RegExp _ipv6 = RegExp(r'^[0-9A-Fa-f:.]+$');

bool _isPlausibleHost(String host) {
  if (host.isEmpty) return false;
  // Uri strips the brackets from an IPv6 literal, so it fails the hostname
  // pattern on its colons alone.
  return host.contains(':') ? _ipv6.hasMatch(host) : _hostname.hasMatch(host);
}

/// True for an address whose traffic is readable in transit.
///
/// Not blocked: plenty of self-hosted instances are plain HTTP on a home
/// network, and refusing would leave those users with no app at all. Surfaced
/// in the UI instead, so the choice is at least an informed one.
bool isInsecureServerUrl(String baseUrl) => baseUrl.startsWith('http://');

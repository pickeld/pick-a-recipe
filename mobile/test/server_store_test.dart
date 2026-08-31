import 'package:flutter_test/flutter_test.dart';
import 'package:pickarecipe/src/core/server_store.dart';

import 'support/fakes.dart';

void main() {
  group('normaliseServerUrl', () {
    test('assumes https for a bare host, rather than http', () {
      // Defaulting the other way would put a password on the wire in clear for
      // anyone who types the short form, which is most people.
      expect(normaliseServerUrl('recipes.example.com'),
          'https://recipes.example.com');
    });

    test('keeps an explicit scheme, including plain http', () {
      expect(normaliseServerUrl('http://192.168.1.10:5006'),
          'http://192.168.1.10:5006');
    });

    test('trims surrounding whitespace from a paste', () {
      expect(normaliseServerUrl('  https://recipes.example.com  '),
          'https://recipes.example.com');
    });

    test('drops a trailing slash that would double up on every path', () {
      expect(normaliseServerUrl('https://recipes.example.com/'),
          'https://recipes.example.com');
      expect(normaliseServerUrl('https://example.com/recipes///'),
          'https://example.com/recipes');
    });

    test('keeps a port and a subpath', () {
      expect(normaliseServerUrl('example.com:8443/recipes'),
          'https://example.com:8443/recipes');
    });

    test('discards a query and fragment, which a base URL cannot carry', () {
      expect(normaliseServerUrl('https://example.com/app?tab=1#top'),
          'https://example.com/app');
    });

    test('accepts an IPv6 literal, brackets and all', () {
      expect(normaliseServerUrl('http://[::1]:5006'), 'http://[::1]:5006');
    });

    test('drops credentials smuggled into the address', () {
      expect(normaliseServerUrl('https://bob:hunter2@example.com'),
          'https://example.com');
    });

    test('refuses input that is not a web address', () {
      expect(normaliseServerUrl(''), isNull);
      expect(normaliseServerUrl('   '), isNull);
      expect(normaliseServerUrl('ftp://example.com'), isNull);
      expect(normaliseServerUrl('javascript://example.com'), isNull);
      expect(normaliseServerUrl('https://'), isNull);
      expect(normaliseServerUrl('where are my recipes'), isNull);
    });
  });

  group('isInsecureServerUrl', () {
    test('flags http and not https', () {
      expect(isInsecureServerUrl('http://192.168.1.10:5006'), isTrue);
      expect(isInsecureServerUrl('https://recipes.example.com'), isFalse);
    });
  });

  group('ServerStore', () {
    test('round-trips the address and clears it', () async {
      final FakeSecureStore backing = FakeSecureStore();
      final ServerStore store = ServerStore(backing);

      expect(await store.read(), isNull);

      await store.save('https://recipes.example.com');
      expect(await store.read(), 'https://recipes.example.com');

      await store.clear();
      expect(await store.read(), isNull);
    });

    test('treats an empty stored value as none', () async {
      final FakeSecureStore backing = FakeSecureStore();
      backing.values['server.base_url'] = '';

      expect(await ServerStore(backing).read(), isNull);
    });
  });
}

import 'dart:async';

import 'package:app_links/app_links.dart';

/// Bridges incoming deep links to a callback.
///
/// Covers both entry points: [AppLinks.getInitialLink] for the link that cold
/// started the app, and the stream for links delivered while it is already
/// running (the usual case, since sign-in leaves the app in the background).
class DeepLinkService {
  DeepLinkService({AppLinks? appLinks}) : _appLinks = appLinks ?? AppLinks();

  final AppLinks _appLinks;
  StreamSubscription<Uri>? _subscription;

  Future<void> start(void Function(Uri uri) onLink) async {
    final Uri? initial = await _appLinks.getInitialLink();
    if (initial != null) {
      onLink(initial);
    }
    _subscription = _appLinks.uriLinkStream.listen(onLink);
  }

  Future<void> dispose() async {
    await _subscription?.cancel();
    _subscription = null;
  }
}

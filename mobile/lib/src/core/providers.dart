import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/app_config.dart';

/// Overridden with the real [AppConfig] in main() before runApp.
final configProvider = Provider<AppConfig>(
  (ref) => throw UnimplementedError(
    'configProvider must be overridden in main()',
  ),
);

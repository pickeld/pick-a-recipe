import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'src/app.dart';
import 'src/config/app_config.dart';
import 'src/core/providers.dart';

void main() {
  final config = AppConfig.fromDefines();

  runApp(
    ProviderScope(
      overrides: [configProvider.overrideWithValue(config)],
      child: const PickARecipeApp(),
    ),
  );
}

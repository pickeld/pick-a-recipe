import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/providers.dart';
import 'router.dart';
import 'theme/app_theme.dart';

class PickARecipeApp extends ConsumerWidget {
  const PickARecipeApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final config = ref.watch(configProvider);

    return MaterialApp.router(
      title: 'Pick-a-Recipe',
      debugShowCheckedModeBanner: !config.isProd,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      routerConfig: createRouter(),
    );
  }
}

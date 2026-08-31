import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/deep_links.dart';
import 'core/providers.dart';
import 'features/auth/auth_controller.dart';
import 'router.dart';
import 'theme/app_theme.dart';

class PickARecipeApp extends ConsumerStatefulWidget {
  const PickARecipeApp({super.key});

  @override
  ConsumerState<PickARecipeApp> createState() => _PickARecipeAppState();
}

class _PickARecipeAppState extends ConsumerState<PickARecipeApp> {
  final DeepLinkService _deepLinks = DeepLinkService();

  @override
  void initState() {
    super.initState();
    // Deferred so the first frame is not blocked on storage and network, and
    // so reading providers happens outside of initState's build phase.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final AuthController auth = ref.read(authControllerProvider.notifier);
      _deepLinks.start(auth.completeSignIn);
      auth.restore();
    });
  }

  @override
  void dispose() {
    _deepLinks.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final config = ref.watch(configProvider);

    return MaterialApp.router(
      title: 'Pick-a-Recipe',
      debugShowCheckedModeBanner: !config.isProd,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      routerConfig: ref.watch(routerProvider),
    );
  }
}

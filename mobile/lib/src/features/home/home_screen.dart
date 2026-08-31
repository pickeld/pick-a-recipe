import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_controller.dart';
import '../auth/auth_state.dart';

/// Signed-in landing screen. Recipe browsing lands here in a later wave; for
/// now it confirms the bearer token reached the backend.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AuthState auth = ref.watch(authControllerProvider);
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pick-a-Recipe'),
        actions: <Widget>[
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () =>
                ref.read(authControllerProvider.notifier).signOut(),
          ),
        ],
      ),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            Icon(
              Icons.check_circle_outline,
              size: 56,
              color: theme.colorScheme.primary,
            ),
            const SizedBox(height: 16),
            Text(
              'Signed in as ${auth.username ?? 'unknown'}',
              style: theme.textTheme.titleMedium,
            ),
            if (auth.isAdmin) ...<Widget>[
              const SizedBox(height: 4),
              Chip(
                label: const Text('Administrator'),
                visualDensity: VisualDensity.compact,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

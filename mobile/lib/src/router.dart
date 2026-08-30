import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

/// Placeholder screens — replaced by feature screens in later waves.
class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Pick-a-Recipe')),
      body: const Center(child: Text('Login placeholder')),
    );
  }
}

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Pick-a-Recipe')),
      body: const Center(child: Text('Home placeholder')),
    );
  }
}

GoRouter createRouter() {
  return GoRouter(
    initialLocation: '/home',
    routes: [
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginScreen(),
      ),
      GoRoute(
        path: '/home',
        builder: (context, state) => const HomeScreen(),
      ),
    ],
  );
}

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'features/auth/auth_controller.dart';
import 'features/auth/auth_state.dart';
import 'features/auth/login_screen.dart';
import 'features/home/home_screen.dart';

/// Shown while stored tokens are checked against the backend.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator()),
    );
  }
}

/// go_router only accepts a [Listenable], so auth changes are republished as
/// one. Subclassed because [ChangeNotifier.notifyListeners] is protected.
class _AuthRefreshNotifier extends ChangeNotifier {
  void ping() => notifyListeners();
}

final Provider<GoRouter> routerProvider = Provider<GoRouter>((Ref ref) {
  final _AuthRefreshNotifier refresh = _AuthRefreshNotifier();
  ref.listen<AuthState>(
    authControllerProvider,
    (AuthState? previous, AuthState next) {
      // Only status moves change routing; error and busy flags are rendered
      // in place and must not re-run redirects.
      if (previous?.status != next.status) {
        refresh.ping();
      }
    },
  );
  ref.onDispose(refresh.dispose);

  return GoRouter(
    initialLocation: '/',
    refreshListenable: refresh,
    redirect: (BuildContext context, GoRouterState state) {
      final AuthStatus status = ref.read(authControllerProvider).status;
      final String location = state.matchedLocation;

      return switch (status) {
        AuthStatus.checking => location == '/' ? null : '/',
        AuthStatus.signedOut => location == '/login' ? null : '/login',
        // Only bounce off the pre-auth routes, so screens added later stay
        // reachable without touching this switch.
        AuthStatus.signedIn =>
          location == '/' || location == '/login' ? '/home' : null,
      };
    },
    routes: <RouteBase>[
      GoRoute(
        path: '/',
        builder: (BuildContext context, GoRouterState state) =>
            const SplashScreen(),
      ),
      GoRoute(
        path: '/login',
        builder: (BuildContext context, GoRouterState state) =>
            const LoginScreen(),
      ),
      GoRoute(
        path: '/home',
        builder: (BuildContext context, GoRouterState state) =>
            const HomeScreen(),
      ),
    ],
  );
});

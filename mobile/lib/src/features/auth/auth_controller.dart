import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/token_store.dart';
import 'auth_callback.dart';
import 'auth_repository.dart';
import 'auth_state.dart';

/// Drives the sign-in handshake and owns the session state the router reads.
class AuthController extends Notifier<AuthState> {
  @override
  AuthState build() => const AuthState.checking();

  TokenStore get _tokens => ref.read(tokenStoreProvider);
  AuthRepository get _repository => ref.read(authRepositoryProvider);

  /// Called once at startup: a stored token pair is only trusted after the
  /// backend confirms it, so a revoked account does not linger in the UI.
  Future<void> restore() async {
    if (await _tokens.read() == null) {
      state = const AuthState.signedOut();
      return;
    }
    final MobileIdentity? identity = await _repository.me();
    if (identity == null) {
      await _tokens.clear();
      state = const AuthState.signedOut();
      return;
    }
    state = AuthState(
      status: AuthStatus.signedIn,
      username: identity.username,
      isAdmin: identity.isAdmin,
    );
  }

  /// Opens the system browser at Authentik. Control returns to the app through
  /// the deep link handled by [completeSignIn].
  Future<void> signIn() async {
    if (state.isBusy) return;
    state = state.copyWith(isBusy: true);

    try {
      final Uri url = await _repository.loginUrl(redirectUri: kAuthCallbackUri);
      // An external browser, not a webview: it carries any existing Authentik
      // session cookie and keeps app code away from the credential entry.
      final bool launched = await ref.read(urlLauncherProvider)(url);
      if (!launched) {
        state = state.copyWith(
          isBusy: false,
          errorMessage: 'No browser available to complete sign-in.',
        );
        return;
      }
      // isBusy stays true: the browser is now in front of the user and the
      // deep link is what resolves it.
    } on AuthApiException catch (error) {
      state = state.copyWith(isBusy: false, errorMessage: error.message);
    }
  }

  /// Handles the `par://auth/callback` deep link. Ignores links that are not
  /// ours so other deep links keep working.
  Future<void> completeSignIn(Uri uri) async {
    final AuthCallback? callback = parseAuthCallback(uri);
    if (callback == null) return;

    switch (callback) {
      case AuthCallbackFailure(:final String message):
        state = AuthState.signedOut(errorMessage: message);
      case AuthCallbackSuccess(:final AuthTokens tokens):
        await _tokens.save(tokens);
        final MobileIdentity? identity = await _repository.me();
        if (identity == null) {
          await _tokens.clear();
          state = const AuthState.signedOut(
            errorMessage: 'Signed in, but the server rejected the session. '
                'Please try again.',
          );
          return;
        }
        state = AuthState(
          status: AuthStatus.signedIn,
          username: identity.username,
          isAdmin: identity.isAdmin,
        );
    }
  }

  Future<void> signOut() async {
    await _tokens.clear();
    state = const AuthState.signedOut();
  }

  void dismissError() {
    if (state.errorMessage == null) return;
    state = state.copyWith();
  }
}

final NotifierProvider<AuthController, AuthState> authControllerProvider =
    NotifierProvider<AuthController, AuthState>(AuthController.new);

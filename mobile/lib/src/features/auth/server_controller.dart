import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/server_store.dart';
import 'auth_repository.dart';
import 'server_repository.dart';
import 'server_state.dart';

/// Owns the server address: reads the saved one at startup, verifies a new one
/// before saving it, and forgets it on request.
class ServerController extends Notifier<ServerState> {
  @override
  ServerState build() => ServerState(
        suggestedUrl: ref.read(configProvider).suggestedServerUrl,
      );

  ServerStore get _store => ref.read(serverStoreProvider);
  ServerRepository get _servers => ref.read(serverRepositoryProvider);

  /// Loads the saved address and asks it how to sign in.
  ///
  /// Returns the address if one is saved, whether or not it answered: an
  /// instance that is merely down should not make the app forget where it is.
  Future<String?> restore() async {
    final String? saved = await _store.read();
    if (saved == null) {
      state = state.copyWith(isBusy: false);
      return null;
    }

    state = ServerState(
      baseUrl: saved,
      isBusy: true,
      suggestedUrl: state.suggestedUrl,
    );
    try {
      final ServerAuthStatus status = await _servers.statusOf(saved);
      state = ServerState(
        baseUrl: saved,
        status: status,
        suggestedUrl: state.suggestedUrl,
      );
    } on AuthApiException catch (error) {
      state = ServerState(
        baseUrl: saved,
        isBusy: false,
        errorMessage: error.message,
        suggestedUrl: state.suggestedUrl,
      );
    }
    return saved;
  }

  /// Verifies what the user typed, then keeps it.
  ///
  /// Nothing is saved until the address answers, so a typo does not leave the
  /// app pointing somewhere it can never reach.
  Future<bool> connect(String input) async {
    if (state.isBusy) return false;

    final String? candidate = normaliseServerUrl(input);
    if (candidate == null) {
      state = state.copyWith(
        errorMessage: 'That does not look like a web address. Something like '
            'recipes.example.com, or http://192.168.1.10:5006.',
      );
      return false;
    }

    state = state.copyWith(isBusy: true);
    try {
      final ServerAuthStatus status = await _servers.statusOf(candidate);
      await _store.save(candidate);
      state = ServerState(
        baseUrl: candidate,
        status: status,
        suggestedUrl: state.suggestedUrl,
      );
      return true;
    } on AuthApiException catch (error) {
      state = state.copyWith(isBusy: false, errorMessage: error.message);
      return false;
    }
  }

  /// Re-asks the current server, for the retry button after it was unreachable.
  Future<void> recheck() async {
    final String? current = state.baseUrl;
    if (current == null || state.isBusy) return;

    state = state.copyWith(isBusy: true);
    try {
      final ServerAuthStatus status = await _servers.statusOf(current);
      state = ServerState(
        baseUrl: current,
        status: status,
        suggestedUrl: state.suggestedUrl,
      );
    } on AuthApiException catch (error) {
      state = state.copyWith(isBusy: false, errorMessage: error.message);
    }
  }

  /// Forgets the address so another can be entered.
  ///
  /// Tokens go with it: they were issued by the old server and mean nothing to
  /// a different one, and leaving them behind would attach a stranger's bearer
  /// token to the next instance's requests.
  Future<void> forget() async {
    await _store.clear();
    await ref.read(tokenStoreProvider).clear();
    state = ServerState(suggestedUrl: state.suggestedUrl);
  }

  void dismissError() {
    if (state.errorMessage == null) return;
    state = state.copyWith();
  }
}

final NotifierProvider<ServerController, ServerState> serverControllerProvider =
    NotifierProvider<ServerController, ServerState>(ServerController.new);

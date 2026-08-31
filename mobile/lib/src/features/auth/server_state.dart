import 'auth_repository.dart';

/// Which instance the app points at, and what it said about signing in.
///
/// Kept apart from AuthState because the two answer different questions and
/// change at different times: this one survives sign-out, since the address is
/// still right after a session ends.
class ServerState {
  const ServerState({
    this.baseUrl,
    this.status,
    this.isBusy = false,
    this.errorMessage,
    this.suggestedUrl,
  });

  /// Normalised base URL, or null when none has been chosen yet.
  final String? baseUrl;

  /// Null until the address has been reached — including when [baseUrl] is set
  /// but the server was unreachable on this launch.
  final ServerAuthStatus? status;

  final bool isBusy;
  final String? errorMessage;

  /// Prefills the address field. Only set for dev builds.
  final String? suggestedUrl;

  /// True once there is an address that answered, which is the only state where
  /// a sign-in form can be shown.
  bool get isReady => baseUrl != null && status != null;

  ServerState copyWith({
    String? baseUrl,
    ServerAuthStatus? status,
    bool? isBusy,
    String? errorMessage,
    String? suggestedUrl,
  }) {
    return ServerState(
      baseUrl: baseUrl ?? this.baseUrl,
      status: status ?? this.status,
      isBusy: isBusy ?? this.isBusy,
      // Passing null clears the message, which copyWith's usual ?? idiom
      // cannot express.
      errorMessage: errorMessage,
      suggestedUrl: suggestedUrl ?? this.suggestedUrl,
    );
  }
}

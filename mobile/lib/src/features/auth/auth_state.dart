enum AuthStatus {
  /// Stored tokens are being read and validated; shown as a splash.
  checking,
  signedOut,
  signedIn,
}

class AuthState {
  const AuthState({
    required this.status,
    this.username,
    this.isAdmin = false,
    this.isBusy = false,
    this.errorMessage,
  });

  const AuthState.checking() : this(status: AuthStatus.checking);

  const AuthState.signedOut({String? errorMessage})
      : this(status: AuthStatus.signedOut, errorMessage: errorMessage);

  final AuthStatus status;
  final String? username;
  final bool isAdmin;

  /// True while the browser handshake is in flight, to keep the sign-in button
  /// from being tapped twice and burning two nonces.
  final bool isBusy;
  final String? errorMessage;

  AuthState copyWith({
    AuthStatus? status,
    String? username,
    bool? isAdmin,
    bool? isBusy,
    String? errorMessage,
  }) {
    return AuthState(
      status: status ?? this.status,
      username: username ?? this.username,
      isAdmin: isAdmin ?? this.isAdmin,
      isBusy: isBusy ?? this.isBusy,
      // Passing null clears the message, which copyWith's usual ?? idiom
      // cannot express.
      errorMessage: errorMessage,
    );
  }
}

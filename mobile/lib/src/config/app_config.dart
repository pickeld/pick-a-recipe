/// Compile-time application configuration.
///
/// Values are injected via --dart-define at build time:
///
///   flutter run  --dart-define=FLAVOR=dev
///   flutter build apk --release --dart-define=FLAVOR=prod
///
/// The server address is *not* compiled in. Every user runs their own instance,
/// so a published build cannot know where to point; it is entered in the app and
/// kept by ServerStore. API_BASE_URL only prefills that field, to save typing
/// during development.
class AppConfig {
  const AppConfig({required this.flavor, this.apiBaseUrlOverride});

  // Android emulator alias for the host machine's loopback interface; the
  // Flask backend listens on port 5006 in local development.
  static const String _devHost = 'http://10.0.2.2:5006';

  /// Either 'dev' or 'prod'.
  final String flavor;

  /// Optional compile-time prefill of the server address.
  final String? apiBaseUrlOverride;

  bool get isProd => flavor == 'prod';

  /// Prefills the server address field on first launch. Null for release
  /// builds: guessing there would point somebody else's install at whichever
  /// instance happened to be convenient when the APK was cut.
  String? get suggestedServerUrl =>
      apiBaseUrlOverride ?? (isProd ? null : _devHost);

  factory AppConfig.fromDefines() {
    const flavor = String.fromEnvironment('FLAVOR', defaultValue: 'dev');
    const apiBaseUrl = String.fromEnvironment('API_BASE_URL');
    return AppConfig(
      flavor: flavor,
      apiBaseUrlOverride: apiBaseUrl.isEmpty ? null : apiBaseUrl,
    );
  }
}

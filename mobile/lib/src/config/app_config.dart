/// Compile-time application configuration.
///
/// Values are injected via --dart-define at build time:
///
///   flutter run  --dart-define=FLAVOR=dev
///   flutter build apk --release --dart-define=FLAVOR=prod
///
/// API_BASE_URL optionally overrides the host for either flavor without a
/// code change (useful for pointing a dev build at staging).
class AppConfig {
  const AppConfig({required this.flavor, this.apiBaseUrlOverride});

  static const String _prodHost = 'https://recipes.pickel.me';
  // Android emulator alias for the host machine's loopback interface; the
  // Flask backend listens on port 5006 in local development.
  static const String _devHost = 'http://10.0.2.2:5006';

  /// Either 'dev' or 'prod'.
  final String flavor;

  /// Optional compile-time override of the API base URL.
  final String? apiBaseUrlOverride;

  bool get isProd => flavor == 'prod';

  /// Base URL of the pick-a-recipe backend, no trailing slash.
  String get baseUrl =>
      apiBaseUrlOverride ?? (isProd ? _prodHost : _devHost);

  factory AppConfig.fromDefines() {
    const flavor = String.fromEnvironment('FLAVOR', defaultValue: 'dev');
    const apiBaseUrl = String.fromEnvironment('API_BASE_URL');
    return AppConfig(
      flavor: flavor,
      apiBaseUrlOverride: apiBaseUrl.isEmpty ? null : apiBaseUrl,
    );
  }
}

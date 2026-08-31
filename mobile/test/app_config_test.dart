import 'package:flutter_test/flutter_test.dart';

import 'package:pickarecipe/src/config/app_config.dart';

void main() {
  test('release builds suggest no server, since they cannot know one', () {
    const config = AppConfig(flavor: 'prod');
    expect(config.suggestedServerUrl, isNull);
    expect(config.isProd, isTrue);
  });

  test('dev builds prefill the emulator loopback alias', () {
    const config = AppConfig(flavor: 'dev');
    expect(config.suggestedServerUrl, 'http://10.0.2.2:5006');
    expect(config.isProd, isFalse);
  });

  test('an explicit override prefills either flavor', () {
    const config = AppConfig(
      flavor: 'prod',
      apiBaseUrlOverride: 'https://staging.example.com',
    );
    expect(config.suggestedServerUrl, 'https://staging.example.com');
  });
}

import 'package:flutter_test/flutter_test.dart';

import 'package:pickarecipe/src/config/app_config.dart';

void main() {
  test('prod flavor resolves to the production host', () {
    const config = AppConfig(flavor: 'prod');
    expect(config.baseUrl, 'https://recipes.pickel.me');
    expect(config.isProd, isTrue);
  });

  test('dev flavor resolves to the emulator loopback alias', () {
    const config = AppConfig(flavor: 'dev');
    expect(config.baseUrl, 'http://10.0.2.2:5006');
    expect(config.isProd, isFalse);
  });

  test('explicit override wins over flavor defaults', () {
    const config = AppConfig(
      flavor: 'prod',
      apiBaseUrlOverride: 'https://staging.example.com',
    );
    expect(config.baseUrl, 'https://staging.example.com');
  });
}

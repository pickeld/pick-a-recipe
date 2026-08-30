import 'package:flutter/material.dart';

/// Material 3 themes seeded from the Pick-a-Recipe brand green.
abstract final class AppTheme {
  static const Color _seed = Color(0xFF388E3C);

  static ThemeData light() => ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: _seed),
      );

  static ThemeData dark() => ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: _seed,
          brightness: Brightness.dark,
        ),
      );
}

import 'package:flutter/material.dart';

/// Gunther's mobile theme, mirroring the desktop design system: white paper,
/// black ink for actions, and the brand green kept for identity and status.
abstract final class AppTheme {
  static const brand = Color(0xFF16745C);
  static const canvas = Color(0xFFFFFFFF);
  static const surfaceSoft = Color(0xFFFAFAF8);
  static const hover = Color(0xFFF3F3F1);
  static const active = Color(0xFFECECEA);
  static const ink = Color(0xFF1A1A18);
  static const muted = Color(0xFF5F5E5A);
  static const line = Color(0x211A1A18);
  static const lineStrong = Color(0x3D1A1A18);

  static ThemeData get light {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: brand,
      brightness: Brightness.light,
    ).copyWith(
      primary: ink,
      onPrimary: Colors.white,
      primaryContainer: active,
      onPrimaryContainer: ink,
      secondary: brand,
      onSecondary: Colors.white,
      secondaryContainer: hover,
      onSecondaryContainer: ink,
      tertiary: brand,
      onTertiary: Colors.white,
      surface: canvas,
      onSurface: ink,
      onSurfaceVariant: muted,
      surfaceContainerLowest: canvas,
      surfaceContainerLow: surfaceSoft,
      surfaceContainer: surfaceSoft,
      surfaceContainerHigh: hover,
      surfaceContainerHighest: active,
      surfaceTint: Colors.transparent,
      outline: lineStrong,
      outlineVariant: line,
    );
    return ThemeData(
      colorScheme: colorScheme,
      scaffoldBackgroundColor: canvas,
      useMaterial3: true,
      appBarTheme: const AppBarTheme(
        backgroundColor: canvas,
        foregroundColor: ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        centerTitle: false,
      ),
      navigationBarTheme: const NavigationBarThemeData(
        backgroundColor: surfaceSoft,
        indicatorColor: active,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        height: 72,
      ),
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        backgroundColor: ink,
        foregroundColor: Colors.white,
      ),
      cardTheme: CardThemeData(
        color: canvas,
        elevation: 0,
        margin: EdgeInsets.zero,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: line),
        ),
      ),
      dividerTheme: const DividerThemeData(color: line, thickness: 1),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surfaceSoft,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: ink, width: 1.4),
        ),
      ),
    );
  }
}

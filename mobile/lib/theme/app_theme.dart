/// Generated theme from shared/design-tokens/tokens.json
/// DO NOT EDIT - regenerate from tokens.json

import 'package:flutter/material.dart';

class AppTheme {
  // Colors
  static const Color bg = Color(0xFF0e1117);
  static const Color bgElevated = Color(0xFF1a1f2e);
  static const Color bgCard = Color(0xFF151922);
  static const Color border = Color(0xFF2a3142);
  static const Color text = Color(0xFFf8fafc);
  static const Color textMuted = Color(0xFF94a3b8);
  static const Color yes = Color(0xFF00c853);
  static const Color no = Color(0xFFff5252);
  static const Color accent = Color(0xFF3b82f6);
  static const Color warning = Color(0xFFf59e0b);

  // Spacing
  static const double spacingXs = 4.0;
  static const double spacingSm = 8.0;
  static const double spacingMd = 16.0;
  static const double spacingLg = 24.0;
  static const double spacingXl = 40.0;

  // Typography
  static const String fontFamily = 'Inter';
  static const double sizeSm = 12.0;
  static const double sizeMd = 14.0;
  static const double sizeLg = 18.0;
  static const double sizeXl = 28.0;
  static const FontWeight weightRegular = FontWeight.w400;
  static const FontWeight weightMedium = FontWeight.w500;
  static const FontWeight weightBold = FontWeight.w700;

  // Radius
  static const double radiusSm = 6.0;
  static const double radiusMd = 12.0;
  static const double radiusLg = 20.0;

  static ThemeData get theme => ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: bg,
        primaryColor: accent,
        fontFamily: fontFamily,
        colorScheme: ColorScheme.dark(
          primary: accent,
          secondary: accent,
          background: bg,
          surface: bgCard,
          error: no,
        ),
        cardTheme: CardThemeData(
          color: bgCard,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(radiusMd),
            side: BorderSide(color: border),
          ),
        ),
        textTheme: TextTheme(
          bodyLarge: TextStyle(color: text, fontSize: sizeLg),
          bodyMedium: TextStyle(color: text, fontSize: sizeMd),
          bodySmall: TextStyle(color: textMuted, fontSize: sizeSm),
          titleLarge: TextStyle(
            color: text,
            fontSize: sizeXl,
            fontWeight: weightBold,
          ),
        ),
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            backgroundColor: accent,
            foregroundColor: text,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(radiusMd),
            ),
            padding: EdgeInsets.symmetric(
              horizontal: spacingLg,
              vertical: spacingMd,
            ),
          ),
        ),
      );
}

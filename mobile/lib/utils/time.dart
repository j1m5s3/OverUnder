import 'package:intl/intl.dart';

final DateFormat _unixFormat = DateFormat('MMM d, y h:mm a');

/// Formats unix seconds in the device's local time, e.g. "Sep 23, 2026 8:15 PM".
String formatUnixSeconds(int seconds) =>
    _unixFormat.format(DateTime.fromMillisecondsSinceEpoch(seconds * 1000));

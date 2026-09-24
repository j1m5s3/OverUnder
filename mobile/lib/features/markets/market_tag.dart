import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../theme/app_theme.dart';

/// Small tinted label used on market cards and the detail header.
class MarketTag extends StatelessWidget {
  final String label;
  final Color color;

  const MarketTag({super.key, required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingSm,
        vertical: AppTheme.spacingXs,
      ),
      decoration: BoxDecoration(
        color: color.withAlpha(51),
        borderRadius: BorderRadius.circular(AppTheme.radiusSm),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: AppTheme.sizeSm,
        ),
      ),
    );
  }
}

/// Tags for a market's type and trading state at [now], in display order.
List<Widget> marketTags(Market market, DateTime now) => [
      if (market.isWildcard) const MarketTag(label: 'Wildcard', color: AppTheme.accent),
      if (market.isUserListed) const MarketTag(label: 'User market', color: AppTheme.accent),
      if (market.resolved)
        const MarketTag(label: 'Resolved', color: AppTheme.yes)
      else if (market.isTradingClosed(now))
        const MarketTag(label: 'Closed', color: AppTheme.warning),
    ];

import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_client.dart';
import '../../theme/app_theme.dart';

class OracleStatusWidget extends StatefulWidget {
  final ApiClient apiClient;
  final String marketId;

  const OracleStatusWidget({
    super.key,
    required this.apiClient,
    required this.marketId,
  });

  @override
  State<OracleStatusWidget> createState() => _OracleStatusWidgetState();
}

class _OracleStatusWidgetState extends State<OracleStatusWidget> {
  OracleStatus? _status;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadStatus();
  }

  Future<void> _loadStatus() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final status = await widget.apiClient.getOracleStatus(widget.marketId);
      setState(() {
        _status = status;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingMd),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Oracle Status',
                  style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                        fontWeight: AppTheme.weightBold,
                      ),
                ),
                if (_loading)
                  const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
              ],
            ),
            const SizedBox(height: AppTheme.spacingMd),
            if (_error != null)
              Text('Error: $_error', style: TextStyle(color: AppTheme.no))
            else if (_status != null)
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Unanimity Status
                  Container(
                    padding: const EdgeInsets.all(AppTheme.spacingMd),
                    decoration: BoxDecoration(
                      color: _status!.unanimous
                          ? AppTheme.yes.withOpacity(0.2)
                          : AppTheme.warning.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(AppTheme.radiusMd),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _status!.unanimous ? Icons.check_circle : Icons.pending,
                          color: _status!.unanimous ? AppTheme.yes : AppTheme.warning,
                        ),
                        const SizedBox(width: AppTheme.spacingSm),
                        Text(
                          _status!.unanimous ? 'Unanimous' : 'No Consensus',
                          style: TextStyle(
                            color: _status!.unanimous ? AppTheme.yes : AppTheme.warning,
                            fontWeight: AppTheme.weightBold,
                          ),
                        ),
                        if (_status!.outcome != null) ...[
                          const Spacer(),
                          Text(
                            'Outcome: ${_status!.outcome == 0 ? "YES" : "NO"}',
                            style: const TextStyle(fontWeight: AppTheme.weightBold),
                          ),
                        ],
                      ],
                    ),
                  ),
                  if (_status!.attestations.isNotEmpty) ...[
                    const SizedBox(height: AppTheme.spacingMd),
                    Text(
                      'Agent Attestations',
                      style: TextStyle(
                        color: AppTheme.textMuted,
                        fontSize: AppTheme.sizeSm,
                      ),
                    ),
                    const SizedBox(height: AppTheme.spacingSm),
                    ..._status!.attestations.map((attestation) => Padding(
                          padding: const EdgeInsets.only(bottom: AppTheme.spacingSm),
                          child: Container(
                            padding: const EdgeInsets.all(AppTheme.spacingSm),
                            decoration: BoxDecoration(
                              color: AppTheme.bgElevated,
                              borderRadius: BorderRadius.circular(AppTheme.radiusSm),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Text(
                                      attestation.agent.toUpperCase(),
                                      style: const TextStyle(
                                        fontWeight: AppTheme.weightBold,
                                        fontSize: AppTheme.sizeSm,
                                      ),
                                    ),
                                    const Spacer(),
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: AppTheme.spacingXs,
                                        vertical: 2,
                                      ),
                                      decoration: BoxDecoration(
                                        color: attestation.outcome == 0
                                            ? AppTheme.yes.withOpacity(0.2)
                                            : AppTheme.no.withOpacity(0.2),
                                        borderRadius: BorderRadius.circular(AppTheme.radiusSm),
                                      ),
                                      child: Text(
                                        attestation.outcome == 0 ? 'YES' : 'NO',
                                        style: TextStyle(
                                          color: attestation.outcome == 0 ? AppTheme.yes : AppTheme.no,
                                          fontSize: AppTheme.sizeSm,
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                                if (attestation.summary.isNotEmpty) ...[
                                  const SizedBox(height: AppTheme.spacingXs),
                                  Text(
                                    attestation.summary,
                                    style: TextStyle(
                                      color: AppTheme.textMuted,
                                      fontSize: AppTheme.sizeSm,
                                    ),
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ],
                              ],
                            ),
                          ),
                        )),
                  ],
                ],
              )
            else
              Text(
                'No oracle data available',
                style: TextStyle(color: AppTheme.textMuted),
              ),
          ],
        ),
      ),
    );
  }
}

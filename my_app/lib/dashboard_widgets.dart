// Dashboard sections: vibration bars, temperature, test timer and the overall
// result. "Healthy" always means the healthy pump's recorded data.

import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'live_data.dart';

const kHealthy = Color(0xFF16A34A);
const kMonitor = Color(0xFF0891B2);
const kWarning = Color(0xFFD97706);
const kFaulty = Color(0xFFDC2626);
const kOff = Color(0xFF94A3B8);
const kInk = Color(0xFF102A43);
const kMuted = Color(0xFF64748B);
const kTrack = Color(0xFFF1F5F9);
const kReference = Color(0xFF0F766E);

/// Colour of an axis status (healthy/warning/faulty/off) or verdict.
Color levelColor(String status) {
  switch (status.toUpperCase()) {
    case 'HEALTHY':
      return kHealthy;
    case 'MONITOR':
      return kMonitor;
    case 'WARNING':
      return kWarning;
    case 'FAULTY':
      return kFaulty;
    default:
      return kOff;
  }
}

/// Colour of a risk 0-100, in the backend's verdict bands
/// (healthy < 30 <= monitor < 60 <= warning < 90 <= faulty).
Color riskColor(double? risk) {
  if (risk == null) return kInk;
  if (risk >= 90) return kFaulty;
  if (risk >= 60) return kWarning;
  if (risk >= 30) return kMonitor;
  return kHealthy;
}

String levelLabel(String status) {
  switch (status.toUpperCase()) {
    case 'HEALTHY':
      return 'Healthy';
    case 'MONITOR':
      return 'Monitor';
    case 'WARNING':
      return 'Warning';
    case 'FAULTY':
      return 'Faulty';
    case 'NO_DATA':
      return 'No data';
    default:
      return 'Pump off';
  }
}

IconData verdictIcon(String verdict) {
  switch (verdict.toUpperCase()) {
    case 'HEALTHY':
      return Icons.check_circle;
    case 'MONITOR':
      return Icons.visibility;
    case 'WARNING':
      return Icons.warning_amber_rounded;
    case 'FAULTY':
      return Icons.dangerous;
    default:
      return Icons.hourglass_empty;
  }
}

String formatPercent(double? value, {bool signed = false}) {
  if (value == null) return '—';
  final text = value.abs() >= 10
      ? value.toStringAsFixed(0)
      : value.toStringAsFixed(1);
  return signed && value > 0 ? '+$text%' : '$text%';
}

String formatDuration(double seconds) {
  final total = seconds.round().clamp(0, 999999);
  final hours = total ~/ 3600;
  final minutes = (total % 3600) ~/ 60;
  final secs = total % 60;
  final mm = minutes.toString().padLeft(2, '0');
  final ss = secs.toString().padLeft(2, '0');
  return hours > 0 ? '$hours:$mm:$ss' : '$mm:$ss';
}

class SectionCard extends StatelessWidget {
  const SectionCard({
    super.key,
    required this.icon,
    required this.title,
    required this.child,
    this.trailing,
  });

  final IconData icon;
  final String title;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: kReference, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
                ?trailing,
              ],
            ),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withAlpha(28),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withAlpha(110)),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w800,
          fontSize: 12,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Pump sensors: switch between the healthy and the faulty pump
// ---------------------------------------------------------------------------

/// Pump sensors: one switch between the healthy and the faulty pump (the
/// dashboard shows the one selected) and whether that pump's sensors are
/// online (its ESP32 is sending data).
class PumpSwitchCard extends StatelessWidget {
  const PumpSwitchCard({
    super.key,
    required this.selectedDeviceId,
    required this.online,
    required this.onSelect,
    this.healthyPumpId = kHealthyPumpId,
    this.faultyPumpId = kFaultyPumpId,
  });

  final String selectedDeviceId;

  /// The selected pump's sensors are online.
  final bool online;
  final ValueChanged<String> onSelect;
  final String healthyPumpId;
  final String faultyPumpId;

  @override
  Widget build(BuildContext context) {
    final faulty = selectedDeviceId == faultyPumpId;
    final healthy = selectedDeviceId == healthyPumpId;
    TextStyle side(bool active, Color color) => TextStyle(
      fontWeight: FontWeight.w800,
      fontSize: 14,
      color: active ? color : kMuted,
    );
    return SectionCard(
      icon: Icons.sensors,
      title: 'Pump sensors',
      trailing: StatusChip(
        label: online ? 'Online' : 'Offline',
        color: online ? kHealthy : kOff,
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Flexible(
            child: InkWell(
              onTap: () => onSelect(healthyPumpId),
              child: Text(
                'Healthy pump',
                textAlign: TextAlign.end,
                style: side(healthy, kHealthy),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Switch(
              value: faulty,
              activeThumbColor: Colors.white,
              activeTrackColor: kFaulty,
              inactiveThumbColor: Colors.white,
              inactiveTrackColor: kHealthy,
              trackOutlineColor: const WidgetStatePropertyAll(
                Colors.transparent,
              ),
              onChanged: (toFaulty) =>
                  onSelect(toFaulty ? faultyPumpId : healthyPumpId),
            ),
          ),
          Flexible(
            child: InkWell(
              onTap: () => onSelect(faultyPumpId),
              child: Text('Faulty pump', style: side(faulty, kFaulty)),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Vibration: X, Y, Z as vertical bars, 0-5 g
// ---------------------------------------------------------------------------

const double kBarMaxG = 5.0;
const double kBarHeight = 210;

class VibrationBarsCard extends StatelessWidget {
  const VibrationBarsCard({
    super.key,
    required this.axes,
    required this.pumpRunning,
    required this.online,
    required this.overall,
    required this.liveTotalRmsG,
    required this.healthyTotalRmsG,
  });

  final List<AxisLevel> axes;
  final bool pumpRunning;
  final bool online;
  final Assessment overall;
  final double? liveTotalRmsG;
  final double? healthyTotalRmsG;

  @override
  Widget build(BuildContext context) {
    final pumpState = !online
        ? 'Sensor offline'
        : (pumpRunning ? 'Pump running' : 'Pump off');
    final judged = overall.verdict != 'NO_DATA';
    final pumpStateColor = !online || !pumpRunning
        ? kOff
        : (judged ? levelColor(overall.verdict) : kReference);
    final pumpStatusLabel = !online || !pumpRunning || !judged
        ? pumpState
        : '$pumpState · ${levelLabel(overall.verdict)}';
    // The bars show the ADXL345's live values whenever the ESP32 is online,
    // pump running or not (grey while it is off).
    return SectionCard(
      icon: Icons.vibration,
      title: 'Vibration (X, Y, Z)',
      trailing: StatusChip(label: pumpStatusLabel, color: pumpStateColor),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: kBarHeight + 22,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SizedBox(
                  width: 34,
                  height: kBarHeight + 22,
                  child: CustomPaint(painter: _ScalePainter()),
                ),
                for (final axis in axes)
                  Expanded(
                    child: SizedBox(
                      height: kBarHeight + 22,
                      child: CustomPaint(
                        painter: _BarPainter(
                          value: online ? axis.rmsG : null,
                          healthy: axis.healthyRmsG,
                          color: levelColor(axis.status),
                          emptyLabel: 'no data',
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(width: 34),
              for (final axis in axes)
                Expanded(
                  child: _AxisCaption(axis: axis, active: online),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _AxisCaption extends StatelessWidget {
  const _AxisCaption({required this.axis, required this.active});

  final AxisLevel axis;

  /// The ESP32 is online.
  final bool active;

  @override
  Widget build(BuildContext context) {
    final rms = active ? axis.rmsG : null;
    final hasLevel = rms != null;
    final color = hasLevel ? levelColor(axis.status) : kOff;
    // Small values (pump off) with one more digit, so they still move.
    final value = rms == null
        ? '— g'
        : '${rms.toStringAsFixed(rms < 0.1 ? 3 : 2)} g';
    final healthy = axis.healthyRmsG == null
        ? ''
        : 'Healthy ${axis.healthyRmsG!.toStringAsFixed(2)} g';
    final times = hasLevel && axis.timesHealthy != null
        ? ' · ${axis.timesHealthy!.toStringAsFixed(1)}×'
        : '';
    return Column(
      children: [
        Text(
          '${axis.axis} axis',
          style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 13),
        ),
        Text(
          value,
          style: TextStyle(
            color: color,
            fontWeight: FontWeight.w800,
            fontSize: 16,
          ),
        ),
        const SizedBox(height: 3),
        StatusChip(
          label: hasLevel ? levelLabel(axis.status) : 'No data',
          color: color,
        ),
        const SizedBox(height: 4),
        Text(
          '$healthy$times',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 10.5, color: kMuted),
        ),
      ],
    );
  }
}

TextPainter _text(String text, TextStyle style) => TextPainter(
  text: TextSpan(text: text, style: style),
  textDirection: TextDirection.ltr,
)..layout();

/// The scale left of the bars, one label per grid line from the bottom up
/// (the top 22 px hold the value labels): 0-5 g for vibration.
class _ScalePainter extends CustomPainter {
  const _ScalePainter({
    this.labels = const ['0 g', '1 g', '2 g', '3 g', '4 g', '5 g'],
  });

  final List<String> labels;

  @override
  void paint(Canvas canvas, Size size) {
    const top = 22.0;
    final height = size.height - top;
    final steps = labels.length - 1;
    for (var i = 0; i <= steps; i++) {
      final y = top + height - height * i / steps;
      final label = _text(
        labels[i],
        const TextStyle(
          color: kMuted,
          fontSize: 10,
          fontWeight: FontWeight.w600,
        ),
      );
      label.paint(
        canvas,
        Offset(size.width - label.width - 6, y - label.height / 2),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _ScalePainter oldDelegate) =>
      oldDelegate.labels != labels;
}

class _BarPainter extends CustomPainter {
  _BarPainter({
    required this.value,
    required this.healthy,
    required this.color,
    required this.emptyLabel,
  });

  final double? value;
  final double? healthy;
  final Color color;
  final String emptyLabel;

  @override
  void paint(Canvas canvas, Size size) {
    const top = 22.0;
    final height = size.height - top;
    final barWidth = (size.width * 0.5).clamp(18.0, 64.0);
    final left = (size.width - barWidth) / 2;
    double yFor(double g) =>
        top + height - height * (g / kBarMaxG).clamp(0.0, 1.0);

    // Grid lines every 1 g, matching the scale.
    final grid = Paint()
      ..color = const Color(0xFFE2E8F0)
      ..strokeWidth = 1;
    for (var g = 0; g <= kBarMaxG; g++) {
      canvas.drawLine(
        Offset(0, yFor(g.toDouble())),
        Offset(size.width, yFor(g.toDouble())),
        grid,
      );
    }

    final track = RRect.fromRectAndRadius(
      Rect.fromLTWH(left, top, barWidth, height),
      const Radius.circular(6),
    );
    canvas.drawRRect(track, Paint()..color = kTrack.withAlpha(200));

    final v = value;
    if (v != null) {
      final fillTop = yFor(v);
      final fillHeight = (top + height - fillTop).clamp(
        v > 0 ? 3.0 : 0.0,
        height,
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(left, top + height - fillHeight, barWidth, fillHeight),
          const Radius.circular(6),
        ),
        Paint()..color = color,
      );
      final percent = v / kBarMaxG * 100;
      final label = _text(
        '${percent < 10 ? percent.toStringAsFixed(1) : percent.toStringAsFixed(0)}%',
        TextStyle(color: color, fontSize: 13, fontWeight: FontWeight.w900),
      );
      final labelY = (top + height - fillHeight - label.height - 2).clamp(
        0.0,
        size.height - label.height,
      );
      label.paint(canvas, Offset((size.width - label.width) / 2, labelY));
    } else {
      final label = _text(
        emptyLabel,
        const TextStyle(color: kOff, fontSize: 12, fontWeight: FontWeight.w700),
      );
      // In the middle of the bar, clear of the healthy-level lines near 0 g.
      label.paint(
        canvas,
        Offset(
          (size.width - label.width) / 2,
          top + (height - label.height) / 2,
        ),
      );
    }

    // Dashed line at the healthy pump's level for this axis.
    final h = healthy;
    if (h != null && h > 0) {
      final y = yFor(h);
      final dash = Paint()
        ..color = kReference
        ..strokeWidth = 2;
      for (var x = left - 6; x < left + barWidth + 6; x += 7) {
        canvas.drawLine(
          Offset(x, y),
          Offset((x + 4).clamp(0, left + barWidth + 6), y),
          dash,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant _BarPainter oldDelegate) =>
      oldDelegate.value != value ||
      oldDelegate.healthy != healthy ||
      oldDelegate.color != color ||
      oldDelegate.emptyLabel != emptyLabel;
}

// ---------------------------------------------------------------------------
// Circle gauge (temperature and test timer)
// ---------------------------------------------------------------------------

const double kCircleSize = 132;

/// A ring filled to [fraction] (0-1) with [value] and a small [label] inside.
class CircleGauge extends StatelessWidget {
  const CircleGauge({
    super.key,
    required this.fraction,
    required this.color,
    required this.value,
    required this.label,
    this.valueColor,
    this.labelColor,
    this.markerFraction,
  });

  final double fraction;
  final Color color;
  final String value;
  final String label;
  final Color? valueColor;
  final Color? labelColor;

  /// Optional tick on the ring (e.g. the healthy pump's maximum temperature).
  final double? markerFraction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: FittedBox(
        fit: BoxFit.scaleDown,
        child: SizedBox(
          width: kCircleSize,
          height: kCircleSize,
          child: CustomPaint(
            painter: _RingPainter(
              fraction: fraction.clamp(0.0, 1.0),
              color: color,
              markerFraction: markerFraction,
            ),
            child: Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 18),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    FittedBox(
                      fit: BoxFit.scaleDown,
                      child: Text(
                        value,
                        style: TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.w900,
                          color: valueColor ?? kInk,
                          fontFeatures: const [FontFeature.tabularFigures()],
                        ),
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      label,
                      textAlign: TextAlign.center,
                      maxLines: 2,
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        color: labelColor ?? kMuted,
                        height: 1.15,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({
    required this.fraction,
    required this.color,
    required this.markerFraction,
  });

  final double fraction;
  final Color color;
  final double? markerFraction;

  static const double stroke = 12;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = (size.shortestSide - stroke) / 2;
    final rect = Rect.fromCircle(center: center, radius: radius);
    const start = -math.pi / 2; // 12 o'clock, clockwise

    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = kTrack
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke,
    );
    if (fraction > 0) {
      canvas.drawArc(
        rect,
        start,
        2 * math.pi * fraction,
        false,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = stroke
          ..strokeCap = StrokeCap.round,
      );
    }
    final marker = markerFraction;
    if (marker != null) {
      final angle = start + 2 * math.pi * marker.clamp(0.0, 1.0);
      final direction = Offset(math.cos(angle), math.sin(angle));
      canvas.drawLine(
        center + direction * (radius - stroke / 2 - 3),
        center + direction * (radius + stroke / 2 + 3),
        Paint()
          ..color = kReference
          ..strokeWidth = 3
          ..strokeCap = StrokeCap.round,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) =>
      oldDelegate.fraction != fraction ||
      oldDelegate.color != color ||
      oldDelegate.markerFraction != markerFraction;
}

// ---------------------------------------------------------------------------
// Temperature
// ---------------------------------------------------------------------------

/// Temperature ring on a 0-100 °C scale, so the filled share equals the
/// reading; the tick marks the healthy pump's maximum.
class TemperatureCard extends StatelessWidget {
  const TemperatureCard({
    super.key,
    required this.temperatureC,
    required this.pumpRunning,
    required this.online,
    required this.healthyMaxC,
    this.healthyMeanC,
  });

  final double? temperatureC;
  final bool pumpRunning;
  final bool online;
  final double? healthyMaxC;

  /// The healthy pump's average running temperature (shown under the ring).
  final double? healthyMeanC;

  static const double scaleMaxC = 100;

  @override
  Widget build(BuildContext context) {
    final t = temperatureC;
    String status;
    Color color;
    if (t == null) {
      status = 'No reading';
      color = kOff;
    } else if (!online) {
      status = 'Last reading';
      color = kOff;
    } else if (!pumpRunning) {
      status = 'Pump off';
      color = kOff;
    } else if (healthyMaxC == null || t <= healthyMaxC!) {
      status = 'Normal';
      color = kHealthy;
    } else if (t <= healthyMaxC! + 10) {
      status = 'Warm';
      color = kWarning;
    } else {
      status = 'Hot';
      color = kFaulty;
    }
    final active = online && t != null && pumpRunning;
    // The healthy pump's temperature; the tick on the ring is its maximum.
    final String? healthyText = healthyMeanC == null && healthyMaxC == null
        ? null
        : 'Healthy: '
              '${healthyMeanC == null ? '' : '${healthyMeanC!.toStringAsFixed(1)} °C'}'
              '${healthyMeanC != null && healthyMaxC != null ? ' · ' : ''}'
              '${healthyMaxC == null ? '' : 'max ${healthyMaxC!.toStringAsFixed(1)} °C'}';

    return SectionCard(
      icon: Icons.thermostat,
      title: 'Temperature',
      child: Column(
        children: [
          CircleGauge(
            fraction: t == null ? 0 : t / scaleMaxC,
            color: active ? color : kOff,
            value: t == null ? '— °C' : '${t.toStringAsFixed(1)} °C',
            valueColor: !online || t == null ? kOff : (active ? color : kInk),
            label: status,
            labelColor: color,
            markerFraction: healthyMaxC == null
                ? null
                : healthyMaxC! / scaleMaxC,
          ),
          if (healthyText != null) ...[
            const SizedBox(height: 10),
            Text(
              healthyText,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: kReference,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Test timer
// ---------------------------------------------------------------------------

/// Countdown ring of the timed test; the duration is set in the settings (⚙).
class TestTimerCard extends StatelessWidget {
  const TestTimerCard({
    super.key,
    required this.test,
    required this.remainingSeconds,
    required this.selectedMinutes,
    required this.pumpRunning,
    required this.busy,
    required this.onStart,
    required this.onStop,
    required this.onOpenSettings,
    this.canStart = true,
    this.connected = true,
  });

  final PumpTest test;
  final double remainingSeconds;
  final int selectedMinutes;
  final bool pumpRunning;
  final bool busy;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onOpenSettings;

  /// False when no pump is selected.
  final bool canStart;

  /// The pump's ESP32 is online: a test can only start then.
  final bool connected;

  @override
  Widget build(BuildContext context) {
    final running = test.isRunning;
    final totalSeconds = running ? test.minutes * 60 : selectedMinutes * 60.0;
    final shownSeconds = running ? remainingSeconds : totalSeconds;

    // The ring shows the time left: full at the start, empty when done.
    double fraction;
    String label;
    Color color;
    Color labelColor;
    if (running) {
      fraction = totalSeconds > 0 ? remainingSeconds / totalSeconds : 0;
      label = pumpRunning ? 'left' : 'switch pump ON';
      color = kReference;
      labelColor = pumpRunning ? kMuted : kWarning;
    } else if (test.state == 'finished') {
      fraction = 0;
      label = 'done · pump OFF';
      color = kOff;
      labelColor = kFaulty;
    } else if (canStart && !connected) {
      fraction = 1;
      label = 'ESP32 offline';
      color = kOff.withAlpha(120);
      labelColor = kWarning;
    } else {
      fraction = 1;
      label = test.state == 'stopped' ? 'stopped' : 'ready';
      color = kOff.withAlpha(120);
      labelColor = kMuted;
    }

    return SectionCard(
      icon: Icons.timer_outlined,
      title: 'Test timer',
      trailing: IconButton(
        tooltip: 'Test settings',
        icon: const Icon(Icons.settings_outlined, size: 20),
        visualDensity: VisualDensity.compact,
        constraints: const BoxConstraints.tightFor(width: 32, height: 32),
        padding: EdgeInsets.zero,
        onPressed: onOpenSettings,
      ),
      child: Column(
        children: [
          CircleGauge(
            fraction: fraction,
            color: color,
            value: formatDuration(shownSeconds),
            valueColor: running ? kReference : kInk,
            label: label,
            labelColor: labelColor,
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: running
                ? OutlinedButton.icon(
                    onPressed: busy ? null : onStop,
                    icon: const Icon(Icons.stop),
                    label: const Text('Stop test'),
                    style: OutlinedButton.styleFrom(foregroundColor: kFaulty),
                  )
                : FilledButton.icon(
                    onPressed: busy || !canStart || !connected ? null : onStart,
                    icon: const Icon(Icons.play_arrow),
                    label: Text(test.exists ? 'New test' : 'Start test'),
                  ),
          ),
        ],
      ),
    );
  }
}

/// Test settings: the test duration (default 20 minutes).
class TestSettingsDialog extends StatefulWidget {
  const TestSettingsDialog({
    super.key,
    required this.minutes,
    required this.testRunning,
  });

  final int minutes;
  final bool testRunning;

  static const int defaultMinutes = 20;
  static const int minMinutes = 1;
  static const int maxMinutes = 180;
  static const presets = [5, 10, 20, 30, 60];

  @override
  State<TestSettingsDialog> createState() => _TestSettingsDialogState();
}

class _TestSettingsDialogState extends State<TestSettingsDialog> {
  late int _minutes = widget.minutes;

  void _set(int minutes) => setState(
    () => _minutes = minutes.clamp(
      TestSettingsDialog.minMinutes,
      TestSettingsDialog.maxMinutes,
    ),
  );

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      icon: const Icon(Icons.settings_outlined, color: kReference),
      title: const Text('Test settings'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Test duration',
            style: TextStyle(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              IconButton.outlined(
                tooltip: '1 minute less',
                onPressed: _minutes > TestSettingsDialog.minMinutes
                    ? () => _set(_minutes - 1)
                    : null,
                icon: const Icon(Icons.remove),
              ),
              Expanded(
                child: Text(
                  '$_minutes min',
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ),
              IconButton.outlined(
                tooltip: '1 minute more',
                onPressed: _minutes < TestSettingsDialog.maxMinutes
                    ? () => _set(_minutes + 1)
                    : null,
                icon: const Icon(Icons.add),
              ),
            ],
          ),
          Slider(
            value: _minutes.clamp(1, 60).toDouble(), // longer: use + / -
            min: TestSettingsDialog.minMinutes.toDouble(),
            max: 60,
            divisions: 59,
            label: '$_minutes min',
            onChanged: (value) => _set(value.round()),
          ),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final minutes in TestSettingsDialog.presets)
                ChoiceChip(
                  label: Text('$minutes min'),
                  selected: _minutes == minutes,
                  onSelected: (_) => _set(minutes),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            widget.testRunning
                ? 'A test is running: the new duration applies to the next test.'
                : 'Default: ${TestSettingsDialog.defaultMinutes} minutes.',
            style: const TextStyle(fontSize: 12, color: kMuted),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => _set(TestSettingsDialog.defaultMinutes),
          child: const Text('Reset to 20 min'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_minutes),
          child: const Text('Save'),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Machine health: vertical bar and the values behind it
// ---------------------------------------------------------------------------

/// Machine health, in two equal halves: the health bar (left) and the values
/// behind it (right), each centred.
class MachineHealthCard extends StatelessWidget {
  const MachineHealthCard({super.key, required this.assessment, this.subtitle});

  final Assessment assessment;

  /// Which data the values are calculated over, e.g. the test and its time.
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final a = assessment;
    final color = levelColor(a.verdict);
    // How far toward faulty: 0% = healthy, 100% = faulty.
    final faulty = a.faultyPercent;

    return SectionCard(
      icon: Icons.health_and_safety_outlined,
      title: 'Machine health',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (subtitle != null) ...[
            Text(
              subtitle!,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: kMuted,
              ),
            ),
            const SizedBox(height: 10),
          ],
          _machineHealthBody(a, color, faulty),
        ],
      ),
    );
  }

  Widget _machineHealthBody(Assessment a, Color color, double? faulty) {
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    faulty == null ? '—' : '${faulty.toStringAsFixed(0)}%',
                    style: TextStyle(
                      fontSize: 26,
                      fontWeight: FontWeight.w900,
                      color: faulty == null ? kOff : color,
                    ),
                  ),
                  const Text(
                    '0% healthy · 100% faulty',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: kMuted,
                    ),
                  ),
                  const SizedBox(height: 8),
                  VerticalHealthBar(faultyPercent: faulty),
                ],
              ),
            ),
          ),
          const VerticalDivider(width: 20, thickness: 1, color: kTrack),
          Expanded(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _StackedInfo(
                    label: 'Vibration vs healthy pump',
                    value: a.totalRmsG == null
                        ? '—'
                        : '${a.totalRmsG!.toStringAsFixed(2)} g vs '
                              '${a.healthyTotalRmsG?.toStringAsFixed(2) ?? '—'} g '
                              '(${formatPercent(a.deviationPercent, signed: true)})',
                    valueColor: a.deviationPercent == null
                        ? null
                        : riskColor(a.riskParts['vibration_deviation']),
                  ),
                  _StackedInfo(
                    label: 'Unusual vibration windows',
                    value: a.anomalyWindowPercent == null
                        ? '—'
                        : '${formatPercent(a.anomalyWindowPercent)} (healthy pump: up to 45%)',
                    valueColor: a.anomalyWindowPercent == null
                        ? null
                        : riskColor(a.riskParts['unusual_windows']),
                  ),
                  _StackedInfo(
                    label: 'Risk score',
                    value: a.riskScore == null
                        ? '—'
                        : '${a.riskScore!.toStringAsFixed(0)} / 100',
                    valueColor: a.riskScore == null
                        ? null
                        : riskColor(a.riskScore),
                  ),
                  _StackedInfo(
                    label: 'Temperature (running)',
                    value: a.temperatureMaxC == null
                        ? '—'
                        : 'max ${a.temperatureMaxC!.toStringAsFixed(1)} °C '
                              '(healthy max ${a.healthyTemperatureMaxC?.toStringAsFixed(1) ?? '—'} °C)',
                    valueColor: a.temperatureMaxC == null
                        ? null
                        : riskColor(a.riskParts['temperature']),
                  ),
                  _StackedInfo(
                    label: 'Running data',
                    value: a.windows == 0
                        ? 'none yet'
                        : '${a.windows} windows · ${formatDuration(a.runningSeconds)}',
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Label above its value, centred.
class _StackedInfo extends StatelessWidget {
  const _StackedInfo({
    required this.label,
    required this.value,
    this.valueColor,
  });

  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        children: [
          Text(
            label,
            textAlign: TextAlign.center,
            style: const TextStyle(
              color: kMuted,
              fontWeight: FontWeight.w600,
              fontSize: 12,
            ),
          ),
          const SizedBox(height: 1),
          Text(
            value,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontWeight: FontWeight.w800,
              fontSize: 13.5,
              color: valueColor ?? kInk,
            ),
          ),
        ],
      ),
    );
  }
}

/// Machine health as one bar drawn like the vibration bars (same height,
/// scale, grid lines, rounded bar and value above the fill). It shows how far
/// the pump is toward faulty: 0% = Healthy at the bottom, 100% = Faulty at
/// the top, filled from the bottom to that value (20% = 20% toward faulty),
/// green at the bottom to red at the top.
class VerticalHealthBar extends StatelessWidget {
  const VerticalHealthBar({super.key, required this.faultyPercent});

  /// 0 (healthy) - 100 (faulty); null = no data.
  final double? faultyPercent;

  static const double scaleWidth = 40; // room for "100%"
  static const double columnWidth =
      96; // bar = half of it, as the vibration bars

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text(
          'Faulty',
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w800,
            color: kFaulty,
          ),
        ),
        const SizedBox(height: 2),
        SizedBox(
          width: scaleWidth + columnWidth,
          height: kBarHeight + 22,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(
                width: scaleWidth,
                height: kBarHeight + 22,
                child: CustomPaint(
                  painter: _ScalePainter(
                    labels: ['0%', '20%', '40%', '60%', '80%', '100%'],
                  ),
                ),
              ),
              SizedBox(
                width: columnWidth,
                height: kBarHeight + 22,
                child: CustomPaint(
                  painter: _HealthBarPainter(faultyPercent: faultyPercent),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Healthy',
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w800,
            color: kHealthy,
          ),
        ),
      ],
    );
  }
}

const _healthColors = [kHealthy, kMonitor, kWarning, kFaulty];
const _healthStops = [0.0, 0.35, 0.65, 1.0];

/// The health bar's colour at [t] (0 = bottom, healthy ... 1 = top, faulty).
Color _healthColorAt(double t) {
  for (var i = 1; i < _healthStops.length; i++) {
    if (t <= _healthStops[i]) {
      final span = _healthStops[i] - _healthStops[i - 1];
      return Color.lerp(
        _healthColors[i - 1],
        _healthColors[i],
        (t - _healthStops[i - 1]) / span,
      )!;
    }
  }
  return _healthColors.last;
}

class _HealthBarPainter extends CustomPainter {
  _HealthBarPainter({required this.faultyPercent});

  /// 0 (healthy) - 100 (faulty); null = no data.
  final double? faultyPercent;

  @override
  void paint(Canvas canvas, Size size) {
    // Same layout as _BarPainter: 22 px for the value label, bar half as wide
    // as its column, grid lines every fifth (20%) matching the scale.
    const top = 22.0;
    final height = size.height - top;
    final barWidth = (size.width * 0.5).clamp(18.0, 64.0);
    final left = (size.width - barWidth) / 2;
    final barRect = Rect.fromLTWH(left, top, barWidth, height);

    final grid = Paint()
      ..color = const Color(0xFFE2E8F0)
      ..strokeWidth = 1;
    for (var i = 0; i <= 5; i++) {
      final y = top + height - height * i / 5;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }

    canvas.drawRRect(
      RRect.fromRectAndRadius(barRect, const Radius.circular(6)),
      Paint()..color = kTrack.withAlpha(200),
    );

    final faulty = faultyPercent;
    if (faulty == null) {
      final label = _text(
        'no data',
        const TextStyle(color: kOff, fontSize: 12, fontWeight: FontWeight.w700),
      );
      label.paint(
        canvas,
        Offset(
          (size.width - label.width) / 2,
          top + (height - label.height) / 2,
        ),
      );
      return;
    }

    // Fill from the bottom (0% = healthy) up to the faulty %; the gradient
    // spans the whole bar, so a higher fill reaches the warning and faulty
    // colours.
    final level = (faulty / 100).clamp(0.0, 1.0);
    final fillHeight = (height * level).clamp(level > 0 ? 3.0 : 0.0, height);
    if (fillHeight > 0) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(left, top + height - fillHeight, barWidth, fillHeight),
          const Radius.circular(6),
        ),
        Paint()
          ..shader = const LinearGradient(
            begin: Alignment.bottomCenter,
            end: Alignment.topCenter,
            colors: _healthColors,
            stops: _healthStops,
          ).createShader(barRect),
      );
    }

    // The faulty % above the fill, in the bar's colour at that level.
    final label = _text(
      '${faulty.toStringAsFixed(0)}%',
      TextStyle(
        color: _healthColorAt(level),
        fontSize: 13,
        fontWeight: FontWeight.w900,
      ),
    );
    final labelY = (top + height - fillHeight - label.height - 2).clamp(
      0.0,
      size.height - label.height,
    );
    label.paint(canvas, Offset((size.width - label.width) / 2, labelY));
  }

  @override
  bool shouldRepaint(covariant _HealthBarPainter oldDelegate) =>
      oldDelegate.faultyPercent != faultyPercent;
}

// ---------------------------------------------------------------------------
// System logo
// ---------------------------------------------------------------------------

/// The system's logo: a water drop in a teal rounded square.
class SystemLogo extends StatelessWidget {
  const SystemLogo({super.key, this.size = 36});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: const Color(0xFFE0F2F1),
        borderRadius: BorderRadius.circular(size * 0.22),
      ),
      child: Icon(
        Icons.water_drop_outlined,
        color: kReference,
        size: size * 0.6,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Machine problems
// ---------------------------------------------------------------------------

Color _severityColor(String severity) =>
    severity.toLowerCase() == 'high' ? kFaulty : kWarning;

IconData _componentIcon(String component) {
  final part = component.toLowerCase();
  if (part.startsWith('bearing')) return Icons.settings;
  if (part.startsWith('motor')) return Icons.electric_bolt;
  if (part.startsWith('impeller')) return Icons.cyclone;
  if (part.startsWith('mounting')) return Icons.construction;
  return Icons.report_problem_outlined;
}

/// Machine Problems: what the test's data points to (bearing, motor,
/// impeller, ...), with the data behind it and what to check, or a positive
/// message when the pump is healthy.
class MachineProblemsCard extends StatelessWidget {
  const MachineProblemsCard({
    super.key,
    required this.assessment,
    required this.hasTest,
    this.testRunning = false,
  });

  /// The test's result (the same data as Machine health).
  final Assessment assessment;
  final bool hasTest;
  final bool testRunning;

  @override
  Widget build(BuildContext context) {
    final problems = assessment.problems;
    Widget? trailing;
    Widget child;
    if (!hasTest) {
      child = const _ProblemsMessage(
        icon: Icons.play_circle_outline,
        color: kMuted,
        title: 'No test yet',
        text: 'Start a test: the pump is checked for problems over the test.',
      );
    } else if (!assessment.hasData) {
      child = _ProblemsMessage(
        icon: Icons.hourglass_empty,
        color: kMuted,
        title: testRunning ? 'Collecting data…' : 'No running data',
        text: testRunning
            ? 'Switch the pump ON: problems are found as the test runs.'
            : 'The test had no running data. Run a new test with the pump ON.',
      );
    } else if (problems.isEmpty) {
      final healthy = assessment.verdict == 'HEALTHY';
      trailing = StatusChip(
        label: healthy ? 'No problems' : 'Monitor',
        color: healthy ? kHealthy : kMonitor,
      );
      child = healthy
          ? const _ProblemsMessage(
              icon: Icons.check_circle,
              color: kHealthy,
              title: 'No problems found',
              text:
                  'The pump runs like the healthy pump: bearings, motor and '
                  'impeller look normal. Keep testing it regularly.',
            )
          : const _ProblemsMessage(
              icon: Icons.visibility,
              color: kMonitor,
              title: 'No specific problem found',
              text:
                  'Some readings differ from the healthy pump, but none points '
                  'to one part. Keep monitoring and run another test.',
            );
    } else {
      final worst = problems.any((p) => p.severity.toLowerCase() == 'high')
          ? kFaulty
          : kWarning;
      trailing = StatusChip(label: '${problems.length} found', color: worst);
      child = Column(
        children: [
          for (var i = 0; i < problems.length; i++) ...[
            if (i > 0) const SizedBox(height: 10),
            _ProblemTile(problem: problems[i]),
          ],
        ],
      );
    }
    return SectionCard(
      icon: Icons.build_circle_outlined,
      title: 'Machine Problems',
      trailing: trailing,
      child: child,
    );
  }
}

class _ProblemsMessage extends StatelessWidget {
  const _ProblemsMessage({
    required this.icon,
    required this.color,
    required this.title,
    required this.text,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: color, size: 28),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 15,
                  color: color == kMuted ? kInk : color,
                ),
              ),
              const SizedBox(height: 2),
              Text(text, style: const TextStyle(color: kMuted, height: 1.3)),
            ],
          ),
        ),
      ],
    );
  }
}

class _ProblemTile extends StatelessWidget {
  const _ProblemTile({required this.problem});

  final PumpProblem problem;

  @override
  Widget build(BuildContext context) {
    final color = _severityColor(problem.severity);
    final high = problem.severity.toLowerCase() == 'high';
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withAlpha(14),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withAlpha(80)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_componentIcon(problem.component), color: color, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  problem.title,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              const SizedBox(width: 6),
              StatusChip(label: high ? 'High' : 'Medium', color: color),
            ],
          ),
          if (problem.component.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              problem.component,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: color,
              ),
            ),
          ],
          const SizedBox(height: 6),
          Text(problem.evidence, style: const TextStyle(fontSize: 12.5)),
          const SizedBox(height: 6),
          Text.rich(
            TextSpan(
              children: [
                const TextSpan(
                  text: 'What to check: ',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
                TextSpan(text: problem.check),
              ],
            ),
            style: const TextStyle(fontSize: 12.5, color: kInk),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Previous tests
// ---------------------------------------------------------------------------

/// The pump's previous tests, newest first, each with all its data; a button
/// deletes them all.
class PreviousTestsCard extends StatelessWidget {
  const PreviousTestsCard({
    super.key,
    required this.tests,
    required this.loading,
    required this.onDeleteAll,
    required this.formatDateTime,
  });

  /// Null until loaded.
  final List<PumpTest>? tests;
  final bool loading;
  final VoidCallback? onDeleteAll;
  final String Function(DateTime?) formatDateTime;

  @override
  Widget build(BuildContext context) {
    final list = tests ?? const <PumpTest>[];
    Widget child;
    if (tests == null && loading) {
      child = const Padding(
        padding: EdgeInsets.all(12),
        child: Center(child: CircularProgressIndicator()),
      );
    } else if (list.isEmpty) {
      child = const Text(
        'No previous tests yet. Each finished or stopped test appears here.',
        style: TextStyle(color: kMuted),
      );
    } else {
      child = Column(
        children: [
          for (final test in list)
            _PreviousTestTile(test: test, formatDateTime: formatDateTime),
        ],
      );
    }
    return SectionCard(
      icon: Icons.history,
      title: 'Previous tests',
      trailing: TextButton.icon(
        onPressed: list.isEmpty || onDeleteAll == null ? null : onDeleteAll,
        style: TextButton.styleFrom(
          foregroundColor: kFaulty,
          visualDensity: VisualDensity.compact,
        ),
        icon: const Icon(Icons.delete_outline, size: 18),
        label: const Text('Delete all'),
      ),
      child: child,
    );
  }
}

class _PreviousTestTile extends StatelessWidget {
  const _PreviousTestTile({required this.test, required this.formatDateTime});

  final PumpTest test;
  final String Function(DateTime?) formatDateTime;

  static String _g(double? value) =>
      value == null ? '—' : '${value.toStringAsFixed(2)} g';

  static String _axis(AxisLevel level) {
    if (level.rmsG == null) return '—';
    final times = level.timesHealthy == null
        ? ''
        : ', ${level.timesHealthy!.toStringAsFixed(1)}× healthy';
    return '${_g(level.rmsG)} (${levelLabel(level.status)}$times)';
  }

  List<(String, String)> _rows(Assessment a) {
    final faulty = a.faultyPercent;
    final minutes = test.minutes.toStringAsFixed(test.minutes % 1 == 0 ? 0 : 1);
    final temperature = a.temperatureMaxC == null
        ? '—'
        : 'max ${a.temperatureMaxC!.toStringAsFixed(1)} °C'
              '${a.temperatureMeanC == null ? '' : ', mean ${a.temperatureMeanC!.toStringAsFixed(1)} °C'}'
              ' (healthy max ${a.healthyTemperatureMaxC?.toStringAsFixed(1) ?? '—'} °C)';
    return [
      ('Started', formatDateTime(test.startedAt)),
      ('Ended', formatDateTime(test.finishedAt ?? test.endsAt)),
      (
        'Duration',
        '$minutes min set · ran ${formatDuration(test.elapsedSeconds)}'
            '${test.state == 'stopped' ? ' (stopped early)' : ''}',
      ),
      (
        'Result',
        a.hasData
            ? '${levelLabel(a.verdict)}: ${a.verdictText}'
            : 'No running data',
      ),
      (
        'Toward faulty',
        faulty == null
            ? '—'
            : '${faulty.toStringAsFixed(0)}% (0% healthy · 100% faulty)',
      ),
      (
        'Risk score',
        a.riskScore == null ? '—' : '${a.riskScore!.toStringAsFixed(0)} / 100',
      ),
      (
        'Vibration vs healthy',
        a.totalRmsG == null
            ? '—'
            : '${_g(a.totalRmsG)} vs ${_g(a.healthyTotalRmsG)} '
                  '(${formatPercent(a.deviationPercent, signed: true)})',
      ),
      (
        'Unusual windows',
        a.anomalyWindowPercent == null
            ? '—'
            : '${formatPercent(a.anomalyWindowPercent)} '
                  '(model ${formatPercent(a.modelUnusualPercent)})',
      ),
      for (final level in a.axes) ('${level.axis} axis', _axis(level)),
      ('Temperature', temperature),
      (
        'Running data',
        a.windows == 0
            ? 'none'
            : '${a.windows} windows · ${formatDuration(a.runningSeconds)}',
      ),
      (
        'Problems',
        a.problems.isEmpty
            ? (a.hasData ? 'None found' : '—')
            : a.problems.map((p) => p.title).join('\n'),
      ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final a = test.assessment ?? Assessment.empty();
    final color = a.hasData ? levelColor(a.verdict) : kOff;
    final faulty = a.faultyPercent;
    return Theme(
      // No divider lines from the expansion tile inside the card.
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(bottom: 10),
        leading: Icon(verdictIcon(a.verdict), color: color),
        title: Text(
          formatDateTime(test.startedAt),
          style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 14),
        ),
        subtitle: Text(
          '${test.minutes.toStringAsFixed(0)} min test · '
          '${faulty == null ? 'no data' : '${faulty.toStringAsFixed(0)}% toward faulty'}'
          '${test.state == 'stopped' ? ' · stopped' : ''}',
          style: const TextStyle(fontSize: 12, color: kMuted),
        ),
        trailing: StatusChip(
          label: a.hasData ? levelLabel(a.verdict) : 'No data',
          color: color,
        ),
        children: [
          for (final (label, value) in _rows(a))
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 130,
                    child: Text(
                      label,
                      style: const TextStyle(
                        color: kMuted,
                        fontWeight: FontWeight.w600,
                        fontSize: 12.5,
                      ),
                    ),
                  ),
                  Expanded(
                    child: Text(
                      value,
                      style: const TextStyle(
                        fontWeight: FontWeight.w700,
                        fontSize: 12.5,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:water_pump_maintenance/dashboard_widgets.dart';
import 'package:water_pump_maintenance/live_data.dart';

/// A real /api/esp32/live response: a faulty-like pump (strong Y axis) during
/// a running 20-minute test (made by the backend with synthetic data).
LiveData loadFaultyFixture() {
  final json = jsonDecode(
    File('test/fixtures/live_faulty.json').readAsStringSync(),
  );
  return LiveData.fromJson(Map<String, dynamic>.from(json as Map));
}

Widget phone(Widget child) => MaterialApp(
  home: MediaQuery(
    data: const MediaQueryData(size: Size(400, 2400)),
    child: Scaffold(
      body: SingleChildScrollView(child: SizedBox(width: 400, child: child)),
    ),
  ),
);

void main() {
  test('parses the live response', () {
    final live = loadFaultyFixture();
    expect(live.deviceName, 'ESP32 Pump Sensor1');
    expect(live.pumpRunning, isTrue);
    expect(live.axes.map((a) => a.status), ['healthy', 'faulty', 'healthy']);
    expect(live.axes[1].rmsG, closeTo(1.19, 0.05));
    expect(live.healthyReference.axisRmsG['y'], closeTo(0.077, 0.001));
    expect(live.liveAssessment.verdict, 'FAULTY');
    expect(live.test.isRunning, isTrue);
    expect(live.test.minutes, 20);
    expect(
      live.test.assessment!.problems.first.title,
      'Radial bearing wear or rotor imbalance',
    );
  });

  group('sensors online while the ESP32 sends', () {
    String serverTime(Duration ago) =>
        // Like the deployed backend: UTC without a time zone.
        DateTime.now()
            .toUtc()
            .subtract(ago)
            .toIso8601String()
            .replaceAll('Z', '');

    // A /api/esp32/status response from a backend without the live view.
    Map<String, dynamic> status(Duration ago) => {
      'device_id': 'ESP32-002',
      'device_name': 'ESP32 Pump Sensor2',
      'location': 'Pump Area',
      'last_update': serverTime(ago),
      'latest_sample': {
        'pump_running': false,
        'temperature_c': 34.5,
        'vibration_rms_g': 0.0083,
      },
      'latest_temperature_c': 34.5,
      'status': 'success',
    };

    test('a backend without the live view: online from /status', () {
      final live = LiveData.fromStatusJson(status(const Duration(seconds: 2)));
      expect(live.online, isTrue);
      expect(live.deviceName, 'ESP32 Pump Sensor2');
      expect(live.temperatureC, 34.5);
      expect(live.pumpRunning, isFalse);
      expect(live.liveAssessment.hasData, isFalse);

      final stale = LiveData.fromStatusJson(status(const Duration(minutes: 2)));
      expect(stale.online, isFalse);
    });

    test('the device list: the backend\'s "online" wins', () {
      final recent = serverTime(const Duration(seconds: 3));
      final old = serverTime(const Duration(minutes: 5));
      expect(isDeviceOnline({'last_update': recent}), isTrue);
      expect(isDeviceOnline({'last_update': old}), isFalse);
      expect(isDeviceOnline({'last_update': null}), isFalse);
      expect(isDeviceOnline({'last_update': old, 'online': true}), isTrue);
      expect(isDeviceOnline({'last_update': recent, 'online': false}), isFalse);
    });

    testWidgets('pump sensors: each pump online or offline, tap to show it', (
      tester,
    ) async {
      String? selected;
      await tester.pumpWidget(
        phone(
          PumpStatusCard(
            pumps: const [
              PumpStatus(
                deviceId: 'ESP32-001',
                name: 'ESP32 Pump Sensor1',
                location: 'Faulty Pump',
                online: false,
              ),
              PumpStatus(
                deviceId: 'ESP32-002',
                name: 'ESP32 Pump Sensor2',
                location: 'Pump Area',
                online: true,
              ),
            ],
            selectedDeviceId: 'ESP32-001',
            onSelect: (id) => selected = id,
          ),
        ),
      );
      expect(find.text('1 of 2 online'), findsOneWidget);
      expect(find.text('Online'), findsOneWidget);
      expect(find.text('Offline'), findsOneWidget);
      expect(find.text('ESP32-002 · Pump Area'), findsOneWidget);
      await tester.tap(find.text('ESP32 Pump Sensor2'));
      expect(selected, 'ESP32-002');
      expect(tester.takeException(), isNull);
    });

    testWidgets('running without per-axis levels: "No data", not "Pump off"', (
      tester,
    ) async {
      await tester.pumpWidget(
        phone(
          VibrationBarsCard(
            axes: kDefaultAxes,
            pumpRunning: true,
            online: true,
            overall: Assessment.empty(),
            liveTotalRmsG: null,
            healthyTotalRmsG: kDefaultHealthyReference.totalRmsG,
          ),
        ),
      );
      expect(find.text('Pump running'), findsOneWidget);
      expect(find.text('No data'), findsNWidgets(3));
      expect(find.text('Pump off'), findsNothing);
    });
  });

  testWidgets('vibration bars show each axis and its status', (tester) async {
    final live = loadFaultyFixture();
    await tester.pumpWidget(
      phone(
        VibrationBarsCard(
          axes: live.axes,
          pumpRunning: true,
          online: true,
          overall: live.liveAssessment,
          liveTotalRmsG: 1.47,
          healthyTotalRmsG: live.healthyReference.totalRmsG,
        ),
      ),
    );
    expect(find.text('X axis'), findsOneWidget);
    expect(find.text('Y axis'), findsOneWidget);
    expect(find.text('Faulty'), findsOneWidget); // Y-axis chip
    expect(find.text('Healthy'), findsWidgets);
    expect(find.text('Pump running · Faulty'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('temperature and timer circles sit side by side on a phone', (
    tester,
  ) async {
    var started = false;
    var settingsOpened = false;
    await tester.pumpWidget(
      phone(
        IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Expanded(
                child: TemperatureCard(
                  temperatureC: 58,
                  pumpRunning: true,
                  online: true,
                  healthyMaxC: 65.25,
                  healthyMeanC: 55.37,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TestTimerCard(
                  test: PumpTest.none,
                  remainingSeconds: 0,
                  selectedMinutes: 20,
                  pumpRunning: false,
                  busy: false,
                  onStart: () => started = true,
                  onStop: () {},
                  onOpenSettings: () => settingsOpened = true,
                ),
              ),
            ],
          ),
        ),
      ),
    );
    // Inside the circles: the reading and the countdown, nothing below them.
    expect(find.text('58.0 °C'), findsOneWidget);
    expect(find.text('Normal'), findsOneWidget);
    expect(find.text('20:00'), findsOneWidget);
    expect(find.text('ready'), findsOneWidget);
    expect(find.text('Healthy: 55.4 °C · max 65.3 °C'), findsOneWidget);
    expect(find.text('5'), findsNothing); // no duration presets on the card
    expect(find.byType(CircleGauge), findsNWidgets(2));
    await tester.tap(find.text('Start test'));
    expect(started, isTrue);
    await tester.tap(find.byTooltip('Test settings'));
    expect(settingsOpened, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Start test works only while the ESP32 is connected', (
    tester,
  ) async {
    var started = 0;
    Widget timer({required bool connected}) => phone(
      TestTimerCard(
        test: PumpTest.none,
        remainingSeconds: 0,
        selectedMinutes: 20,
        pumpRunning: false,
        busy: false,
        onStart: () => started++,
        onStop: () {},
        onOpenSettings: () {},
        connected: connected,
      ),
    );

    await tester.pumpWidget(timer(connected: false));
    expect(find.text('ESP32 offline'), findsOneWidget);
    await tester.tap(find.text('Start test'));
    expect(started, 0); // disabled

    await tester.pumpWidget(timer(connected: true));
    expect(find.text('ready'), findsOneWidget);
    await tester.tap(find.text('Start test'));
    expect(started, 1);
  });

  testWidgets('vibration bars show the live ADXL values with the pump off', (
    tester,
  ) async {
    AxisLevel level(String axis, double rms) => AxisLevel(
      axis: axis,
      pumpPart: '',
      rmsG: rms,
      healthyRmsG: 0.3,
      healthyLimitG: 0.5,
      timesHealthy: null,
      status: 'off',
    );
    await tester.pumpWidget(
      phone(
        VibrationBarsCard(
          axes: [level('X', 0.012), level('Y', 0.008), level('Z', 0.021)],
          pumpRunning: false,
          online: true,
          overall: Assessment.empty(),
          liveTotalRmsG: null,
          healthyTotalRmsG: 0.75,
        ),
      ),
    );
    expect(find.text('Pump off'), findsNWidgets(4)); // header + 3 axis chips
    expect(find.text('0.012 g'), findsOneWidget);
    expect(find.text('0.008 g'), findsOneWidget);
    expect(find.text('0.021 g'), findsOneWidget);
    expect(find.text('No data'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a running test counts down', (tester) async {
    final live = loadFaultyFixture();
    await tester.pumpWidget(
      phone(
        TestTimerCard(
          test: live.test,
          remainingSeconds: 754,
          selectedMinutes: 20,
          pumpRunning: true,
          busy: false,
          onStart: () {},
          onStop: () {},
          onOpenSettings: () {},
        ),
      ),
    );
    expect(find.text('12:34'), findsOneWidget);
    expect(find.text('left'), findsOneWidget);
    expect(find.text('Stop test'), findsOneWidget);
  });

  testWidgets('test settings change the duration (default 20 min)', (
    tester,
  ) async {
    int? saved;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () async {
              saved = await showDialog<int>(
                context: context,
                builder: (_) =>
                    const TestSettingsDialog(minutes: 7, testRunning: false),
              );
            },
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('7 min'), findsWidgets);
    await tester.tap(find.text('Reset to 20 min'));
    await tester.pump();
    expect(find.text('20 min'), findsWidgets);
    await tester.tap(find.byTooltip('1 minute more'));
    await tester.pump();
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(saved, 21);
  });

  testWidgets('machine health counts up from 0% healthy to 100% faulty', (
    tester,
  ) async {
    // Risk 20 (machine health 80%): the bar shows 20% toward faulty.
    final result = Assessment.fromJson({
      'verdict': 'HEALTHY',
      'health_percent': 80,
      'risk_score': 20,
      'windows': 12,
    });
    expect(result.faultyPercent, 20);
    await tester.pumpWidget(phone(MachineHealthCard(assessment: result)));
    expect(find.text('20%'), findsOneWidget);
    expect(find.text('80%'), findsNothing);
    expect(
      tester
          .widget<VerticalHealthBar>(find.byType(VerticalHealthBar))
          .faultyPercent,
      20,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('machine health: vertical bar left, values right', (
    tester,
  ) async {
    final live = loadFaultyFixture();
    await tester.pumpWidget(
      phone(MachineHealthCard(assessment: live.test.assessment!)),
    );
    expect(find.text('Machine health'), findsOneWidget);
    expect(find.text('Overall result'), findsNothing);
    // Machine health 0% = 100% toward faulty.
    expect(find.text('100%'), findsOneWidget);
    expect(find.text('0% healthy · 100% faulty'), findsOneWidget);
    expect(find.byType(VerticalHealthBar), findsOneWidget);
    // Two equal halves: the bar centred on the left, the values on the right.
    final card = tester.getRect(find.byType(MachineHealthCard));
    final bar = tester.getCenter(find.byType(VerticalHealthBar));
    final risk = tester.getCenter(find.text('Risk score'));
    expect(bar.dx, closeTo(card.left + card.width * 0.25, 20));
    expect(risk.dx, closeTo(card.left + card.width * 0.75, 20));
    // One bar: Faulty at the top, Healthy at the bottom.
    expect(
      tester.getCenter(find.text('Faulty')).dy,
      lessThan(tester.getCenter(find.text('Healthy')).dy),
    );
    for (final label in [
      'Vibration vs healthy pump',
      'Unusual vibration windows',
      'Risk score',
      'Temperature (running)',
      'Running data',
    ]) {
      expect(find.text(label), findsOneWidget, reason: label);
    }
    // Removed: verdict box, parts line, possible problems.
    expect(find.text('FAULTY'), findsNothing);
    expect(find.text('Parts over healthy limit'), findsNothing);
    expect(find.text('Possible problems'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}

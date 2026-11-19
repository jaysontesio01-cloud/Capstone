// The whole dashboard shows even without a pump, an ESP32 or a server.
// (In widget tests every HTTP request fails, like a server that can't be reached.)

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:water_pump_maintenance/main.dart';

Future<void> startApp(WidgetTester tester) async {
  // Tall phone screen, so every section of the list is built.
  tester.view.physicalSize = const Size(420, 3200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(const MyApp());
  for (var i = 0; i < 5; i++) {
    await tester.pump(const Duration(milliseconds: 200));
  }
}

Future<void> closeApp(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox()); // stops the app's timers
}

void expectAllSections() {
  for (final text in [
    'Pump sensors',
    'Vibration (X, Y, Z)',
    'X axis',
    'Y axis',
    'Z axis',
    'Temperature',
    'Test timer',
    'Machine health',
  ]) {
    expect(find.text(text), findsOneWidget, reason: text);
  }
  expect(find.text('Predictive maintenance'), findsNothing); // removed
  // The healthy pump's values are built in.
  expect(find.textContaining('Healthy 0.50 g'), findsOneWidget);
  expect(find.textContaining('Healthy 0.15 g'), findsOneWidget);
  expect(find.textContaining('Healthy 0.67 g'), findsOneWidget);
  expect(find.text('— °C'), findsOneWidget); // temperature circle
  expect(find.text('Healthy: 55.4 °C · max 65.3 °C'), findsOneWidget);
  expect(find.text('20:00'), findsOneWidget); // default test duration
  expect(find.text('Running data'), findsOneWidget);
  expect(find.text('none yet'), findsOneWidget);
}

void main() {
  testWidgets('no pump selected: all sections show', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await startApp(tester);

    expectAllSections();
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton).first).onPressed,
      isNull,
    ); // Start test needs a pump
    expect(find.text('No pump selected'), findsNothing);
    expect(find.textContaining('No pumps yet'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await closeApp(tester);
  });

  testWidgets('pump selected but no data: all sections show "No data"', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({'selected_device_id': 'ESP32-002'});
    await startApp(tester);

    expectAllSections();
    expect(find.text('Sensor offline'), findsOneWidget); // vibration section
    expect(find.text('No data'), findsNWidgets(3)); // one chip per axis
    expect(find.text('Offline'), findsOneWidget); // the pump in Pump sensors
    expect(find.text('Start test'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await closeApp(tester);
  });
}

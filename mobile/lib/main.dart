import 'package:flutter/material.dart';

import 'app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  svc = Services();
  await svc.init();
  runApp(const NasconApp());
}

package com.nascon26.nascon_league

import android.content.ContentValues
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    // nascon:// link that launched the app (cold start); handed to Dart once.
    private var initialLink: String? = null
    private var linkChannel: MethodChannel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        initialLink = intent?.dataString
        super.onCreate(savedInstanceState)
    }

    // Warm start: the running activity (singleTop) receives the new link here.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val link = intent.dataString ?: return
        val channel = linkChannel
        if (channel == null) {
            initialLink = link
        } else {
            channel.invokeMethod("link", link)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        linkChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nascon/links").apply {
            setMethodCallHandler { call, result ->
                if (call.method == "initialLink") {
                    result.success(initialLink)
                    initialLink = null
                } else {
                    result.notImplemented()
                }
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nascon/files")
            .setMethodCallHandler { call, result ->
                if (call.method != "saveToDownloads") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                try {
                    val name = call.argument<String>("name") ?: "receipt.pdf"
                    val bytes = call.argument<ByteArray>("bytes") ?: ByteArray(0)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        val values = ContentValues().apply {
                            put(MediaStore.Downloads.DISPLAY_NAME, name)
                            put(MediaStore.Downloads.MIME_TYPE, "application/pdf")
                            put(MediaStore.Downloads.RELATIVE_PATH,
                                Environment.DIRECTORY_DOWNLOADS + "/NaSCon")
                        }
                        val uri = contentResolver.insert(
                            MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                            ?: throw IllegalStateException("insert failed")
                        contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
                        result.success("Downloads/NaSCon/$name")
                    } else {
                        val dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                        val f = File(dir, name)
                        f.writeBytes(bytes)
                        result.success(f.absolutePath)
                    }
                } catch (e: Exception) {
                    result.error("SAVE_FAILED", e.message, null)
                }
            }
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        linkChannel = null
        super.cleanUpFlutterEngine(flutterEngine)
    }
}

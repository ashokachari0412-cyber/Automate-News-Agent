package com.alfred.voice

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.util.Base64
import android.webkit.CookieManager
import android.webkit.HttpAuthHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

/** One app: the full Alfred AI Studio web app, with the hands-free voice assistant running in the background. */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private var loadedUrl = ""
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)

        web = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            webViewClient = object : WebViewClient() {
                override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, realm: String) {
                    handler.proceed("alfred", prefs.password) // the studio's APP_PASSWORD basic auth
                }
            }
            webChromeClient = object : WebChromeClient() {
                // Lets the chat's attach-file button open the phone's file picker.
                override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = callback
                    return try { startActivityForResult(params.createIntent(), FILE_REQUEST); true } catch (e: Exception) {
                        fileCallback = null; false
                    }
                }
            }
            setDownloadListener { url, _, disposition, mime, _ -> download(url, disposition, mime) }
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)

        // Settings: long-press the app icon -> Settings (no button over the chat).
        setContentView(web)

        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        startVoiceIfAllowed()
    }

    override fun onResume() {
        super.onResume()
        // Reload when the server URL was changed in Settings.
        if (prefs.serverUrl != loadedUrl) {
            loadedUrl = prefs.serverUrl
            web.loadUrl(loadedUrl)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else moveTaskToBack(true) // keep Alfred alive in the background
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == FILE_REQUEST) {
            fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
        } else super.onActivityResult(requestCode, resultCode, data)
    }

    /** "Hey Alfred" mode starts listening with the app; push-to-talk mode needs nothing running. */
    private fun startVoiceIfAllowed() {
        if (!prefs.voiceEnabled || !prefs.alwaysListen) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        startForegroundService(Intent(this, AlfredService::class.java))
    }

    private fun download(url: String, disposition: String?, mime: String?) {
        val name = URLUtil.guessFileName(url, disposition, mime)
        val req = DownloadManager.Request(Uri.parse(url))
            .setMimeType(mime)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        if (prefs.password.isNotEmpty()) {
            val token = Base64.encodeToString("alfred:${prefs.password}".toByteArray(), Base64.NO_WRAP)
            req.addRequestHeader("Authorization", "Basic $token")
        }
        getSystemService(DownloadManager::class.java).enqueue(req)
    }

    companion object {
        private const val FILE_REQUEST = 7
    }
}

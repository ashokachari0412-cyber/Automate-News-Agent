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
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
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

                // Instead of a silent black screen, say why the studio didn't load and offer a retry.
                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) failOver(error.description?.toString() ?: "network error")
                }

                override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                    if (request.isForMainFrame && response.statusCode >= 400) {
                        if (response.statusCode == 401) showOffline("wrong password (check Settings)")
                        else failOver("server error ${response.statusCode}")
                    }
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
        // Reload when the server URLs were changed in Settings.
        if (loadedUrl !in prefs.routes()) loadRoute(0)
    }

    private var routeIndex = 0

    private fun loadRoute(index: Int) {
        routeIndex = index
        loadedUrl = prefs.routes()[index]
        web.loadUrl(loadedUrl)
    }

    /** Main route failed: try the backup route before showing the offline screen. */
    private fun failOver(reason: String) {
        if (routeIndex + 1 < prefs.routes().size) loadRoute(routeIndex + 1)
        else showOffline(reason)
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

    private fun showOffline(reason: String) {
        val safe = android.text.Html.escapeHtml(reason)
        val server = android.text.Html.escapeHtml(prefs.routes().joinToString("  |  "))
        routeIndex = 0
        loadedUrl = prefs.serverUrl
        val html = """
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="background:#121214;color:#f6f6f6;font-family:sans-serif;padding:32px 20px;text-align:center">
              <h2 style="color:#d97757">Can't reach Alfred</h2>
              <p style="color:#9ea0a6">$safe</p>
              <p style="color:#65676e;font-size:13px;word-break:break-all">$server</p>
              <p style="color:#9ea0a6;font-size:14px">Check that the PC is on, the server is running and Tailscale is connected on this phone.</p>
              <button onclick="location.href='${android.text.Html.escapeHtml(prefs.serverUrl)}'" style="margin-top:16px;padding:12px 28px;border:0;border-radius:10px;background:#d97757;color:#fff;font-size:16px">Retry</button>
            </body></html>
        """.trimIndent()
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
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

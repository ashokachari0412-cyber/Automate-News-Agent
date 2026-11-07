package com.alfred.voice

import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
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
import android.widget.Toast
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** The Alfred AI Studio web app in a full-screen WebView (main route first, backup route if it fails).
 *  Also a share target: a post / link / screenshot shared to Alfred opens the studio, which reads it and asks
 *  what to make from it (image, avatar video or standalone video). */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private var loadedUrl = ""
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var shareQuery: String? = null // "share=...&share_image=..." until the studio page has loaded it

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)

        web = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.setSupportMultipleWindows(false) // target="_blank" links go through shouldOverrideUrlLoading
            webViewClient = object : WebViewClient() {
                // Links to other sites (e.g. "view" on a published post) open in their app or the browser,
                // so Alfred itself never navigates away.
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val host = request.url.host ?: return false
                    if (prefs.routes().any { Uri.parse(it).host == host }) return false
                    return try { startActivity(Intent(Intent.ACTION_VIEW, request.url)); true } catch (e: Exception) { false }
                }

                override fun onPageFinished(view: WebView, url: String) {
                    if (url.contains("share=") || url.contains("share_image=")) shareQuery = null
                }

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

        if (!handleShare(intent) && savedInstanceState != null) web.restoreState(savedInstanceState)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleShare(intent)
    }

    /** "Share -> Alfred" from Instagram, LinkedIn, the browser or the gallery. Returns true if it was a share. */
    private fun handleShare(intent: Intent?): Boolean {
        if (intent?.action != Intent.ACTION_SEND) return false
        val text = listOfNotNull(intent.getStringExtra(Intent.EXTRA_SUBJECT), intent.getStringExtra(Intent.EXTRA_TEXT))
            .joinToString("\n").trim()
        @Suppress("DEPRECATION")
        val image = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        val textPart = if (text.isNotEmpty()) "share=" + URLEncoder.encode(text, "UTF-8") else ""
        if (image == null || intent.type?.startsWith("image/") != true) {
            if (textPart.isEmpty()) return false
            openShare(textPart)
            return true
        }
        Toast.makeText(this, "Sending the screenshot to Alfred...", Toast.LENGTH_SHORT).show()
        Thread {
            val id = try { uploadImage(image) } catch (e: Exception) { null }
            runOnUiThread {
                if (id == null) Toast.makeText(this, "Couldn't send the screenshot", Toast.LENGTH_LONG).show()
                val parts = listOfNotNull(textPart.ifEmpty { null }, id?.let { "share_image=$it" })
                if (parts.isNotEmpty()) openShare(parts.joinToString("&")) else loadRoute(0)
            }
        }.start()
        return true
    }

    private fun openShare(query: String) {
        shareQuery = query
        loadRoute(0)
    }

    /** Uploads a shared image to the studio (main route, then backup) and returns its id. */
    private fun uploadImage(uri: Uri): String? {
        var bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return null
        var type = contentResolver.getType(uri) ?: "image/jpeg"
        if (bytes.size > 7 * 1024 * 1024) { // shrink very large images (the studio accepts up to 8 MB)
            val bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
            val out = ByteArrayOutputStream()
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 80, out)
            bytes = out.toByteArray(); type = "image/jpeg"
        }
        for (base in prefs.routes()) {
            try {
                val conn = URL("$base/api/v1/share/image").openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 15000
                conn.readTimeout = 30000
                conn.setRequestProperty("Content-Type", type)
                if (prefs.password.isNotEmpty()) {
                    val token = Base64.encodeToString("alfred:${prefs.password}".toByteArray(), Base64.NO_WRAP)
                    conn.setRequestProperty("Authorization", "Basic $token")
                }
                conn.outputStream.use { it.write(bytes) }
                if (conn.responseCode == 200) {
                    val body = conn.inputStream.bufferedReader().use { it.readText() }
                    return org.json.JSONObject(body).optString("id").ifEmpty { null }
                }
            } catch (e: Exception) { /* try the backup route */ }
        }
        return null
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
        // A pending share rides along until the studio page has loaded it (also on the backup route).
        web.loadUrl(shareQuery?.let { "$loadedUrl/?$it" } ?: loadedUrl)
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
        if (web.canGoBack()) web.goBack() else moveTaskToBack(true)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == FILE_REQUEST) {
            fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
        } else super.onActivityResult(requestCode, resultCode, data)
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

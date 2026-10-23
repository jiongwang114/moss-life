package com.moss.mobile

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.FrameLayout
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebMessage
import android.webkit.WebMessagePort
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import androidx.room.Room
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import org.json.JSONArray
import java.io.File

/** Android hosts the exact frontend bundled from moss-prototype by Gradle. */
class MainActivity : ComponentActivity() {
    lateinit var webView: WebView
        private set
    private lateinit var storage: NativeStorage
    private lateinit var database: MossDatabase
    private var saveReply: ((JSONObject) -> Unit)? = null
    private var saveFile: File? = null
    private var saveName: String = "records.zip"
    private var trustedOrigin = "https://note.jiongplay.cn"
    private var handlingBack = false
    private var legacyPort: WebMessagePort? = null
    private lateinit var updater: AppUpdater
    private var updatePreparation: Pair<String, CompletableDeferred<Unit>>? = null
    private val saveDocument = registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val reply = saveReply
        val file = saveFile
        if (uri == null) {
            finishSave(JSONObject().put("ok", false).put("name", "AbortError").put("error", "已取消保存"), reply)
        } else if (file == null || reply == null) {
            finishSave(JSONObject().put("ok", false).put("error", "保存流程已中断，请重新导出"), reply)
        } else lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val output = contentResolver.openOutputStream(uri, "wt") ?: error("系统未提供可写文件流")
                    output.use { target -> file.inputStream().use { it.copyTo(target) } }
                    val expected = file.inputStream().use { NativeStorage.digest(it) }
                    val actual = (contentResolver.openInputStream(uri) ?: error("无法核对保存结果"))
                        .use { NativeStorage.digest(it) }
                    check(expected == actual) { "文件写入校验失败" }
                    val actualName = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                        ?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null } ?: saveName
                    JSONObject().put("ok", true).put("value", JSONObject().put("uri", uri.toString()).put("name", actualName).put("sha256", actual))
                }.getOrElse { JSONObject().put("ok", false).put("error", it.message ?: "文件保存失败") }
            }
            finishSave(result, reply)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Only instrumented debug tests may override the origin/database; release always uses HTTPS.
        val verification = BuildConfig.DEBUG && intent.getBooleanExtra("moss.verification", false)
        if (verification) trustedOrigin = "http://127.0.0.1:8766"
        updater = AppUpdater(this, trustedOrigin) { prepareAppUpdate() }
        database = Room.databaseBuilder(applicationContext, MossDatabase::class.java,
            if (verification) "moss-verification.db" else "moss-mobile.db")
            .addMigrations(PENDING_RECORD_MIGRATION_1_2, WEB_STATE_MIGRATION_2_3).build()
        storage = NativeStorage(this, database, verification)
        window.statusBarColor = Color.rgb(247, 248, 241)
        window.navigationBarColor = Color.rgb(247, 248, 241)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        if (android.os.Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(true)
        webView = WebView(this)
        webView.setBackgroundColor(Color.rgb(247, 248, 241))
        val frame = FrameLayout(this)
        frame.addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        frame.setOnApplyWindowInsetsListener { view, insets ->
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                val keyboard = insets.getInsets(WindowInsets.Type.ime())
                view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard.bottom))
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop, insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        setContentView(frame)
        frame.requestApplyInsets()
        // API 23 WebMessagePort keeps Room, Keystore and system export available
        // on old WebViews too. Never fall back to storing app sessions in JS.
        val nativeBridgeAvailable = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) &&
            !(verification && intent.getBooleanExtra("moss.legacyPort", false))
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                legacyPort?.close()
                legacyPort = null
            }

            override fun onPageFinished(view: WebView, url: String) {
                val uri = Uri.parse(url)
                if (nativeBridgeAvailable || legacyPort != null || origin(uri) != trustedOrigin ||
                    uri.path.orEmpty() !in listOf("", "/", "/index.html")) return
                val ports = view.createWebMessageChannel()
                legacyPort = ports[0]
                ports[0].setWebMessageCallback(object : WebMessagePort.WebMessageCallback() {
                    override fun onMessage(port: WebMessagePort, message: WebMessage) {
                        if (legacyPort !== port) return
                        dispatchHostMessage(message.data) { response ->
                            if (legacyPort === port) port.postMessage(WebMessage(response))
                        }
                    }
                })
                // Transfer only to the trusted origin, never to '*' or an iframe.
                view.postWebMessage(WebMessage("moss-native-channel", arrayOf(ports[1])), Uri.parse(trustedOrigin))
            }

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val uri = request.url
                if (origin(uri) != trustedOrigin) return blocked()
                if (uri.path.orEmpty().startsWith("/api/v1/")) return null
                val path = uri.path.orEmpty().removePrefix("/").ifEmpty { "index.html" }
                // Never serve downloaded code with access to the native bridge.
                if (path !in bundledPaths) return blocked()
                return runCatching {
                    val type = when {
                        path.endsWith(".css") -> "text/css"
                        path.endsWith(".js") -> "application/javascript"
                        else -> "text/html"
                    }
                    WebResourceResponse(type, "UTF-8", 200, "OK", mapOf(
                        "Cache-Control" to "no-store",
                        "Content-Security-Policy" to "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'"
                    ), assets.open("web/$path"))
                }.getOrElse { blocked() }
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (origin(uri) == trustedOrigin && uri.path.orEmpty() in listOf("", "/", "/index.html")) return false
                if (request.isForMainFrame && uri.scheme in listOf("https", "http", "mailto")) {
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, uri)) }
                }
                return true
            }
        }
        if (nativeBridgeAvailable) {
            WebViewCompat.addWebMessageListener(webView, "MossHost", setOf(trustedOrigin)) { _, message, source, mainFrame, reply ->
                if (!mainFrame || origin(source) != trustedOrigin) return@addWebMessageListener
                dispatchHostMessage(message.data) { response -> reply.postMessage(response) }
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (handlingBack) return
                if (ViewCompat.getRootWindowInsets(webView)?.isVisible(WindowInsetsCompat.Type.ime()) == true) {
                    WindowInsetsControllerCompat(window, webView).hide(WindowInsetsCompat.Type.ime())
                    webView.evaluateJavascript("if (document.activeElement && document.activeElement.blur) document.activeElement.blur()", null)
                    return
                }
                handlingBack = true
                webView.evaluateJavascript("(window.mossHandleBack ? window.mossHandleBack() : false) === true") { handled ->
                    handlingBack = false
                    if (handled != "true") moveTaskToBack(true)
                }
            }
        })
        webView.loadUrl("$trustedOrigin/")
    }

    private fun dispatchHostMessage(data: String?, reply: (String) -> Unit) {
        val envelope = runCatching { JSONObject(data ?: "") }.getOrNull() ?: return
        val id = envelope.optString("id")
        val respond: (JSONObject) -> Unit = { response -> reply(response.put("id", id).toString()) }
        when (envelope.optString("method")) {
            "saveExport" -> beginSave(envelope, respond)
            "appInfo" -> respond(JSONObject().put("ok", true).put("value", JSONObject()
                .put("version", BuildConfig.VERSION_NAME).put("versionCode", BuildConfig.VERSION_CODE)))
            "checkAppUpdate" -> {
                updater.check(envelope.optJSONObject("params")?.optBoolean("manual", false) == true)
                respond(JSONObject().put("ok", true))
            }
            "appUpdatePrepared" -> {
                val params = envelope.optJSONObject("params")
                updatePreparation?.let { (token, deferred) ->
                    if (params?.optString("token") == token) {
                        if (params.optBoolean("ok")) deferred.complete(Unit)
                        else deferred.completeExceptionally(IllegalStateException(params.optString("error", "请先保存当前内容")))
                    }
                }
                respond(JSONObject().put("ok", true))
            }
            "backgroundApp" -> { respond(JSONObject().put("ok", true)); moveTaskToBack(true) }
            else -> lifecycleScope.launch {
                val response = runCatching { storage.handle(envelope) }
                    .getOrElse { JSONObject().put("ok", false).put("error", it.message ?: "手机存储操作失败") }
                respond(response)
            }
        }
    }

    private suspend fun prepareAppUpdate() {
        val token = java.util.UUID.randomUUID().toString()
        val completion = CompletableDeferred<Unit>()
        updatePreparation = token to completion
        try {
            webView.evaluateJavascript("""
                Promise.resolve().then(function () {
                    if (!window.mossPrepareAppUpdate) throw new Error('页面尚未就绪，请稍后重试');
                    return window.mossPrepareAppUpdate();
                }).then(function () {
                    return MossNative.call('appUpdatePrepared', {token:'$token',ok:true});
                }, function (error) {
                    return MossNative.call('appUpdatePrepared', {token:'$token',ok:false,error:error.message});
                });
            """.trimIndent(), null)
            check(withTimeoutOrNull(10000) { completion.await(); true } == true) { "保存草稿超时，请确认内容后重试" }
        } finally { updatePreparation = null }
    }

    private fun beginSave(envelope: JSONObject, reply: (JSONObject) -> Unit) {
        if (saveReply != null) { reply(JSONObject().put("ok", false).put("error", "已有文件等待选择位置")); return }
        saveReply = reply
        saveName = envelope.optJSONObject("params")?.optString("name")?.substringAfterLast('/')?.substringAfterLast('\\') ?: "records.zip"
        lifecycleScope.launch {
            runCatching {
                saveFile = withContext(Dispatchers.IO) {
                    val bytes = android.util.Base64.decode(envelope.getJSONObject("params").getString("base64"), android.util.Base64.DEFAULT)
                    File.createTempFile("moss-export-", ".zip", cacheDir).apply { outputStream().use { it.write(bytes) } }
                }
                saveDocument.launch(saveName)
            }.onFailure { finishSave(JSONObject().put("ok", false).put("error", it.message ?: "无法打开系统保存窗口"), reply) }
        }
    }

    private fun finishSave(result: JSONObject, reply: ((JSONObject) -> Unit)?) {
        saveReply = null
        saveFile?.delete()
        saveFile = null
        reply?.invoke(result)
    }

    override fun onDestroy() {
        updater.close()
        legacyPort?.close()
        legacyPort = null
        (webView.parent as? ViewGroup)?.removeView(webView)
        webView.destroy()
        saveFile?.delete()
        database.close()
        super.onDestroy()
    }

    private fun origin(uri: Uri): String = "${uri.scheme}://${uri.encodedAuthority}"
    private fun blocked() = WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", emptyMap(), "Blocked".byteInputStream())
    private val bundledPaths by lazy {
        val manifest = assets.open("web/assets.json").bufferedReader().use { JSONArray(it.readText()) }
        (0 until manifest.length()).map { manifest.getString(it) }.toSet()
    }
}

package com.moss.mobile

import android.app.AlertDialog
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import android.provider.Settings
import android.widget.ProgressBar
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Downloads only after consent. All installer and unknown-source decisions belong to Android. */
class AppUpdater(
    private val activity: ComponentActivity,
    private val apiOrigin: String,
    private val beforeInstall: suspend () -> Unit
) {
    private var checking = false
    private var automaticChecked = false
    private var dialog: AlertDialog? = null
    private var downloadJob: Job? = null
    @Volatile private var downloadConnection: HttpURLConnection? = null
    private var installJob: Job? = null
    private var pendingRelease: AndroidRelease? = null
    private val directory = File(activity.cacheDir, "app-updates")
    private val preferences = activity.getSharedPreferences("app-updates", 0)
    private val permission = activity.registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (activity.packageManager.canRequestPackageInstalls()) {
            val release = pendingRelease ?: runCatching {
                parseRelease(JSONObject(preferences.getString("pendingRelease", null) ?: error("没有待安装更新")))
            }.getOrNull()
            if (release != null) prepareInstallation(release)
            else showMessage("请重新检查更新", "安装流程已中断，请在设置中重新检查更新。")
        } else showMessage("尚未允许安装", "本次没有安装。你可以稍后在设置中检查更新并重试。")
    }

    fun check(manual: Boolean) {
        if (!manual && automaticChecked) return
        if (!manual) automaticChecked = true
        if (checking || downloadJob?.isCompleted == false || installJob?.isCompleted == false || dialog?.isShowing == true) {
            if (manual) toast("更新操作正在进行")
            return
        }
        checking = true
        if (manual) toast("正在检查更新…")
        activity.lifecycleScope.launch {
            try {
                val release = withContext(Dispatchers.IO) { readRelease() }
                if (!activity.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) return@launch
                if (release == null) {
                    if (manual) showMessage("检查更新", "暂未发布可安装的新版本。")
                } else if (!release.isNewerThan(BuildConfig.VERSION_CODE.toLong())) {
                    if (manual) showMessage("检查更新", "当前已是最新版本（${BuildConfig.VERSION_NAME}）。")
                } else if (release.minSdk > Build.VERSION.SDK_INT) {
                    if (manual) showMessage("暂时无法升级", "新版需要 Android API ${release.minSdk} 或以上；当前版本仍可继续使用。")
                } else if (manual || preferences.getLong("skippedCode", 0) != release.versionCode ||
                    System.currentTimeMillis() - preferences.getLong("skippedAt", 0) >= 24 * 60 * 60 * 1000L) {
                    offer(release)
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                // Failed background checks must not obstruct recording or imply "up to date".
                if (manual) showMessage("检查失败", if (error is UpdateServiceException) error.message.orEmpty()
                    else "暂时无法连接更新服务，请稍后重试。当前版本仍可继续使用。")
            } finally { checking = false }
        }
    }

    private fun readRelease(): AndroidRelease? {
        val connection = URL("$apiOrigin/api/v1/app-updates/android").openConnection() as HttpURLConnection
        connection.connectTimeout = 10000
        connection.readTimeout = 10000
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("Accept", "application/json")
        try {
            val status = connection.responseCode
            if (status == 404) throw UpdateServiceException("更新服务尚未启用，请稍后再试。当前版本仍可继续使用。")
            if (status != 200) throw UpdateServiceException("更新服务暂时不可用，请稍后再试。当前版本仍可继续使用。")
            val raw = connection.inputStream.use { it.readBytesLimited(32768) }
            return try {
                val response = JSONObject(String(raw, Charsets.UTF_8))
                check(response.has("release")) { "版本信息不完整" }
                if (response.isNull("release")) null else parseRelease(response.getJSONObject("release"))
            } catch (_: Exception) {
                throw UpdateServiceException("更新信息异常，请稍后再试。当前版本仍可继续使用。")
            }
        } finally { connection.disconnect() }
    }

    private fun parseRelease(value: JSONObject): AndroidRelease {
        val notes = value.getJSONArray("notes")
        return AndroidRelease(value.getString("package_name"), value.getString("version_name"),
            value.getLong("version_code"), value.getInt("min_sdk"), value.getString("download_url"),
            value.getLong("size_bytes"), value.getString("sha256"),
            (0 until notes.length()).map { notes.getString(it) }).validate()
    }

    private fun rememberPending(release: AndroidRelease) {
        val value = JSONObject().put("package_name", release.packageName).put("version_name", release.versionName)
            .put("version_code", release.versionCode).put("min_sdk", release.minSdk)
            .put("download_url", release.downloadUrl).put("size_bytes", release.sizeBytes)
            .put("sha256", release.sha256).put("notes", org.json.JSONArray(release.notes))
        preferences.edit().putString("pendingRelease", value.toString()).apply()
    }

    private fun offer(release: AndroidRelease) {
        val megabytes = String.format(java.util.Locale.ROOT, "%.1f", release.sizeBytes / 1048576.0)
        dialog = AlertDialog.Builder(activity).setTitle("发现新版本 ${release.versionName}")
            .setMessage("当前版本 ${BuildConfig.VERSION_NAME} · 下载约 ${megabytes} MB\n\n" +
                release.notes.joinToString("\n") { "• $it" } + "\n\n下载完成后，需要在系统窗口中确认安装。")
            .setNegativeButton("暂不更新") { _, _ -> skip(release) }
            .setPositiveButton("立即更新") { _, _ -> download(release) }
            .setOnCancelListener { skip(release) }.show()
    }

    private fun skip(release: AndroidRelease) {
        preferences.edit().putLong("skippedCode", release.versionCode).putLong("skippedAt", System.currentTimeMillis()).apply()
    }

    private fun download(release: AndroidRelease) {
        if (downloadJob?.isCompleted == false) return
        val progress = ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100; isIndeterminate = false; setPadding(40, 20, 40, 20)
        }
        val progressDialog = AlertDialog.Builder(activity).setTitle("正在下载 ${release.versionName}")
            .setMessage("下载完成后将打开系统安装窗口。")
            .setView(progress).setNegativeButton("取消下载") { _, _ -> cancelDownload() }
            .setCancelable(false).show()
        dialog = progressDialog
        downloadJob = activity.lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { downloadAndVerify(release) { value ->
                    withContext(Dispatchers.Main) { progress.progress = value }
                } }
                progressDialog.dismiss()
                pendingRelease = release
                rememberPending(release)
                prepareInstallation(release)
            } catch (error: CancellationException) {
                toast("已取消下载")
                throw error
            } catch (error: Exception) {
                progressDialog.dismiss()
                dialog = AlertDialog.Builder(activity).setTitle("未能完成更新")
                    .setMessage(error.message ?: "下载失败，请稍后重试。")
                    .setNegativeButton("稍后再说", null)
                    .setPositiveButton("重试") { _, _ -> download(release) }.show()
            } finally { progressDialog.dismiss() }
        }
    }

    private suspend fun downloadAndVerify(release: AndroidRelease, onProgress: suspend (Int) -> Unit) {
        directory.mkdirs()
        val file = apkFile(release)
        if (file.isFile) {
            try { verify(file, release); return } catch (_: Exception) { file.delete() }
        }
        directory.listFiles()?.filter { it.name != file.name }?.forEach { it.delete() }
        val partial = File(directory, "${release.versionCode}.part.apk")
        var connection: HttpURLConnection? = null
        try {
            var address = release.downloadUrl
            var ready = false
            // GitHub assets redirect to HTTPS object storage. Never forward session headers.
            for (redirect in 0..5) {
                currentCoroutineContext().ensureActive()
                AndroidRelease.requireHttps(address)
                val next = URL(address).openConnection() as HttpURLConnection
                connection = next
                downloadConnection = next
                currentCoroutineContext().ensureActive()
                next.connectTimeout = 15000; next.readTimeout = 20000
                next.instanceFollowRedirects = false
                next.setRequestProperty("Accept", "application/vnd.android.package-archive")
                val status = next.responseCode
                if (status in listOf(301, 302, 303, 307, 308)) {
                    check(redirect < 5) { "下载地址跳转过多，请稍后重试" }
                    val location = next.getHeaderField("Location") ?: error("下载地址不可用")
                    address = URL(URL(address), location).toString()
                    next.disconnect()
                } else {
                    check(status == 200) { "下载失败，请确认网络后重试" }
                    ready = true; break
                }
            }
            check(ready) { "下载地址不可用" }
            val response = connection ?: error("下载连接失败")
            val length = response.contentLengthLong
            check(length == -1L || length == release.sizeBytes) { "安装包大小与发布信息不符" }
            val started = android.os.SystemClock.elapsedRealtime()
            var received = 0L
            var previous = -1
            response.inputStream.use { input -> partial.outputStream().use { output ->
                val buffer = ByteArray(32768)
                while (true) {
                    currentCoroutineContext().ensureActive()
                    check(android.os.SystemClock.elapsedRealtime() - started < 10 * 60 * 1000L) { "下载超时，请重试" }
                    val count = input.read(buffer)
                    currentCoroutineContext().ensureActive()
                    if (count == -1) break
                    received += count
                    check(received <= release.sizeBytes) { "安装包大小与发布信息不符" }
                    output.write(buffer, 0, count)
                    val percent = (received * 100 / release.sizeBytes).toInt()
                    if (percent != previous) { onProgress(percent); previous = percent }
                }
            } }
            verify(partial, release)
            currentCoroutineContext().ensureActive()
            check(partial.renameTo(file)) { "无法保存安装包，请重试" }
        } finally { connection?.disconnect(); downloadConnection = null; partial.delete() }
    }

    private fun cancelDownload() {
        val connection = downloadConnection
        downloadJob?.cancel()
        // Cancelling a coroutine alone cannot unblock HttpURLConnection's socket read.
        // Disconnect off the main thread; do not allow another transfer until cleanup ends.
        activity.lifecycleScope.launch(Dispatchers.IO) { connection?.disconnect() }
    }

    @Suppress("DEPRECATION")
    private fun verify(file: File, release: AndroidRelease) {
        check(file.length() == release.sizeBytes) { "安装包下载不完整，请重试" }
        val digest = file.inputStream().use { NativeStorage.digest(it) }
        check(digest == release.sha256) { "安装包校验失败，请重新下载" }
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val manager = activity.packageManager
        val archive = manager.getPackageArchiveInfo(file.absolutePath, flags) ?: error("无法识别安装包")
        val installed = manager.getPackageInfo(activity.packageName, flags)
        val code = if (Build.VERSION.SDK_INT >= 28) archive.longVersionCode else archive.versionCode.toLong()
        check(archive.packageName == activity.packageName && code == release.versionCode &&
            archive.versionName == release.versionName && release.isNewerThan(BuildConfig.VERSION_CODE.toLong())) { "安装包版本或应用标识不符" }
        check(archive.applicationInfo?.minSdkVersion == release.minSdk && release.minSdk <= Build.VERSION.SDK_INT) { "安装包不适合当前手机" }
        check((archive.applicationInfo!!.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) { "不能安装测试安装包" }
        fun certificates(info: PackageInfo): Set<String> {
            val signatures = if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners else info.signatures
            return signatures.orEmpty().map { signatureDigest(it.toByteArray()) }.toSet()
        }
        val original = certificates(installed)
        val candidate = certificates(archive)
        val rotated = if (Build.VERSION.SDK_INT >= 28 && archive.signingInfo?.hasMultipleSigners() == false) {
            archive.signingInfo?.signingCertificateHistory.orEmpty().map { signatureDigest(it.toByteArray()) }.toSet()
        } else emptySet()
        check(original.isNotEmpty() && candidate.isNotEmpty() &&
            (original == candidate || original.size == 1 && rotated.containsAll(original))) { "安装包签名不匹配，已停止更新" }
    }

    private fun prepareInstallation(release: AndroidRelease) {
        if (installJob?.isActive == true) return
        val preparing = AlertDialog.Builder(activity).setTitle("正在准备安装")
            .setMessage("正在校验安装包并保存当前草稿…").setCancelable(false).show()
        dialog = preparing
        installJob = activity.lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { verify(apkFile(release), release) }
                beforeInstall()
                preparing.dismiss()
                if (!activity.packageManager.canRequestPackageInstalls()) {
                    dialog = AlertDialog.Builder(activity).setTitle("允许 MOSS 安装更新")
                        .setMessage("手机需要你允许 MOSS 安装应用。允许后返回，将继续打开系统安装窗口；你仍可取消。")
                        .setNegativeButton("暂不安装", null).setPositiveButton("去允许") { _, _ ->
                            runCatching { permission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:${activity.packageName}"))) }
                                .onFailure { showMessage("无法打开系统设置", "请在手机设置中允许 MOSS 安装应用，然后重新检查更新。") }
                        }.show()
                } else {
                    val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.updates", apkFile(release))
                    activity.startActivity(Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        clipData = android.content.ClipData.newRawUri("MOSS update", uri)
                    })
                }
            } catch (error: CancellationException) { throw error
            } catch (error: Exception) {
                showMessage("尚未安装", error.message ?: "无法打开安装窗口，请稍后重试。")
            } finally { preparing.dismiss() }
        }
    }

    private fun apkFile(release: AndroidRelease) = File(directory, "moss-${release.versionCode}.apk")
    private fun signatureDigest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    private fun toast(text: String) = Toast.makeText(activity, text, Toast.LENGTH_SHORT).show()
    private fun showMessage(title: String, message: String) {
        dialog?.dismiss()
        if (!activity.isFinishing && !activity.isDestroyed) dialog = AlertDialog.Builder(activity)
            .setTitle(title).setMessage(message).setPositiveButton("知道了", null).show()
    }
    fun close() { cancelDownload(); dialog?.dismiss() }

    private class UpdateServiceException(message: String) : Exception(message)

    private fun java.io.InputStream.readBytesLimited(limit: Int): ByteArray {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val count = read(buffer)
            if (count == -1) break
            check(output.size() + count <= limit) { "版本信息过大" }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
}

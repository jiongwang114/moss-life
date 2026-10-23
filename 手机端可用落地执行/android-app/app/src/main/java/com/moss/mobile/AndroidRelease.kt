package com.moss.mobile

import java.net.URI

/** Public release contract; versionCode is monotonic, display versions are never ordered. */
data class AndroidRelease(
    val packageName: String, val versionName: String, val versionCode: Long,
    val minSdk: Int, val downloadUrl: String, val sizeBytes: Long,
    val sha256: String, val notes: List<String>
) {
    fun validate(): AndroidRelease {
        require(packageName == "com.moss.mobile") { "安装包应用标识不正确" }
        require(Regex("\\d{1,5}\\.\\d{1,5}\\.\\d{1,5}").matches(versionName)) { "版本信息不完整" }
        require(versionCode in 1..2100000000L && minSdk in 26..100) { "版本信息不完整" }
        require(sizeBytes in 1..MAX_APK_BYTES && Regex("[0-9a-f]{64}").matches(sha256)) { "安装包校验信息不完整" }
        require(notes.size in 1..20 && notes.all { it.isNotBlank() && it.length <= 300 }) { "更新说明不完整" }
        require(downloadUrl.length <= 2048 && downloadUrl.none { it.isWhitespace() || it.code < 32 }) { "下载地址无效" }
        requireHttps(downloadUrl)
        return this
    }

    fun isNewerThan(installedCode: Long) = versionCode > installedCode

    companion object {
        const val MAX_APK_BYTES = 200L * 1024 * 1024
        fun requireHttps(value: String): URI {
            val uri = URI(value)
            require(uri.scheme == "https" && !uri.host.isNullOrEmpty() &&
                uri.rawUserInfo == null && uri.rawFragment == null && uri.port in listOf(-1, 443)) { "下载地址必须使用 HTTPS" }
            return uri
        }
    }
}

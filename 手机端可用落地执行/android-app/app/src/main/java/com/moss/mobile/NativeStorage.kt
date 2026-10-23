package com.moss.mobile

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.room.withTransaction
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.json.JSONTokener
import java.io.InputStream
import java.security.KeyStore
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Durable acknowledgements come after Room commits; auth values use Android Keystore encryption. */
class NativeStorage(private val context: Context, private val database: MossDatabase, private val verification: Boolean) {
    private val dao get() = database.webStateDao()
    private suspend fun read(key: String): String? = dao.get(key)?.let { if (key == "session") decrypt(it) else it }
    private suspend fun write(key: String, value: String) = dao.put(WebState(key, if (key == "session") encrypt(value) else value))

    suspend fun handle(envelope: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        val params = envelope.optJSONObject("params") ?: JSONObject()
        if (envelope.getString("method") == "purgeAccount") {
            val user = params.getString("user_id")
            require(user.matches(Regex("[a-f0-9-]{36}")))
            database.withTransaction {
                val pending = read("user:$user:submission")?.let { runCatching { JSONObject(it) }.getOrNull() }
                pending?.optString("record_id")?.let { database.pendingRecordDao().delete(it) }
                database.openHelper.writableDatabase.execSQL("DELETE FROM web_state WHERE key LIKE ?", arrayOf("user:$user:%"))
            }
            return@withContext JSONObject().put("ok", true).put("value", JSONObject.NULL)
        }
        val key = params.optString("key")
        require(key.matches(Regex("[a-zA-Z0-9:_-]{1,120}"))) { "不支持的存储键" }
        when (envelope.getString("method")) {
            "get" -> {
                if (!verification && key == "session" && dao.get("session") == null) migrateSession()
                if (!verification && key == "submission" && dao.get("submission") == null) migratePending()
                JSONObject().put("ok", true).put("value", read(key)?.let { JSONTokener(it).nextValue() } ?: JSONObject.NULL)
            }
            "put" -> {
                database.withTransaction {
                    val previous = if (key == "submission") read(key)?.let { runCatching { JSONObject(it) }.getOrNull() } else null
                    val value = params.opt("value")
                    write(key, if (value is String) JSONObject.quote(value) else value?.toString() ?: "null")
                    if (key == "submission" && params.isNull("value")) previous?.optString("record_id")?.let { database.pendingRecordDao().delete(it) }
                }
                JSONObject().put("ok", true).put("value", JSONObject.NULL)
            }
            else -> error("不支持的手机操作")
        }
    }

    private suspend fun migrateSession() {
        val old = context.getSharedPreferences("moss-session", Context.MODE_PRIVATE)
        val access = old.getString("access", null) ?: return
        val refresh = old.getString("refresh", null) ?: return
        write("session", JSONObject().put("access_token", access).put("refresh_token", refresh).toString())
        // Remove plaintext only after the encrypted Room value is committed.
        old.edit().clear().commit()
    }

    private suspend fun migratePending() {
        val old = database.pendingRecordDao().latest() ?: return
        val stamp = if (old.firstSentAt.isNotBlank()) java.time.OffsetDateTime.parse(old.firstSentAt)
            else Instant.ofEpochMilli(old.updatedAt).atZone(ZoneId.systemDefault()).toOffsetDateTime()
        val status = when (old.status) {
            "local_only", "local_pending", "not_accepted" -> "local_only"
            else -> "unknown"
        }
        write("submission", JSONObject().put("record_id", old.id).put("raw_input", old.rawInput)
            .put("first_sent_at", stamp.toString()).put("record_date", old.recordDate.ifBlank { stamp.toLocalDate().toString() })
            .put("sent_utc_offset_minutes", if (old.firstSentAt.isBlank()) stamp.offset.totalSeconds / 60 else old.offsetMinutes)
            .put("delivery_state", status).put("modifiable_rejection", false).put("last_known_revision", JSONObject.NULL).toString())
    }

    private fun key(): SecretKey {
        val alias = if (verification) "moss-verification-session" else "moss-web-session"
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
    }

    private fun decrypt(value: String): String {
        val bytes = Base64.decode(value, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12))) }
        return cipher.doFinal(bytes.copyOfRange(12, bytes.size)).toString(Charsets.UTF_8)
    }

    companion object {
        fun digest(input: InputStream): String {
            val digest = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(8192)
            while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}

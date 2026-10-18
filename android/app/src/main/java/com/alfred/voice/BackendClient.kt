package com.alfred.voice

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class VoiceReply(
    val speak: String,
    val actionType: String,
    val contact: String,
    val message: String,
    val needsConfirmation: Boolean,
)

/** Talks to the FastAPI backend: POST /api/v1/voice/command */
class BackendClient(private val prefs: Prefs) {
    private val http = OkHttpClient.Builder().callTimeout(25, TimeUnit.SECONDS).build()
    private val history = ArrayDeque<JSONObject>()

    suspend fun command(transcript: String): VoiceReply = withContext(Dispatchers.IO) {
        val nav = JSONObject()
            .put("title", NavState.title).put("text", NavState.text).put("subtext", NavState.subtext)
            .put("age_seconds", NavState.ageSeconds() ?: JSONObject.NULL)
        val body = JSONObject()
            .put("transcript", transcript).put("nav", nav).put("history", JSONArray(history.toList()))

        val req = Request.Builder()
            .url("${prefs.serverUrl}/api/v1/voice/command")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .apply {
                if (prefs.password.isNotEmpty()) {
                    val token = Base64.encodeToString("alfred:${prefs.password}".toByteArray(), Base64.NO_WRAP)
                    header("Authorization", "Basic $token")
                }
            }.build()

        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) error("Server returned ${resp.code}")
            val j = JSONObject(resp.body!!.string())
            val a = j.optJSONObject("action") ?: JSONObject()
            val reply = VoiceReply(
                j.optString("speak"), a.optString("type", "none"), a.optString("contact"),
                a.optString("message"), j.optBoolean("needs_confirmation"),
            )
            remember("user", transcript)
            remember("assistant", reply.speak)
            reply
        }
    }

    private fun remember(role: String, content: String) {
        history.addLast(JSONObject().put("role", role).put("content", content))
        while (history.size > 6) history.removeFirst()
    }
}

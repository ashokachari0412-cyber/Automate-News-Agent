package com.alfred.voice

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.text.InputType
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Setup: server, password, permissions, start/stop the voice assistant. */
class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private var tts: TextToSpeech? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)

        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 48, 48, 48) }
        fun label(t: String) = col.addView(TextView(this).apply { text = t; textSize = 14f; setPadding(0, 24, 0, 4) })
        fun field(value: String, secret: Boolean = false) = EditText(this).apply {
            setText(value)
            if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            col.addView(this, MATCH_PARENT, -2)
        }
        fun button(t: String, onClick: () -> Unit) =
            col.addView(Button(this).apply { text = t; setOnClickListener { onClick() } }, MATCH_PARENT, -2)

        label("Server URL (your PC or tunnel)")
        val url = field(prefs.serverUrl)
        label("App password (APP_PASSWORD in .env, blank if none)")
        val pwd = field(prefs.password, secret = true)
        val headset = CheckBox(this).apply {
            text = "Use Bluetooth headset mic (needed on bike; music becomes call quality)"
            isChecked = prefs.useHeadsetMic
        }
        col.addView(headset)
        val always = CheckBox(this).apply {
            text = "Always listen for \"Hey Alfred\" (uses more battery). Off = hold the power button to talk"
            isChecked = prefs.alwaysListen
        }
        col.addView(always)

        label("Talk to Alfred by holding the power button")
        button("Make Alfred the default digital assistant") {
            startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
        }

        label("Alfred's voice (Indian English)")
        button("Change voice and preview") { nextVoice() }

        label("One-time permissions")
        button("1. Grant mic / SMS / contacts") {
            requestPermissions(arrayOf(
                Manifest.permission.RECORD_AUDIO, Manifest.permission.SEND_SMS, Manifest.permission.READ_CONTACTS,
                Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.POST_NOTIFICATIONS,
            ), 1)
        }
        button("2. Allow reading Google Maps notifications") {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        button("3. (Optional) Enable WhatsApp auto-send") {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        label("")
        button("Save and start Alfred Voice") {
            prefs.voiceEnabled = true
            prefs.alwaysListen = always.isChecked
            prefs.serverUrl = url.text.toString()
            prefs.password = pwd.text.toString()
            prefs.useHeadsetMic = headset.isChecked
            stopService(Intent(this, AlfredService::class.java))
            if (prefs.alwaysListen) startForegroundService(Intent(this, AlfredService::class.java))
            else android.widget.Toast.makeText(this, "Saved. Hold the power button to talk to Alfred.", android.widget.Toast.LENGTH_LONG).show()
        }
        button("Stop Alfred Voice") {
            prefs.voiceEnabled = false
            stopService(Intent(this, AlfredService::class.java))
        }
        button("Back to Studio") { finish() }

        setContentView(ScrollView(this).apply { addView(col) })
    }

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }

    /** Cycles through the phone's Indian-English voices and says a sample. Tap "Start Alfred Voice" to use it. */
    private fun nextVoice() {
        val engine = tts ?: TextToSpeech(this) { if (it == TextToSpeech.SUCCESS) nextVoice() }.also { tts = it; return }
        val voices = VoicePicker.indianVoices(engine)
        if (voices.isEmpty()) {
            engine.speak("No Indian English voice is installed. Please add one in Google text to speech settings, sir.",
                TextToSpeech.QUEUE_FLUSH, null, "preview")
            return
        }
        val i = voices.indexOfFirst { it.name == prefs.voiceName }
        val next = voices[(i + 1) % voices.size]
        prefs.voiceName = next.name
        engine.voice = next
        engine.setSpeechRate(0.95f)
        engine.speak("Hello sir, voice ${(i + 1) % voices.size + 1} of ${voices.size}. Shall I sound like this?",
            TextToSpeech.QUEUE_FLUSH, null, "preview")
    }
}

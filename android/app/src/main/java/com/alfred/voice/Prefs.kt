package com.alfred.voice

import android.content.Context

/** Simple settings store: backend URL and app password. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("alfred", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("serverUrl", "https://desktop-tvjj8s1.tailc3cb6c.ts.net")!!
        set(v) = sp.edit().putString("serverUrl", v.trim().trimEnd('/')).apply()

    var password: String
        get() = sp.getString("password", "")!!
        set(v) = sp.edit().putString("password", v).apply()

    /** Hands-free voice assistant starts with the app unless switched off. */
    var voiceEnabled: Boolean
        get() = sp.getBoolean("voiceEnabled", true)
        set(v) = sp.edit().putBoolean("voiceEnabled", v).apply()

    /** Listen for "Hey Alfred" all the time. Off = push-to-talk (hold power button / headset button). */
    var alwaysListen: Boolean
        get() = sp.getBoolean("alwaysListen", false)
        set(v) = sp.edit().putBoolean("alwaysListen", v).apply()

    /** Chosen text-to-speech voice name ("" = best Indian-English voice). */
    var voiceName: String
        get() = sp.getString("voiceName", "")!!
        set(v) = sp.edit().putString("voiceName", v).apply()

    /** Listen through the Bluetooth headset mic. Needed on a bike, but drops music to call quality. */
    var useHeadsetMic: Boolean
        get() = sp.getBoolean("useHeadsetMic", true)
        set(v) = sp.edit().putBoolean("useHeadsetMic", v).apply()
}

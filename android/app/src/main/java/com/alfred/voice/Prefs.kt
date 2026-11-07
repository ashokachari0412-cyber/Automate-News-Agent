package com.alfred.voice

import android.content.Context

/** Simple settings store: backend URL and app password. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("alfred", Context.MODE_PRIVATE)

    /** Main route: the PC's fixed Tailscale IP (direct, encrypted by Tailscale; no DNS / Funnel needed). */
    var serverUrl: String
        get() = sp.getString("serverUrl", "http://100.88.12.72:8000")!!
        set(v) = sp.edit().putString("serverUrl", v.trim().trimEnd('/')).apply()

    /** Backup route, used automatically when the main one fails: the public Tailscale Funnel address. */
    var fallbackUrl: String
        get() = sp.getString("fallbackUrl", "https://desktop-tvjj8s1.tailc3cb6c.ts.net")!!
        set(v) = sp.edit().putString("fallbackUrl", v.trim().trimEnd('/')).apply()

    /** Main route first, then the backup (if set and different). */
    fun routes(): List<String> = listOf(serverUrl, fallbackUrl).filter { it.isNotBlank() }.distinct()

    var password: String
        get() = sp.getString("password", "")!!
        set(v) = sp.edit().putString("password", v).apply()
}

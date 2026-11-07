package com.alfred.voice

import android.app.Activity
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Setup: where the studio runs (main + backup URL) and the app password. Long-press the app icon -> Settings. */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)

        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 48, 48, 48) }
        fun label(t: String) = col.addView(TextView(this).apply { text = t; textSize = 14f; setPadding(0, 24, 0, 4) })
        fun field(value: String, secret: Boolean = false) = EditText(this).apply {
            setText(value)
            if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            col.addView(this, MATCH_PARENT, -2)
        }
        fun button(t: String, onClick: () -> Unit) =
            col.addView(Button(this).apply { text = t; setOnClickListener { onClick() } }, MATCH_PARENT, -2)

        label("Server URL (main route: PC's Tailscale IP)")
        val url = field(prefs.serverUrl)
        label("Backup URL (used automatically if the main one fails)")
        val backup = field(prefs.fallbackUrl)
        label("App password (APP_PASSWORD in .env, blank if none)")
        val pwd = field(prefs.password, secret = true)

        label("")
        button("Save") {
            prefs.serverUrl = url.text.toString()
            prefs.fallbackUrl = backup.text.toString()
            prefs.password = pwd.text.toString()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            finish()
        }
        button("Back to Studio") { finish() }

        setContentView(ScrollView(this).apply { addView(col) })
    }
}

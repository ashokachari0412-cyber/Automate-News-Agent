package com.alfred.voice

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Opened by Android when Alfred is the default digital assistant: holding the power button,
 * or long-pressing the Bluetooth headset button. Starts one voice conversation and disappears.
 */
class AssistActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true) // works from the lock screen too
        startForegroundService(Intent(this, AlfredService::class.java).setAction(AlfredService.ACTION_SESSION))
        finish()
        overridePendingTransition(0, 0)
    }
}

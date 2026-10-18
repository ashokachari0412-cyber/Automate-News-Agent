package com.alfred.voice

import android.accessibilityservice.AccessibilityService
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/** Presses WhatsApp's Send button once Alfred has opened a chat with the message pre-filled. */
class WhatsAppSender : AccessibilityService() {
    companion object {
        @Volatile var pendingSend = false
        @Volatile var isEnabled = false
    }

    override fun onServiceConnected() { isEnabled = true }
    override fun onDestroy() { isEnabled = false; super.onDestroy() }
    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (!pendingSend) return
        val root = rootInActiveWindow ?: return
        val send = root.findAccessibilityNodeInfosByViewId("${root.packageName}:id/send").firstOrNull() ?: return
        if (send.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            pendingSend = false
            // Go back to the previous app (Maps) after sending.
            Handler(Looper.getMainLooper()).postDelayed({ performGlobalAction(GLOBAL_ACTION_BACK) }, 600)
        }
    }
}

package com.alfred.voice

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/** Latest Google Maps turn-by-turn notification, e.g. title "12 km · 18 min", text "Turn left onto MG Road". */
object NavState {
    @Volatile var title = ""
    @Volatile var text = ""
    @Volatile var subtext = ""
    @Volatile var updatedAt = 0L

    fun clear() { title = ""; text = ""; subtext = ""; updatedAt = 0L }
    fun ageSeconds(): Int? = if (updatedAt == 0L) null else ((System.currentTimeMillis() - updatedAt) / 1000).toInt()
}

class NavListener : NotificationListenerService() {
    private val mapsPackages = setOf("com.google.android.apps.maps", "com.google.android.apps.mapslite")

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in mapsPackages || !sbn.isOngoing) return
        val e = sbn.notification.extras
        NavState.title = e.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        NavState.text = (e.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: e.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
        NavState.subtext = e.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty()
        NavState.updatedAt = System.currentTimeMillis()
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        if (sbn.packageName in mapsPackages && sbn.isOngoing) NavState.clear()
    }
}

package com.alfred.voice

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.ContactsContract
import android.telephony.SmsManager

/** Resolves a spoken name to a phone number and sends the message without any taps. */
class Messenger(private val context: Context) {

    data class Contact(val name: String, val number: String)

    sealed class Match {
        data class Found(val contact: Contact) : Match()
        data class Ambiguous(val names: List<String>) : Match()
        object None : Match()
    }

    /**
     * Best match wins: exact name > first name > name starts with > word starts with.
     * Several different people at the best level -> Ambiguous, so Alfred asks instead of guessing.
     */
    fun findContact(spokenName: String): Match {
        val wanted = spokenName.trim().lowercase()
        if (wanted.isEmpty()) return Match.None
        val all = mutableListOf<Contact>()
        val cols = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER)
        context.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, cols, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(0) ?: continue
                val number = c.getString(1) ?: continue
                all += Contact(name, number)
            }
        }
        val levels: List<(String) -> Boolean> = listOf(
            { n -> n == wanted },
            { n -> n.split(" ").first() == wanted },
            { n -> n.startsWith(wanted) },
            { n -> n.split(" ").any { it.startsWith(wanted) } },
        )
        for (matches in levels) {
            val hits = all.filter { matches(it.name.lowercase().trim()) }
            val people = hits.distinctBy { it.name.lowercase() }
            when {
                people.size == 1 -> return Match.Found(hits.first())
                people.size > 1 -> return Match.Ambiguous(people.map { it.name })
            }
        }
        return Match.None
    }

    /** Sends the message and returns the channel actually used ("sms" or "whatsapp"). */
    fun send(contact: Contact, message: String, channel: String): String {
        val locked = context.getSystemService(KeyguardManager::class.java).isKeyguardLocked
        // WhatsApp needs its UI on screen, so fall back to SMS when the phone is locked or the helper is off.
        if (channel == "whatsapp" && !locked && WhatsAppSender.isEnabled) {
            sendWhatsApp(contact.number, message)
            return "whatsapp"
        }
        sendSms(contact.number, message)
        return "sms"
    }

    private fun sendSms(number: String, message: String) {
        val sms = context.getSystemService(SmsManager::class.java)
        sms.sendMultipartTextMessage(number, null, sms.divideMessage(message), null, null)
    }

    private fun sendWhatsApp(number: String, message: String) {
        // WhatsApp wants the number with country code; assume India (+91) for bare 10-digit numbers.
        val digits = number.filter { it.isDigit() }.let { if (it.length == 10) "91$it" else it }
        WhatsAppSender.pendingSend = true
        val url = "https://api.whatsapp.com/send?phone=$digits&text=${Uri.encode(message)}"
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage("com.whatsapp").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }
}

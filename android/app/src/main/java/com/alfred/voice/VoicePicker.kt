package com.alfred.voice

import android.speech.tts.TextToSpeech
import android.speech.tts.Voice
import android.util.Log
import java.util.Locale

/** Picks a natural Indian-English text-to-speech voice for Alfred. */
object VoicePicker {
    private val INDIA = Locale("en", "IN")

    // Google TTS en-IN voices that sound male, best first (names vary by device; others are still allowed).
    private val PREFERRED = listOf("en-in-x-end", "en-in-x-enc", "en-in-x-ene", "en-in-x-ahp", "en-in-x-cxx")

    /** All installed Indian-English voices, preferred ones first, offline before online. */
    fun indianVoices(tts: TextToSpeech): List<Voice> =
        (tts.voices ?: emptySet())
            .filter { it.locale.language == "en" && it.locale.country == "IN" && !it.features.contains("notInstalled") }
            .sortedWith(compareBy<Voice>(
                { v -> PREFERRED.indexOfFirst { v.name.startsWith(it) }.let { if (it < 0) 99 else it } },
                { it.isNetworkConnectionRequired },
                { -it.quality },
            ))

    fun apply(tts: TextToSpeech, prefs: Prefs) {
        val voices = indianVoices(tts)
        Log.i("VoicePicker", "Indian voices: ${voices.joinToString { it.name }}")
        val chosen = voices.firstOrNull { it.name == prefs.voiceName } ?: voices.firstOrNull()
        if (chosen != null) tts.voice = chosen else tts.language = INDIA
        tts.setSpeechRate(0.95f) // a touch slower sounds more natural and is easier to follow on a bike
        tts.setPitch(1.0f)
    }
}

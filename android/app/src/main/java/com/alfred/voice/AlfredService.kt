package com.alfred.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.annotation.SuppressLint
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService
import java.util.Locale

/**
 * Always-on hands-free loop:
 *   wake word "Alfred" -> speech-to-text -> backend LLM -> spoken reply -> (confirm by voice) -> send message.
 */
class AlfredService : Service() {
    private val tag = "AlfredService"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var prefs: Prefs
    private lateinit var backend: BackendClient
    private lateinit var messenger: Messenger
    private lateinit var audio: AudioManager
    private lateinit var tts: TextToSpeech
    private var voskModel: Model? = null
    @Volatile private var wakeThread: Thread? = null
    private var recognizer: SpeechRecognizer? = null
    private var focusRequest: AudioFocusRequest? = null

    /** Message waiting for a spoken "yes"/"no", with the contact it resolved to. */
    private var pending: VoiceReply? = null
    private var pendingContact: Messenger.Contact? = null
    /** What to do after the current TTS utterance finishes. */
    private var afterSpeech: () -> Unit = {}
    private var ttsReady = false
    private var sessionRequested = false
    private var inSession = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        backend = BackendClient(prefs)
        messenger = Messenger(this)
        audio = getSystemService(AudioManager::class.java)
        startInForeground()
        // Always-listen mode keeps the headset mic open for the wake word; push-to-talk opens it per conversation.
        if (prefs.alwaysListen && prefs.useHeadsetMic) routeToHeadset()

        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                VoicePicker.apply(tts, prefs)
            }
            ttsReady = true
            if (prefs.alwaysListen) loadWakeModel()
            if (sessionRequested) startSession()
        }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) { scope.launch { val next = afterSpeech; afterSpeech = {}; next() } }
            @Deprecated("Deprecated in Java")
            override fun onError(id: String?) { onDone(id) }
        })
    }

    /** ACTION_SESSION comes from AssistActivity (power button / headset button). */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_SESSION) {
            if (ttsReady) startSession() else sessionRequested = true
        }
        return if (prefs.alwaysListen) START_STICKY else START_NOT_STICKY
    }

    override fun onDestroy() {
        stopWakeWord()
        voskModel?.close()
        recognizer?.destroy()
        tts.shutdown()
        releaseHeadset()
        scope.cancel()
        super.onDestroy()
    }

    // ---------- Wake word ----------

    /** Unpacks the offline Vosk model from assets on first run (takes a few seconds), then starts listening. */
    private fun loadWakeModel() {
        StorageService.unpack(this, "model-en-in", "model",
            { model -> voskModel = model; startWakeWord() },
            { e -> Log.e(tag, "Vosk model failed", e); say("Wake word model failed to load.") })
    }

    /**
     * Own mic loop instead of Vosk's SpeechService: that one crashes the app when the mic glitches
     * (e.g. Bluetooth switching modes). Here a failed read just reopens the mic.
     */
    @SuppressLint("MissingPermission")
    private fun startWakeWord() {
        val model = voskModel ?: return
        if (wakeThread != null) return
        val source = if (headsetActive) MediaRecorder.AudioSource.VOICE_COMMUNICATION else MediaRecorder.AudioSource.VOICE_RECOGNITION
        wakeThread = Thread {
            // Grammar mode: the recognizer only chooses between the wake word and "unknown", which keeps it light.
            val recognizer = Recognizer(model, SAMPLE_RATE.toFloat(), """["hey alfred", "[unk]"]""")
            val bufSize = maxOf(AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 4096)
            val buf = ShortArray(bufSize / 2)
            try {
                while (!Thread.currentThread().isInterrupted) {
                    val rec = AudioRecord(source, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufSize)
                    if (rec.state != AudioRecord.STATE_INITIALIZED) { rec.release(); Thread.sleep(1000); continue }
                    rec.startRecording()
                    try {
                        while (!Thread.currentThread().isInterrupted) {
                            val n = rec.read(buf, 0, buf.size)
                            if (n < 0) { Log.w(tag, "Mic read error $n, reopening"); break }
                            val heard = if (recognizer.acceptWaveForm(buf, n)) recognizer.result else recognizer.partialResult
                            if (heard.contains("hey alfred")) {
                                recognizer.reset()
                                scope.launch { onWakeWord() }
                                return@Thread
                            }
                        }
                    } finally {
                        try { rec.stop() } catch (_: Exception) {}
                        rec.release()
                    }
                    Thread.sleep(500)
                }
            } catch (_: InterruptedException) {
            } catch (e: Exception) {
                Log.e(tag, "Wake word loop failed", e)
            } finally {
                recognizer.close()
            }
        }.apply { name = "alfred-wake"; start() }
    }

    private fun stopWakeWord() {
        val t = wakeThread ?: return
        wakeThread = null
        t.interrupt()
        if (t !== Thread.currentThread()) t.join(1000)
    }

    private fun onWakeWord() = startSession()

    /** One conversation: "Yes, sir?" -> listen -> answer (-> confirm). */
    private fun startSession() {
        sessionRequested = false
        if (inSession) return
        inSession = true
        stopWakeWord() // releases the mic for speech-to-text
        duckMusic(true)
        if (!prefs.alwaysListen && prefs.useHeadsetMic && routeToHeadset()) {
            scope.launch { kotlinx.coroutines.delay(HEADSET_SETTLE_MS); say("Yes, sir?") { listen() } } // let Bluetooth switch to call mode
        } else {
            say("Yes, sir?") { listen() }
        }
    }

    private fun backToSleep() {
        pending = null
        pendingContact = null
        inSession = false
        duckMusic(false)
        if (prefs.alwaysListen) {
            startWakeWord()
        } else {
            // Push-to-talk: free the mic and the headset (music back to full quality) and stop until the next press.
            releaseHeadset()
            stopSelf()
        }
    }

    // ---------- Speech to text ----------

    /**
     * Listens until you have really finished. Android's recognizer stops at the first short pause, so after each
     * segment we listen again; if you carry on speaking it is appended, if you stay quiet for ~1.5s we answer.
     */
    private fun listen(soFar: String = "") {
        recognizer?.destroy()
        val stt = SpeechRecognizer.createSpeechRecognizer(this)
        recognizer = stt
        val continuing = soFar.isNotEmpty()
        var handled = false
        var spoke = false
        // Android may fire a late second callback (e.g. error 11 after error 7); only the first one counts.
        fun once(block: () -> Unit) {
            if (handled || recognizer !== stt) return
            handled = true
            stt.destroy()
            recognizer = null
            block()
        }
        fun finish() = if (soFar.isBlank()) retryOrSleep() else onHeard(soFar)

        stt.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle) = once {
                val text = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty().trim()
                val all = listOf(soFar, text).filter { it.isNotBlank() }.joinToString(" ")
                if (text.isBlank()) finish() else listen(all) // maybe there's more: keep listening
            }
            override fun onError(error: Int) = once {
                if (!continuing) Log.w(tag, "STT error $error")
                finish()
            }
            override fun onReadyForSpeech(params: Bundle?) {
                Log.i(tag, if (continuing) "Listening for more" else "Listening for command")
                if (continuing) {
                    // No new speech within the pause window -> you're done.
                    scope.launch { kotlinx.coroutines.delay(CONTINUE_WINDOW_MS); if (!spoke) once { finish() } }
                }
            }
            override fun onBeginningOfSpeech() { spoke = true }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        stt.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 2500L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 2000L)
        })
    }

    private fun retryOrSleep() {
        if (pending != null) say("Forgive me, sir. Say yes to send it, or no to cancel.") { listen() }
        else say("I didn't quite catch that, sir.") { backToSleep() }
    }

    // ---------- Brain ----------

    private fun onHeard(heard: String) {
        Log.i(tag, "Heard: $heard")
        // People often repeat the wake word ("Hey Alfred, how many km left?").
        val text = heard.replace(Regex("""^\s*(hey\s+)?alfred[\s,.!?]*""", RegexOption.IGNORE_CASE), "").trim()
        if (text.isEmpty()) return say("At your service, sir.") { listen() }
        pending?.let { msg -> return confirm(msg, text) }

        scope.launch {
            val reply = try { backend.command(text) } catch (e: Exception) {
                Log.e(tag, "Backend failed", e)
                return@launch say("I'm afraid I can't reach the manor's servers right now, sir.") { backToSleep() }
            }
            Log.i(tag, "Reply [${reply.actionType}]: ${reply.speak}")
            if (reply.needsConfirmation && reply.actionType != "none") askToSend(reply)
            else say(reply.speak) { backToSleep() }
        }
    }

    /** Resolve the contact on the phone first and read back the REAL name, so a mishearing can't message the wrong person. */
    private fun askToSend(reply: VoiceReply) {
        when (val match = messenger.findContact(reply.contact)) {
            is Messenger.Match.None -> say("I couldn't find ${reply.contact} in your contacts, sir.") { backToSleep() }
            is Messenger.Match.Ambiguous ->
                say("I found ${match.names.take(3).joinToString(", ", limit = 3)}. Which one did you mean, sir? Please ask me again with the full name.") { backToSleep() }
            is Messenger.Match.Found -> {
                pending = reply
                pendingContact = match.contact
                val via = if (reply.actionType == "whatsapp") " on WhatsApp" else ""
                say("Send to ${match.contact.name}$via: ${reply.message}. Shall I send it, sir?") { listen() }
            }
        }
    }

    private fun confirm(msg: VoiceReply, answer: String) {
        val a = " ${answer.lowercase()} "
        val contact = pendingContact ?: return say("Something went amiss, sir.") { backToSleep() }
        when {
            NO_WORDS.any { a.contains(it) } -> say("Very good, sir. Cancelled.") { backToSleep() }
            YES_WORDS.any { a.contains(it) } -> try {
                val used = messenger.send(contact, msg.message, msg.actionType)
                say("Done. Sent to ${contact.name} by ${if (used == "sms") "SMS" else "WhatsApp"}, sir.") { backToSleep() }
            } catch (e: Exception) {
                Log.e(tag, "Send failed", e)
                say("The message failed to send, sir.") { backToSleep() }
            }
            else -> say("Shall I send it, sir? Yes or no.") { listen() }
        }
    }

    // ---------- Audio ----------

    private fun say(text: String, then: () -> Unit = {}) {
        afterSpeech = then
        // Headset in call mode -> speak into the call channel; otherwise the phone's (dual) loudspeakers, like music.
        tts.setAudioAttributes(AudioAttributes.Builder()
            .setUsage(if (headsetActive) AudioAttributes.USAGE_VOICE_COMMUNICATION else AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "alfred-${System.nanoTime()}")
    }

    private fun duckMusic(on: Boolean) {
        if (on) {
            focusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).build()
                .also { audio.requestAudioFocus(it) }
        } else {
            focusRequest?.let { audio.abandonAudioFocusRequest(it) }
            focusRequest = null
        }
    }

    /** True while a Bluetooth headset is in call mode (HFP) for Alfred. */
    private var headsetActive = false

    /**
     * Use the Bluetooth headset mic + speaker (HFP) if one is connected. Music drops to call quality while this is on.
     * Without a headset we stay in normal mode, so Alfred talks through the loudspeakers instead of the earpiece.
     */
    @Suppress("DEPRECATION")
    private fun routeToHeadset(): Boolean {
        val sco = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
            ?: return false.also { Log.i(tag, "No Bluetooth headset: using phone speakers") }
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val device = audio.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO } ?: sco
            audio.setCommunicationDevice(device)
        } else {
            audio.startBluetoothSco()
            audio.isBluetoothScoOn = true
        }
        headsetActive = true
        return true
    }

    @Suppress("DEPRECATION")
    private fun releaseHeadset() {
        if (!headsetActive) return
        headsetActive = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) audio.clearCommunicationDevice()
        else { audio.isBluetoothScoOn = false; audio.stopBluetoothSco() }
        audio.mode = AudioManager.MODE_NORMAL
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Alfred listening", NotificationManager.IMPORTANCE_LOW))
        val n = Notification.Builder(this, CHANNEL)
            .setContentTitle("Alfred is listening")
            .setContentText(if (prefs.alwaysListen) "Say \"Hey Alfred\" to talk" else "Go ahead, sir")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(1, n)
    }

    companion object {
        private const val CHANNEL = "alfred"
        private const val SAMPLE_RATE = 16000
        private const val CONTINUE_WINDOW_MS = 1500L
        private const val HEADSET_SETTLE_MS = 900L
        const val ACTION_SESSION = "com.alfred.voice.SESSION"
        // Matched as whole words (the answer is padded with spaces).
        private val YES_WORDS = listOf(" yes ", " yeah ", " yep ", " send ", " ok ", " okay ", " sure ", " haan ", " go ahead ", " do it ")
        private val NO_WORDS = listOf(" no ", " nope ", " cancel ", " don't ", " do not ", " stop ", " wait ")
    }
}

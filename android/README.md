# Alfred Voice (Android)

A hands-free voice companion for riding. Say **"Alfred"**, then for example "how many km left?" or "message Vamsi saying I am stuck in the office, I may be late". Alfred reads the message back to you and sends it when you say "yes".

## Setup (one time)
1. The wake word runs offline with [Vosk](https://alphacephei.com/vosk/models), with no account or key. Download `vosk-model-small-en-in-0.4.zip`, unzip its contents into `app/src/main/assets/model-en-in/` and add a file named `uuid` there containing any text. (The model is gitignored because it is 55 MB.)
2. Open this `android/` folder in Android Studio, then Run it on your phone.
3. In the app:
   - Enter the server URL (your PC's LAN IP or tunnel URL) and `APP_PASSWORD`.
   - Tap the three permission buttons:
     - mic/SMS/contacts
     - Notification access → Alfred Voice
     - Accessibility → Alfred Voice (optional, for WhatsApp)
4. Tap **Start Alfred**.
5. Settings → Apps → Alfred Voice → Battery → **Unrestricted**, so Android doesn't kill it.

## Notes
- Messages go by **SMS** unless you say "WhatsApp". WhatsApp only works when the screen is unlocked; otherwise Alfred falls back to SMS.
- With "Use Bluetooth headset mic" on, the headset runs in call mode (HFP), so music plays at lower quality. This is a Bluetooth limitation.
- Backend endpoint: `POST /api/v1/voice/command` (`src/api/voice.py`).

# Alfred (Android)

The Alfred AI Studio web app on your phone: a full-screen WebView of the studio running on the PC
(news, images, videos, Content-Monitor, chat). There is no voice assistant any more.

## Setup (one time)
1. Install the Tailscale app on the phone and sign in with the same account as the PC.
2. Open this `android/` folder in Android Studio and Run it on the phone, or build with
   `gradlew assembleDebug` and install with `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
3. Long-press the Alfred icon -> **Settings**:
   - Server URL: the PC's Tailscale address, e.g. `http://100.88.12.72:8000` (works anywhere Tailscale is on)
   - Backup URL: used automatically when the main one fails (e.g. the Tailscale Funnel address)
   - App password: `APP_PASSWORD` from the PC's `.env`

The app shows a "Can't reach Alfred" screen with a Retry button when the PC or Tailscale is off.

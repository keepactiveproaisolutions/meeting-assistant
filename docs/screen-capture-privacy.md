# Screen Capture Privacy & Window Exclusion

The Meeting Assistant desktop application provides an optional **"Hide from screen capture"** privacy feature using native Microsoft Windows display affinity APIs.

---

## 1. How It Works (Native Windows Implementation)

The feature utilizes the 64-bit Windows user32 APIs:
- `SetWindowDisplayAffinity(HWND hWnd, DWORD dwAffinity)`
- `GetWindowDisplayAffinity(HWND hWnd, DWORD *pdwAffinity)`

When enabled:
* The application sets the display affinity flag to `WDA_EXCLUDEFROMCAPTURE` (`0x00000011`).
* This requests that the Desktop Window Manager (DWM) and Windows Graphics Capture exclude the window's visual contents from capture surfaces while remaining fully visible and interactive on the local monitor.
* The application reads back the affinity using `GetWindowDisplayAffinity` to verify the setting was accepted by the OS before updating status text.

When disabled:
* The display affinity flag is reset to `WDA_NONE` (`0x00000000`).

---

## 2. Windows Version Support & Expected Behavior

| Windows Version | Expected Visual Behavior during Desktop Share |
|---|---|
| **Windows 10 Version 2004+ (Build 19041+) & Windows 11** | **Transparent Exclusion:** The window is omitted from the screen capture stream. Whatever is positioned behind the assistant window remains visible to viewers. |
| **Older Windows (Windows 7, 8, Windows 10 prior to build 19041)** | **Black Rectangle:** Older systems only support `WDA_MONITOR` (`0x00000001`), which renders a solid black box where the window is located. |
| **Non-Windows Platforms (macOS / Linux)** | **Unsupported:** Displays *"Capture exclusion unsupported on this system"*. |

---

## 3. Important Limitations & Boundary Conditions

> [!WARNING]
> This is a client-side privacy request, **NOT** an absolute cryptographic guarantee of invisibility.

* **Capture Method Dependencies:**  
  Modern applications using the Desktop Window Manager (DWM) or the **Windows Graphics Capture API** (such as modern Microsoft Teams, Zoom, Google Meet desktop sharing, and Discord) honor `WDA_EXCLUDEFROMCAPTURE`. However, legacy mirror drivers, kernel-mode capture software, or certain remote management tools may bypass DWM composition.
* **External Cameras & Hardware Capture Cards:**  
  Physical cameras recording your screen, HDMI capture cards, and display hardware splitters capture the actual monitor output signal and **cannot** be blocked by OS display affinity.
* **Audio & Microphones:**  
  This setting controls only visual window rendering. Audio captured or transcribed remains subject to meeting audio routing.
* **Clipboard Content:**  
  Using "Copy" or "Copy All" writes text to the operating system clipboard. Any application with clipboard access can read it.
* **Web Application Limitation:**  
  **The synchronized web browser view (React web app in Chrome, Edge, Firefox, etc.) is NOT protected by this desktop setting.** If you share the browser tab or monitor showing the web app, viewers will see the live transcript and AI answers.
* **Compliance & Ethics:**  
  Use this feature only in compliance with organizational policies, meeting rules, examination guidelines, and recording consent regulations.

---

## 4. Manual Verification Steps

A successful API return value does not prove that a remote meeting participant cannot see the window. You should verify your environment manually:

1. Launch Meeting Assistant and check **"Hide from screen capture"**. Verify the status indicates *"Capture exclusion enabled — verify before sharing"*.
2. Open a test call on your conferencing platform (**Microsoft Teams, Zoom, or Google Meet**) and share your **entire screen / desktop**.
3. Connect to the test call using a **secondary device** (such as a smartphone or laptop) as an attendee.
4. Verify from the attendee view that the assistant window is transparently omitted.
5. If using floating mode, confirm the floating window is also excluded.
6. Repeat verification whenever switching video conferencing tools or recording software.

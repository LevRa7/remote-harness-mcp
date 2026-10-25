# Screenshots

Captures of the Gemini app driving nodes through the gateway, used by the top-level
README.

| File | What it shows | Node |
| :--- | :--- | :--- |
| `01-hardware.png` | Gemini lists the CPU, GPU, disks and memory it read from the machine | Linux laptop |
| `02-phone.png` | battery, Wi-Fi signal, LTE state, VPN and CPU temperature of a phone | Android (Termux) |
| `03-screen-capture.png` | Gemini captures the desktop, publishes the file, then describes both monitors | Linux laptop |

## What was edited, and what was not

The sources are ordinary screenshots of the Gemini app (a phone capture and two
browser captures). Three corrections were made, none of them to the content of an
answer:

- **Cropped**: browser chrome (the address bar carries a conversation id and a zoom
  badge), the "ask a follow-up question" composer, and the trailing half-line each
  scroll capture ends with, so no card stops mid-sentence.
- **Mosaicked**: the Wi-Fi SSID, the LAN address and the Tailscale address in
  `02-phone.png`. They identify a network, not a capability.
- **Nothing else**: every message, tool result, model name, temperature and device
  name is what the assistant returned. No text was rewritten and no result was
  staged by hand.

The gateway is addressed as `@Rem<node>` in the chat: the orchestration skill names
each node so one conversation can drive several machines.

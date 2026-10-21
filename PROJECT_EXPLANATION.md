# RESTS Project Explanation

## 1. What RESTS does

RESTS is a speech translation demo. A person speaks into a laptop microphone. The Windows application recognizes the speech, translates it locally, and displays both the original words and the translation. If Windows has a suitable speech voice installed, the app can also read the translation aloud.

An emulated STM32F429ZI microcontroller is part of the audio route. The host compresses captured speech into small packets, sends them through Renode to firmware over USART1, and receives the relayed packets back through the same local connection. Renode provides the virtual STM32 board and connects its UART to the app through a TCP socket.

**The laptop does the AI processing.** The STM32 firmware validates and relays compressed audio frames, and also handles control commands. It does not recognize or translate speech. Renode emulates the board; it is not the translation service.

## 2. Applications and tools

| Tool | What it is used for |
|---|---|
| STM32CubeIDE | Opens and builds the STM32F429 firmware project. |
| STM32CubeMX configuration (`RESTS_STM32.ioc`) | Describes the microcontroller and peripheral configuration used by the firmware project. |
| Renode | Emulates the STM32F429ZI board and bridges USART1 to the desktop app's local TCP connection. |
| Python desktop application | Captures and compresses speech, sends it through the STM32 relay, runs recognition and translation on returned audio, displays captions, and optionally speaks the translation. |
| Faster-Whisper | Converts the recorded speech into text on the laptop. |
| NLLB-200 | Translates recognized text on the laptop. |
| Windows SAPI voices | Local text-to-speech voices supplied by Windows; the selected target needs a matching voice. |

After the model files are downloaded, recognition and translation run locally. The app does not send recordings to a cloud translation service. Renode's UART connection is local to the computer.

## 3. End-to-end workflow

1. Build the Debug firmware in STM32CubeIDE. This creates `Debug/RESTS_STM32.elf`.
2. Start the Renode launcher. It loads the STM32F429ZI platform, loads the ELF firmware, and connects the emulated USART1 to a TCP socket terminal, normally port `12345`.
3. Start the RESTS desktop application. It connects to Renode at `127.0.0.1:12345` and checks that the firmware responds.
4. Select a source language or **Auto**, choose a target language, and optionally enable spoken output.
5. Hold the **HOLD TO TALK** button or **F9**, speak into the laptop microphone, and release.
6. The host encodes audio into compact IMA ADPCM blocks and sends sequenced, CRC-checked frames through TCP, Renode, and USART1.
7. The STM32 checks each frame and relays it back through USART1 and Renode. The host verifies the response and rebuilds the audio samples.
8. Faster-Whisper transcribes the returned audio. NLLB-200 translates the transcript. The app displays both captions and, when enabled, asks an installed matching Windows voice to speak the translation.
9. Language and push-to-talk commands continue to use the text UART protocol; firmware acknowledgements remain available.

## 4. System flow

```text
AUDIO / TRANSLATION
Laptop microphone
    → push-to-talk recording
    → IMA ADPCM audio blocks
    → sequenced CRC-checked frames
    → TCP socket → Renode → STM32F429 USART1 relay
    → returned frames → host audio reconstruction
    → Faster-Whisper speech recognition
    → source transcript → NLLB-200 translation
    → translated caption → Windows SAPI voice → laptop speaker (if installed)

MICROCONTROLLER / UART
RESTS desktop app
    → TCP socket (127.0.0.1:12345)
    → Renode STM32F429ZI emulation
    → USART1 interrupt receiver
    → CRC-checked audio frame relay
    → returned frame over USART1 / Renode / TCP
    → host verifies sequence and checksum, then decodes audio

Control messages (HELLO?, PING, LANG, PTT) share the same link and retain
their newline-delimited command/reply format.
```

The captured voice follows the UART route before speech recognition. Models still execute on the laptop, and translated text goes directly from the laptop to both the display and (when a matching Windows voice is installed) text-to-speech. The computer used for verification detected English, Japanese, and Korean Windows voices. Hindi and Malayalam still display translated captions, but no matching SAPI voice was installed on that computer.

## 5. UART protocol in plain language

The app and firmware exchange newline-terminated text commands and binary audio frames over USART1. Renode bridges USART1 to TCP. Audio frames contain magic bytes, type, sequence, length, payload, and CRC-16/CCITT-FALSE. The host encodes 16-bit samples into independent IMA ADPCM blocks; the firmware validates and returns each frame unchanged.

| Message | Meaning | Typical firmware response |
|---|---|---|
| `HELLO?` | App asks whether the RESTS firmware is available. | `RESTS/1 READY MCU=STM32F429ZI` |
| `PING` | Checks that the link is responsive. | `PONG` |
| `LANG:<source>:<target>` | Reports the selected source and target language tokens. | An `ACK` response or an error. |
| `PTT:BEGIN` | Push-to-talk recording has started. | An `ACK` response. |
| `PTT:END` | Push-to-talk recording has ended. | An `ACK` response. |
| Binary audio frame | Compressed audio block with sequence and CRC. | The same frame is relayed back; no text acknowledgement is needed. |

For example, `LANG:en:ml` indicates English as the selected source and Malayalam as the target. If source is **Auto**, the app sends `auto` at the time the button is pressed; the actual language is detected from the recording afterward.

## 6. Important project files

### Firmware and board configuration

- `Core/Src/main.c` — firmware startup and main loop, USART1 interrupt receive, command handling, audio frame validation/relay, and text responses. Generated-code regions are marked with CubeMX `USER CODE` comments.
- `Core/Src/stm32f4xx_it.c` — interrupt handlers, including the USART interrupt entry point.
- `Core/Inc/` — firmware headers and generated peripheral declarations.
- `Drivers/` — CMSIS device/core headers and STM32F4 HAL implementation.
- `RESTS_STM32.ioc` — CubeMX project source configuration.
- `STM32F429ZITX_FLASH.ld` and `STM32F429ZITX_RAM.ld` — linker layouts for the MCU memory.
- `Debug/RESTS_STM32.elf` — generated Debug firmware image loaded by Renode; rebuild it after firmware changes.

### Host application

- `host_app/rests_host/app.py` — Tkinter user interface and user interaction; `_job_worker()` calls text-to-speech after a successful translation and displays speech errors or the voice used.
- `host_app/rests_host/audio.py` — microphone recording and audio handling.
- `host_app/rests_host/audio_transport.py` — IMA ADPCM codec, binary frame/CRC handling, and audio round-trip coordination.
- `host_app/rests_host/ptt.py` — push-to-talk state and coordination.
- `host_app/rests_host/asr.py` — local Faster-Whisper recognition and CPU/CUDA device handling.
- `host_app/rests_host/translate.py` — local translation backend and NLLB integration.
- `host_app/rests_host/languages.py` — language labels and model language codes.
- `host_app/rests_host/tts.py` — Windows SAPI voice matching and spoken output via `WindowsSpeech.speak()`.
- `host_app/rests_host/uart.py` — socket connection, command/binary frame demultiplexing, handshake, and sequenced audio exchange.
- `host_app/rests_host/pipeline.py` — coordinates audio, recognition, translation, and results.
- `host_app/rests_host/config.py` — application settings and environment-based overrides.
- `host_app/rests_host/selfcheck.py` — model, voice, and optional UART diagnostics.

### Running, emulation, and verification

- `host_app/renode/launch_renode.ps1` — starts Renode and prepares the firmware image path.
- `host_app/renode/stm32f429_rests.resc` — loads the STM32F429ZI model and firmware, then connects USART1 to the socket terminal.
- `tests/renode_uart_protocol.robot` — Renode-based UART protocol checks.
- `host_app/tests/` — Python tests for host app components.
- `host_app/setup.ps1`, `host_app/run.ps1`, `host_app/test.ps1` — set up, run, and test the host app.
- `host_app/download_models.py` — explicit one-time local model download.
- `README.md` — short overview and quick start; `host_app/README.md` — detailed host application setup and troubleshooting.

## 7. Languages, voice, and compute

The language selector offers 95 languages supported by both the speech-recognition model and the translation model. The source selector also offers **Auto**. Model language support does not guarantee equal translation quality for every language pair.

Speech output depends on Windows SAPI voices installed on the computer. The app reports the selected voice before a recording and the voice used after speaking; speech errors are shown in the UI instead of being silently swallowed. On the verified computer, the detected spoken targets were English, Japanese, and Korean. Hindi and Malayalam still work as translated captions, but a matching Windows SAPI voice was not installed. The available targets can differ between Windows computers.

The app can use CUDA when the required runtime is available. In automatic mode, a CUDA runtime failure can fall back to CPU. CPU mode is also available when there is no supported GPU/runtime.

## 8. Model licence and project limitations

The default NLLB-200 translation model is licensed **CC-BY-NC 4.0 (Attribution-NonCommercial)**. This is a non-commercial-use restriction. Check the original model licence before distributing or using the project in a commercial setting. Downloaded weights are stored locally and are excluded from Git.

The emulated MCU relays compressed speech frames but does not perform recognition, run neural-network inference, or translate text. Speech and translation models run on the laptop; voice packets return from the emulated MCU before recognition.

## 9. Short demonstration sequence

1. Show the project in STM32CubeIDE and point out `Core/Src/main.c` and `RESTS_STM32.ioc`.
2. Show Renode running the STM32F429ZI firmware.
3. Start the RESTS desktop app and show the connected/ready state.
4. Choose a source and target language, hold **HOLD TO TALK** or **F9**, say a short sentence, and release.
5. Select a target with an installed voice (English, Japanese, or Korean on the verified computer), enable **Spoken output**, and show the source transcript, translated caption, and audible spoken translation.
6. Explain that voice packets cross USART1 and return through Renode before Faster-Whisper processes them; NLLB translation, captions, and text-to-speech remain on the laptop.

# RESTS Project Explanation

## 1. What RESTS does

RESTS is a speech translation demo. A person speaks into a laptop microphone. The Windows application recognizes the speech, translates it locally, and displays both the original words and the translation. If Windows has a suitable speech voice installed, the app can also read the translation aloud.

An emulated STM32F429ZI microcontroller is part of the demonstration. It receives session and language commands from the desktop application and replies over UART. Renode provides the virtual STM32 board and connects its UART to the app through a local TCP socket.

**The laptop does the AI processing.** The STM32 firmware does not recognize or translate speech. Its role is to demonstrate embedded firmware, interrupt-based serial input, command parsing, and acknowledgements.

## 2. Applications and tools

| Tool | What it is used for |
|---|---|
| STM32CubeIDE | Opens and builds the STM32F429 firmware project. |
| STM32CubeMX configuration (`RESTS_STM32.ioc`) | Describes the microcontroller and peripheral configuration used by the firmware project. |
| Renode | Emulates the STM32F429ZI board and exposes USART1 to the desktop app. |
| Python desktop application | Captures speech, runs recognition and translation, displays captions, optionally speaks the translation, and sends UART commands. |
| Faster-Whisper | Converts the recorded speech into text on the laptop. |
| NLLB-200 | Translates recognized text on the laptop. |
| Windows SAPI voices | Optional local text-to-speech voices supplied by Windows. |

After the model files are downloaded, recognition and translation are intended to run locally. The app does not send recordings to a cloud translation service. Renode's UART connection is local to the computer.

## 3. End-to-end workflow

1. Build the Debug firmware in STM32CubeIDE. This creates `Debug/RESTS_STM32.elf`.
2. Start the Renode launcher. It loads the STM32F429ZI platform, loads the ELF firmware, and connects the emulated USART1 to a TCP socket terminal, normally port `12345`.
3. Start the RESTS desktop application. It connects to Renode at `127.0.0.1:12345` and checks that the firmware responds.
4. Select a source language or **Auto**, choose a target language, and optionally enable spoken output.
5. Hold the **HOLD TO TALK** button or **F9**, speak into the laptop microphone, and release.
6. The application sends the selected language and push-to-talk state to the firmware. In parallel, it processes the captured audio locally.
7. Faster-Whisper produces a source transcript. NLLB-200 translates that text. The app displays the transcript and translated caption, then uses an installed Windows voice if spoken output is enabled and available.
8. The firmware sends acknowledgements to the app. These demonstrate the UART path; they do not carry audio or translated text.

## 4. System flow

```text
AUDIO / TRANSLATION
Laptop microphone
    → push-to-talk recording
    → Faster-Whisper speech recognition
    → source transcript
    → NLLB-200 translation
    → translated caption
    → optional Windows voice

MICROCONTROLLER / UART
RESTS desktop app
    → TCP socket (127.0.0.1:12345)
    → Renode STM32F429ZI emulation
    → emulated USART1
    → firmware interrupt receiver and command parser
    → acknowledgement returned along the same path
```

The two paths are connected by the desktop application, but speech audio and model inference stay on the laptop. UART messages are short control commands.

## 5. UART protocol in plain language

The app and firmware exchange newline-terminated text commands over USART1. Renode bridges USART1 to TCP, allowing the Windows app to connect as a normal TCP client.

| Message | Meaning | Typical firmware response |
|---|---|---|
| `HELLO?` | App asks whether the RESTS firmware is available. | `RESTS/1 READY MCU=STM32F429ZI` |
| `PING` | Checks that the link is responsive. | `PONG` |
| `LANG:<source>:<target>` | Reports the selected source and target language tokens. | An `ACK` response or an error. |
| `PTT:BEGIN` | Push-to-talk recording has started. | An `ACK` response. |
| `PTT:END` | Push-to-talk recording has ended. | An `ACK` response. |

For example, `LANG:en:ml` indicates English as the selected source and Malayalam as the target. If source is **Auto**, the app sends `auto` at the time the button is pressed; the actual language is detected from the recording afterward.

## 6. Important project files

### Firmware and board configuration

- `Core/Src/main.c` — firmware startup and main loop, USART1 setup, command handling, and protocol responses. Generated-code regions are marked with CubeMX `USER CODE` comments.
- `Core/Src/stm32f4xx_it.c` — interrupt handlers, including the USART interrupt entry point.
- `Core/Inc/` — firmware headers and generated peripheral declarations.
- `Drivers/` — CMSIS device/core headers and STM32F4 HAL implementation.
- `RESTS_STM32.ioc` — CubeMX project source configuration.
- `STM32F429ZITX_FLASH.ld` and `STM32F429ZITX_RAM.ld` — linker layouts for the MCU memory.
- `Debug/RESTS_STM32.elf` — generated Debug firmware image loaded by Renode; rebuild it after firmware changes.

### Host application

- `host_app/rests_host/app.py` — Tkinter user interface and user interaction.
- `host_app/rests_host/audio.py` — microphone recording and audio handling.
- `host_app/rests_host/ptt.py` — push-to-talk state and coordination.
- `host_app/rests_host/asr.py` — local Faster-Whisper recognition and CPU/CUDA device handling.
- `host_app/rests_host/translate.py` — local translation backend and NLLB integration.
- `host_app/rests_host/languages.py` — language labels and model language codes.
- `host_app/rests_host/tts.py` — optional Windows speech voice selection and output.
- `host_app/rests_host/uart.py` — socket connection, line framing, handshake, and UART command exchange.
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

Speech output depends on Windows SAPI voices installed on the computer. If a target language has no matching installed voice, the app still displays the translated caption and reports that speech output is unavailable for that language.

The app can use CUDA when the required runtime is available. In automatic mode, a CUDA runtime failure can fall back to CPU. CPU mode is also available when there is no supported GPU/runtime.

## 8. Model licence and project limitations

The default NLLB-200 translation model is licensed **CC-BY-NC 4.0 (Attribution-NonCommercial)**. This is a non-commercial-use restriction. Check the original model licence before distributing or using the project in a commercial setting. Downloaded weights are stored locally and are excluded from Git.

The emulated MCU demonstrates UART control and acknowledgement behavior. It does not process microphone audio, run neural-network inference, or perform translation. The real-time speech pipeline is implemented on the laptop.

## 9. Short demonstration sequence

1. Show the project in STM32CubeIDE and point out `Core/Src/main.c` and `RESTS_STM32.ioc`.
2. Show Renode running the STM32F429ZI firmware.
3. Start the RESTS desktop app and show the connected/ready state.
4. Choose a source and target language, hold **HOLD TO TALK** or **F9**, say a short sentence, and release.
5. Show the source transcript, translated caption, and optional spoken output.
6. Explain that UART acknowledgements demonstrate the host-to-firmware communication, while the laptop performs speech recognition and translation.

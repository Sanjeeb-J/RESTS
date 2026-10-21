# RESTS — Real-Time English Speech Translation System

RESTS is a local speech translation demonstration built around a Windows desktop application and an STM32F429 firmware project. Speak using push-to-talk, view the recognized speech and translated caption, and optionally hear the translation through an installed Windows voice. Renode emulates the microcontroller and connects to the desktop application through UART-over-TCP.

> **Processing split:** The laptop captures and compresses the speech. The audio packets travel through Renode and the STM32F429 UART relay, then return to the laptop. Faster-Whisper and NLLB run on the laptop; the STM32 does not run the AI models.

## Features

- Push-to-talk recording from a laptop microphone (button or **F9**).
- Local speech recognition with Faster-Whisper and local translation with NLLB-200.
- Source transcript and translated caption in the desktop UI.
- Spoken translation using a matching installed Windows speech voice; the app reports the voice used or explains when one is unavailable.
- A 95-language source/target selection, plus automatic source-language detection.
- Compressed audio relay through Renode and interrupt-driven STM32F429ZI USART1 firmware.
- CRC-checked, sequenced UART audio packets; existing PING and command acknowledgements remain available.
- Local operation after model files are downloaded; no cloud translation service is used.

## Architecture

```text
Laptop microphone
       │
       ▼
RESTS host ──► ADPCM audio packets ──► TCP socket ──► Renode ──► STM32F429 USART1 relay
       ▲                                                                    │
       └──────────────────── relayed audio returns over UART/TCP ──────────┘
       │
       └──► Faster-Whisper ──► transcript ──► NLLB-200 ──► captions / optional voice
```

The STM32 returns the framed audio packets; it does not recognize speech or translate text. Recognition, translation, captions, and optional text-to-speech happen on the laptop. Push-to-talk and language commands also use the UART link. See [PROJECT_EXPLANATION.md](PROJECT_EXPLANATION.md) for the full workflow, key files, and packet protocol.

Spoken output uses Windows SAPI voices installed on the computer. Voice availability depends on the selected target language. In the verified Windows setup, English, Japanese, and Korean voices were detected; when a target has no matching voice, RESTS keeps the translated caption visible and reports that speech is unavailable.

## Requirements

- Windows 10 or 11, 64-bit.
- Python 3.11.
- STM32CubeIDE with the STM32F4 firmware package for building the firmware.
- Renode 1.17 or later for the emulated target.
- A microphone. A compatible NVIDIA CUDA runtime/GPU is optional; recognition can run on CPU.
- Several gigabytes of disk space for the local speech and translation models.

## Quick start

Run the following from the project root in PowerShell.

### 1. Build the firmware

Open the project in STM32CubeIDE and build the **Debug** configuration. Renode expects the output at `Debug/RESTS_STM32.elf`.

### 2. Set up the desktop application

```powershell
powershell -ExecutionPolicy Bypass -File host_app\setup.ps1
host_app\.venv\Scripts\python.exe host_app\download_models.py
```

Model downloads are a one-time step and require internet access. The app loads the models from the local cache and does not download them when it starts.

### 3. Start Renode

In a PowerShell window, run:

```powershell
powershell -ExecutionPolicy Bypass -File host_app\renode\launch_renode.ps1
```

Keep Renode running while using the app. Its emulated USART1 is available to the host at `127.0.0.1:12345` by default.

### 4. Start RESTS

In another PowerShell window, run:

```powershell
powershell -ExecutionPolicy Bypass -File host_app\run.ps1
```

Choose the source and target languages, hold the on-screen talk button or **F9** while speaking, and release it. The compressed audio travels through Renode and the STM32 relay before local recognition and translation. Results appear in the source transcript and translated caption panels. Keep Renode running for each recording.

## Checks and tests

Check the local model/voice setup and UART connection:

```powershell
host_app\.venv\Scripts\python.exe -m rests_host --self-check --probe-uart
```

Run the host application tests:

```powershell
powershell -ExecutionPolicy Bypass -File host_app\test.ps1
```

Run the Renode UART protocol suite (requires Renode and a built Debug ELF):

```powershell
renode-test.bat tests\renode_uart_protocol.robot
```

Run the real Renode audio round-trip test (starts and stops its own Renode instance):

```powershell
$env:RESTS_RUN_RENODE_TESTS = "1"
host_app\.venv\Scripts\python.exe -m pytest host_app\tests\test_renode_audio_relay.py -q
Remove-Item Env:\RESTS_RUN_RENODE_TESTS
```

More setup details and troubleshooting are in [host_app/README.md](host_app/README.md).

## Important model licence

The default translation model, `facebook/nllb-200-distilled-600M`, is licensed **CC-BY-NC 4.0**. Its use is restricted to non-commercial purposes under that licence. Review the model licence before redistributing or using RESTS commercially. The model files are local and are not included in Git.

## Project structure

```text
RESTS/
├── Core/                         STM32 application and HAL integration
├── Drivers/                      CMSIS and STM32F4 HAL drivers
├── Debug/                        Debug build output (generated)
├── Release/                      Release build output (generated)
├── tests/                        Renode UART protocol test
├── host_app/
│   ├── rests_host/               Desktop app, audio, ASR, translation, UART
│   ├── renode/                   Renode platform launch and configuration
│   ├── tests/                    Python unit tests
│   ├── README.md                 Host app setup and detailed usage
│   └── setup.ps1 / run.ps1       Setup and launch scripts
├── RESTS_STM32.ioc               STM32CubeMX project configuration
├── PROJECT_EXPLANATION.md        Plain-language architecture and file guide
└── README.md                     Project overview and quick start
```

Build products, the Python virtual environment, and downloaded models are generated locally and excluded from Git.

# RESTS — Real-Time English Speech Translation System

RESTS is a local speech translation demonstration built around a Windows desktop application and an STM32F429 firmware project. Speak using push-to-talk, view the recognized speech and translated caption, and optionally hear the translation through an installed Windows voice. Renode emulates the microcontroller and connects to the desktop application through UART-over-TCP.

> **Current scope:** Speech recognition and translation run on the laptop. The STM32 firmware demonstrates command handling and UART communication; it does not run the speech or translation models.

## Features

- Push-to-talk recording from a laptop microphone (button or **F9**).
- Local speech recognition with Faster-Whisper and local translation with NLLB-200.
- Source transcript and translated caption in the desktop UI.
- Optional spoken output using installed Windows speech voices.
- A 95-language source/target selection, plus automatic source-language detection.
- STM32F429ZI firmware emulated in Renode, with interrupt-driven USART1 reception.
- Local operation after model files are downloaded; no cloud translation service is used.

## Architecture

```text
Laptop microphone
       │
       ▼
RESTS Windows app ──► Faster-Whisper ──► source transcript
       │                                      │
       │                                      ▼
       │                                  NLLB-200 ──► translated caption / optional voice
       │
       └── UART commands over TCP ──► Renode ──► emulated STM32F429ZI firmware
                                      127.0.0.1:12345
```

The desktop app performs audio capture, recognition, translation, captions, and optional text-to-speech. The firmware receives session and language commands and returns acknowledgements. See [PROJECT_EXPLANATION.md](PROJECT_EXPLANATION.md) for the full workflow, key files, and protocol.

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

Choose the source and target languages, hold the on-screen talk button or **F9** while speaking, and release it to process the audio. Results appear in the source transcript and translated caption panels.

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

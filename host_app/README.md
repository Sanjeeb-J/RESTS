# RESTS host application

Windows desktop application for the RESTS STM32F429 UART project: it
records speech with the laptop microphone, transcribes and translates it
entirely on-device, shows always-visible captions and drives the emulated
firmware over UART (via Renode's TCP socket) with push-to-talk control.

```
microphone ──► faster-whisper (local) ──► captions (source)
                         │
                         ▼
               NLLB-200 (local)  ──► captions (translation) ──► optional Windows TTS
                         │
UART  ◄──── Renode 127.0.0.1:12345 ──── HELLO? / LANG:.. / PTT:BEGIN / PTT:END
```

Nothing leaves the machine: there is **no cloud service, no telemetry and
no network access** other than (a) the one-off model download in
`download_models.py` and (b) the localhost Renode socket.

---

## 1. Requirements

| Component | Requirement |
|---|---|
| OS | Windows 10/11 (64-bit) |
| Python | **3.11** (tkinter, `setup.ps1` verifies the version) |
| Renode | 1.17+ (tested with `C:\Program Files\Renode`) |
| Firmware build | `Debug/RESTS_STM32.elf` from STM32CubeIDE (or the headless build) |
| GPU | optional - CUDA is tried first and falls back to CPU automatically; `--asr-device cpu` disables CUDA entirely |
| Microphone | any Windows audio input device |
| Voices | optional - installed Windows (SAPI) voices for spoken output |

## 2. Setup

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File host_app\setup.ps1
```

This creates an isolated environment in `host_app\.venv` (Python 3.11) and
installs `requirements.txt` + `requirements-dev.txt`.

Tests-only variant (no torch / transformers / faster-whisper, ~instant):

```powershell
powershell -ExecutionPolicy Bypass -File host_app\setup.ps1 -DevOnly
```

### Download the models (separate, explicit step)

The application **never** downloads weights at start-up: every load uses
`local_files_only=True` and reports *"run host_app\download_models.py"*
when the cache is empty. Download them once, on a machine that has
internet access:

```powershell
host_app\.venv\Scripts\python.exe host_app\download_models.py
```

| Model | Default id | ~Size | Licence |
|---|---|---|---|
| Speech recognition | `Systran/faster-whisper-medium` | 1.5 GB | MIT (Whisper) / Apache-2.0 (Systran conversions) |
| Translation | `facebook/nllb-200-distilled-600M` | 2.4 GB | **CC-BY-NC 4.0 - research / non-commercial only** |

Both are stored in `host_app\models\` (overridable with
`RESTS_MODEL_DIR`), which is **ignored by Git**. Use `--skip-nllb` /
`--skip-whisper` to fetch only one of them.

## 3. Start the emulation

```powershell
powershell -ExecutionPolicy Bypass -File host_app\renode\launch_renode.ps1
```

The script checks that `Debug\RESTS_STM32.elf` exists (build the *Debug*
configuration first), starts Renode and runs
`host_app\renode\stm32f429_rests.resc`:

```
mach create "rests-f429"
machine LoadPlatformDescription @platforms/cpus/stm32f429.repl
sysbus LoadELF ./Debug/RESTS_STM32.elf
emulation CreateServerSocketTerminal 12345 "term" false
connector Connect sysbus.usart1 term
start
```

Handy switches: `-Headless` (no GUI), `-ElfPath <file>` (custom ELF),
`-RenodePath <file>` / `RENODE_EXE` (custom install), `-Port <n>` (the
`$port` override; keep the app in sync with `RESTS_UART_PORT`).

The host application connects as a **TCP client** to `127.0.0.1:12345`.
If Renode is not running the UI simply shows `error - connect failed`.

## 4. Run the application

```powershell
powershell -ExecutionPolicy Bypass -File host_app\run.ps1
```

Useful flags (forwarded to `python -m rests_host`):

| Flag | Effect |
|---|---|
| `--self-check` | print model/voice/CUDA status, no UI, no downloads |
| `--self-check --probe-uart` | also performs a live `HELLO?` + `PING` handshake |
| `--no-connect` | start without the automatic Renode connection |
| `--backend passthrough` | skip NLLB (captions show the source text) |
| `--asr-device auto\|cpu\|cuda` | speech-recognition device (default `auto`; see §9) |
| `--host` / `--port` | override the Renode socket address |

### Choosing the compute device

`--asr-device` (or `RESTS_ASR_DEVICE`) has three settings, mirrored by the
**Device** box in the UI:

| Mode | Behaviour |
|---|---|
| `auto` (default) | Try CUDA first (float16); if the CUDA runtime is missing or fails, retry the same recording on CPU (int8), stay on CPU afterwards and show a notice in the status bar. |
| `cpu` | Never touch CUDA - works without any NVIDIA library installed. |
| `cuda` | Force CUDA. When the CUDA runtime is unusable the error message explains what to install instead of failing with a bare `cublas64_12.dll` loader error. |

### Using it

1. **Source** = `Auto` (or one of the 95 languages), **Target** = one of
   the 95 languages - see §7.
2. Press and **hold** the *HOLD TO TALK* button or the **F9** key
   (`RESTS_PTT_KEY`), speak, then release.
3. The source transcript and the translated caption appear immediately and
   stay on screen. The next hold replaces them.

The window keeps a **700x660** minimum so every section stays readable: the
translated caption gets about two thirds of the caption area, the status bar
is colour-coded (blue busy / green ok / amber warning / red error) and
errors or warnings also appear in a dismissible strip above it. A mid-session
change such as the CUDA-to-CPU fallback shows up there once.

While a session is active the app sends, in this order:

```
CRLF-terminated commands on 127.0.0.1:12345
  press : LANG:<source>:<target>   then   PTT:BEGIN
  release:                          PTT:END
handshake on connect               HELLO?   ->  RESTS/1 READY MCU=STM32F429ZI
```

`source` is `auto` when the source language is *Auto* (detection happens
after the recording), otherwise `en`, `ml`, `hi`, `ja`, `ko`. The firmware
answers with `ACK:...` / `PONG`, or `ERR:...`, which the status bar shows
verbatim. All sockets, audio callbacks and model inference run off the UI
thread.

## 5. Offline behaviour

* ASR, translation, TTS and UART are all local; no HTTP client exists in
  the code path (only `huggingface_hub.snapshot_download` inside the
  explicit download script).
* Model loads use `local_files_only=True` - an empty cache produces an
  instruction to run `download_models.py` instead of a silent download.
* Verified by `python -m rests_host --self-check`, which performs no
  network access.

## 6. Model licences

* **`facebook/nllb-200-distilled-600M` - CC-BY-NC 4.0 (Creative Commons
  Attribution-NonCommercial).** Research and other non-commercial use
  only; it must not be used in a commercial product or service, and this
  restriction carries over to translated output. See
  <https://huggingface.co/facebook/nllb-200-distilled-600M> for the
  authoritative text.
* `Systran/faster-whisper-*` - Apache-2.0 conversions of OpenAI Whisper
  (MIT).
* `torch` - BSD-style; `transformers` - Apache-2.0.

The translation backend is swappable (`rests_host.translate.
TranslationBackend`, selected with `RESTS_TRANSLATION_BACKEND`); use
`passthrough` for a no-model run, or drop in a permissively licensed
backend without touching the UI or the UART code.

## 7. Language limitations

* Supported languages: **95** - the intersection of faster-whisper's
  100-code transcription alphabet and the local NLLB-200 tokenizer
  vocabulary (`rests_host.languages.BASE_LANGUAGES`). The source combo box
  additionally offers *Auto*. The five Whisper languages NLLB-200 has no
  tag for are **not** offered: `br` Breton, `haw` Hawaiian, `la` Latin,
  `mg` Malagasy, `sq` Albanian - detection of one of them is rejected with
  a clear "not supported" message instead of being mistranslated.
* The menu keeps the original five (English, Malayalam, Hindi, Japanese,
  Korean) first, then the rest alphabetically by label.
* *Auto* resolves through Whisper's language detection, which happens
  after the recording, so the firmware receives `LANG:auto:<target>` at
  press time.
* NLLB quality varies by pair; Malayalam <- English is noticeably weaker
  than the European/Japanese/Korean pairs, and long utterances are
  truncated at 512 tokens.
* Speech **recognition** needs the matching language spoken; the source
  combo box constrains what gets translated, not what Whisper detects.
* Firmware-side: `LANG:` accepts any colon-free token, so every host
  token (`en`, `ml`, `yue`, ..., `auto`) is always ACKed.
* UI language labels and tokens are UI-side only: the firmware and the
  UART protocol are unchanged, and neither the model weights nor
  `Core/`, `CubeMX` or Renode files were touched.

### Spoken output

Spoken output uses installed Windows (SAPI) voices (`pywin32`). A voice is
picked by Windows primary language ID (LANGID & 0x3FF), e.g. English
0x09, Malayalam 0x4C, Hindi 0x39, Japanese 0x11, Korean 0x12, Tamil
0x49. Eight targets have **no Windows locale at all** - Cantonese,
Haitian Creole, Javanese, Lingala, Shona, Sundanese, Tagalog and Yiddish
- and are always captions-only. Typical Windows installs ship English +
Japanese only, so e.g. Malayalam shows

> no Windows voice for Malayalam; captions only (installed: ...)

Captions keep working either way; add voices via
*Settings > Time & Language > Speech* (or the "Add a voice" link in
Windows). Disable spoken output with the checkbox or `RESTS_SPEECH=0`.

## 8. Tests

```powershell
powershell -ExecutionPolicy Bypass -File host_app\test.ps1
```

Focused tests with fake devices (no microphone, no Renode, no models):
protocol framing/handshake against a fake UART socket device, PTT command
order, the 95-language mapping, translation-service interfaces, CUDA
error detection with the automatic CPU fallback, audio/temp-file cleanup
and voice matching.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `error - connect failed: [WinError 10061]` | Renode is not running: start `launch_renode.ps1`. |
| Handshake timeout / `RESTS/1` never arrives | Wrong port (use 12345), or the ELF was not loaded; check the Renode monitor. |
| `ELF not found` from the launch script | Build the *Debug* configuration (`Debug\RESTS_STM32.elf`) first. |
| `speech model ... not available locally` | Run `host_app\download_models.py` once (needs internet). |
| `transformers is not installed` / `faster-whisper ...` | Re-run `host_app\setup.ps1` (without `-DevOnly`). |
| `Library cublas64_12.dll is not found or cannot be loaded` | The NVIDIA *driver* is installed but the CUDA 12 runtime (cuBLAS/cuDNN) is not. In `auto` mode the recording is simply retried on CPU and the status bar shows `CUDA unavailable (...); speech recognition switched to CPU`. Force CPU with `--asr-device cpu`, `RESTS_ASR_DEVICE=cpu` or the **Device** box; install the CUDA 12.x toolkit + cuDNN to use the GPU. |
| First transcription is slow on CPU | Expected (~seconds for `medium`); use `RESTS_WHISPER_MODEL=Systran/faster-whisper-small` for speed, or install the CUDA 12 runtime. |
| CUDA not detected / `cuda runtime missing` | Expected without a CUDA install; `auto` runs on CPU (int8) and `--self-check` prints `compute device` and `cuda runtime`. |
| The language I want is missing from the combo box | Only the 95 languages known to *both* models are offered (§7); the five omitted ones have no NLLB-200 tag. |
| Microphone error at press | Windows > Privacy > Microphone: allow desktop apps; check the default input device. |
| Recording ignored (too short) | Hold the key/button for at least 0.3 s. |
| `firmware error: ERR:...` in the status bar | The firmware rejected the last command; the reply is shown verbatim. |
| Port 12345 already in use | Stop the old Renode, or change both `$port` in the `.resc`/`-Port` and `RESTS_UART_PORT`. |
| `No module named tkinter` | Install Python 3.11 *with* tcl/tk (default installer). |
| `Required robotframework ...` (Renode test runner) | Unrelated to this app; only the MCU `tests\*.robot` suite needs it. |
| Speech unavailable for a target | No installed voice for that language - see §7; captions still work. |

## 10. Manual checks still required

These cannot be automated here and should be verified on the target
machine:

1. **Live firmware run**: `launch_renode.ps1` + `run.ps1 --self-check
   --probe-uart` must print `handshake + PING/PONG OK`, then a real
   hold-to-talk session must show `ACK:PTT:BEGIN` / `ACK:PTT:END`.
2. **Model download & inference**: `download_models.py` on a machine with
   internet, then one transcription + one translation (GPU path if you
   have CUDA).
3. **Microphone**: real capture through PortAudio and Windows privacy
   settings.
4. **Spoken output**: confirm the target language has an installed voice
   and that `speak()` produces audio.
5. **CUDA fallback**: on a machine whose CUDA runtime is missing,
   `--self-check` must print `cuda runtime missing: ...` and a recording
   must still transcribe (CPU), with the `CUDA unavailable ...` notice in
   the status bar. With `--asr-device cuda` the same recording must fail
   with the actionable "install the NVIDIA CUDA 12.x toolkit" message.

## 11. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RESTS_UART_HOST` / `RESTS_UART_PORT` | `127.0.0.1` / `12345` | Renode socket |
| `RESTS_PTT_KEY` | `F9` | hold-to-talk keyboard shortcut |
| `RESTS_SPEECH` | `1` | `0` disables spoken output |
| `RESTS_ASR_DEVICE` | `auto` | `auto`, `cpu` or `cuda` speech-recognition device |
| `RESTS_MODEL_DIR` | `host_app\models` | model cache location |
| `RESTS_WHISPER_MODEL` | `Systran/faster-whisper-medium` | ASR checkpoint |
| `RESTS_NLLB_MODEL` | `facebook/nllb-200-distilled-600M` | translation checkpoint |
| `RESTS_TRANSLATION_BACKEND` | `nllb` | `nllb` or `passthrough` |

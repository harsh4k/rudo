# Rudo — Project Context

Instructions for any coding agent working in this repository.

## What this is

Rudo is a personal, Jarvis-style AI assistant that runs in the terminal. It talks to a
local model through [Ollama](https://ollama.com) (`gemma3:4b`, wrapped as the `rudo`
model by `Modelfile`). It streams replies with Markdown rendering, keeps chat history on
disk, can call a small set of tools, answers from the user's own notes (local
embeddings), and supports voice in and out.

Everything runs on the user's machine. The only network calls are to the local Ollama
server and, when the `web` tool or `/web` is used, to DuckDuckGo.

## Stack

| Area | Choice |
|---|---|
| Language | Python 3.12+ (`math.sumprod` needs 3.12) |
| Model runtime | Ollama HTTP API via `urllib` (no Ollama SDK) |
| Terminal UI | `rich` (Live, Markdown, Table, status spinners) |
| System stats | `psutil` (optional; degrades to clock-only) |
| Web search | `ddgs` (optional; no API key) |
| Voice in | `faster-whisper` + `sounddevice` (optional) |
| Voice out, clipboard | OS tools: SAPI/PowerShell, `say`/`pbpaste`, espeak-ng/spd-say + wl-paste/xclip |
| Notes search | `nomic-embed-text` embeddings, stored in `rag.json`, cosine scan in pure Python |

There is no server, no database and no web UI. Do not add one.

## Layout

```
rudo.py              The whole app: Ollama client, history, platform helpers, tools,
                     voice, boot screen, chat loop (section banners separate them)
test_rudo.py         pytest suite, incl. end-to-end chat tests against a stub Ollama
Modelfile            Base model, sampling params, system prompt (persona)
rudo.cmd             Windows launcher so `rudo` works from any shell on PATH
requirements.txt     Runtime dependencies
requirements-dev.txt pytest + ruff
pyproject.toml       ruff + pytest config only (not an installable package)
.github/workflows/   CI: ruff + pytest on Windows/macOS/Linux x py3.12/3.13
```

Runtime files written next to `rudo.py` (both are personal data and must stay out of git):
`history.json` (chat history) and `rag.json` (indexed notes text + vectors).

## Commands

```bash
ollama create rudo -f Modelfile     # build the model after editing Modelfile
pip install -r requirements.txt
python rudo.py                      # run (add --fast to skip the boot animation)
RUDO_OLLAMA=http://host:11434 python rudo.py   # point at another Ollama

pip install -r requirements-dev.txt
python -m pytest -W error           # tests (no Ollama needed)
ruff check .                        # lint; don't run `ruff format`, it restyles everything
```

## How the tool loop works

1. Each request sends a system message with live status (`sysinfo()`) plus `TOOL_PROMPT`.
2. If the reply contains a single-line JSON object naming a known tool
   (`find_tool_call`), `run_tool` executes it and the result goes back as a
   `TOOL RESULT (...)` user message.
3. At most 3 tool rounds per user message.

Rules for tools:
- New tools are added to the `TOOLS` dict and `TOOL_PROMPT` together, with a test.
- A tool never raises into the chat loop; failures come back as strings.
- Anything that changes the machine or reads private data asks the user first via
  `confirm()` (`shell`, `read`). Default is no; Ctrl+C or EOF at the prompt is no.
- Tool output is untrusted input to the model. Once private data has been in the
  context (`has_private()`: read/notes/shell results, `/notes`, `/clip`), a
  model-initiated `web` call must be confirmed. The flag is sticky for the session.
  Never let one tool's output silently trigger another privileged tool.
- Bare tool-call replies are hidden while streaming (`maybe_tool_call`) and shown as a
  one-line `tool_trace` instead of raw JSON.

## Conventions

- Keep it a small, dependency-light script. Ask before adding a package; optional
  features import lazily and degrade with a clear message (see `tool_web`, `voice_input`).
- Match the existing style: section banners (`# ----- tools`), short docstrings,
  type hints, `ponytail:` comments mark known shortcuts and the trigger to revisit them.
- UI colours have meaning: green = user, cyan = Rudo, red = errors/alerts,
  yellow = in-progress / needs confirmation, dim = metadata.
- The UI must work in an 80-column terminal and when piped (`FAST` mode).
- All OS-specific code lives in the `platform` section of `rudo.py`
  (`clipboard_text`, `tts_command`, `beep`, `SHELL_KIND`) and degrades with an install
  hint instead of raising. Don't scatter `IS_WIN` checks elsewhere.

## Definition of done

- `ruff check .` and `python -m pytest -W error` pass, with a regression test for any
  bug fixed. CI is green on all six jobs.
- The app was actually run (against real Ollama, or a stub server when Ollama is not
  available) and the changed path was exercised.
- README updated when commands, tools or setup change.

## Status and roadmap

Done (Oct 2026): safety fixes (confirm `read`, web-search guard, `/notes` crash,
stream timeout, `rag.json` ignored), UX polish (no raw tool JSON, usage hints,
80-column help, clean trimming), pytest + ruff + CI, macOS/Linux support, README and
MIT license.

Open:
- Smoke test against real Ollama + Gemma on Windows (CI uses a stub server).
- Tool results are tagged by a `TOOL RESULT` text prefix; Ollama's `tool` role would
  be more robust but needs testing with the Gemma chat template first.
- Timer alerts print over the input line (stdlib `input()` can't redraw it).

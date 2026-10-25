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
| Voice out, clipboard | Windows PowerShell / SAPI (Windows-only today) |
| Notes search | `nomic-embed-text` embeddings, stored in `rag.json`, cosine scan in pure Python |

There is no server, no database and no web UI. Do not add one.

## Layout

```
rudo.py          The whole app: Ollama client, history, tools, voice, boot screen, chat loop
test_rudo.py     Tool-call parsing and dispatch checks
Modelfile        Base model, sampling params, system prompt (persona)
rudo.cmd         Windows launcher so `rudo` works from any shell on PATH
requirements.txt Runtime dependencies
```

Runtime files written next to `rudo.py` (both are personal data and must stay out of git):
`history.json` (chat history) and `rag.json` (indexed notes text + vectors).

## Commands

```bash
ollama create rudo -f Modelfile     # build the model after editing Modelfile
pip install -r requirements.txt
python rudo.py                      # run (add --fast to skip the boot animation)
python test_rudo.py                 # tests
RUDO_OLLAMA=http://host:11434 python rudo.py   # point at another Ollama
```

There is no lint or CI config yet; see the roadmap.

## How the tool loop works

1. Each request sends a system message with live status (`sysinfo()`) plus `TOOL_PROMPT`.
2. If the reply contains a single-line JSON object naming a known tool
   (`find_tool_call`), `run_tool` executes it and the result goes back as a
   `TOOL RESULT (...)` user message.
3. At most 3 tool rounds per user message.

Rules for tools:
- New tools are added to the `TOOLS` dict and `TOOL_PROMPT` together, with a test.
- A tool never raises into the chat loop; failures come back as strings.
- Anything that changes the machine or reads private data asks the user first
  (`shell` already does). Tool output from the web or files is untrusted input to the
  model, so never let one tool's output silently trigger another privileged tool.

## Conventions

- Keep it a small, dependency-light script. Ask before adding a package; optional
  features import lazily and degrade with a clear message (see `tool_web`, `voice_input`).
- Match the existing style: section banners (`# ----- tools`), short docstrings,
  type hints, `ponytail:` comments mark known shortcuts and the trigger to revisit them.
- UI colours have meaning: green = user, cyan = Rudo, red = errors/alerts,
  yellow = in-progress / needs confirmation, dim = metadata.
- The UI must work in an 80-column terminal and when piped (`FAST` mode).
- Platform-specific code (PowerShell, `winsound`) is guarded so the app still runs
  elsewhere.

## Definition of done

- `python test_rudo.py` passes, with a regression test for any bug fixed.
- The app was actually run (against real Ollama, or a stub server when Ollama is not
  available) and the changed path was exercised.
- README updated when commands, tools or setup change.

## Roadmap

Tracked in the project thread's audit. In order:
1. Correctness and safety: confirm before the `read` tool, stop `/notes` crashing when
   Ollama drops, git-ignore `rag.json`, timeouts on the chat stream.
2. UX polish: hide raw tool-call JSON, usage hints for `/web` and `/notes` with no
   query, help table that fits 80 columns, history trimming on message boundaries.
3. Project hygiene: `pyproject.toml` with ruff, pytest suite, GitHub Actions CI,
   README and LICENSE refresh.
4. Optional: macOS/Linux support for clipboard, speech and timer beeps.

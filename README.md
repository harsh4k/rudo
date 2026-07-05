# Rudo

Personal AI assistant (Jarvis-style) built on Gemma 3 via Ollama, with a
terminal chat UI: boot animation, streaming replies, markdown + code
rendering, persistent memory, tools (shell, web search, file read, timers,
personal notes), voice in/out, and live system awareness.

## Interface

<img width="1893" height="958" alt="image" src="https://github.com/user-attachments/assets/254662a7-7184-4154-8dde-725395192d3d" />

Colors mean things: **green** is you, **cyan** is Rudo, **red** is alerts.

## Requirements

- [Ollama](https://ollama.com) running locally
- `gemma3:4b` pulled
- `nomic-embed-text` pulled (only for `/index` + `/notes`)
- Python 3.12+ with `pip install -r requirements.txt`

## Setup

    ollama create rudo -f Modelfile
    pip install -r requirements.txt

## Run

    python rudo.py

or just:

    rudo

The boot sequence is real: it checks Ollama is reachable, the `rudo` model
exists, and restores chat history from `history.json`.

## In-chat commands

| Command | Action |
|---------|--------|
| `/new`  | Wipe memory (chat history) |
| `cls`   | Clear the screen |
| `/v` | Voice input — talk, press Enter to stop |
| `/speak` | Toggle spoken replies |
| `/clip [question]` | Ask about whatever is on your clipboard |
| `/web <query>` | Web-search, answer from the results |
| `/index <folder>` | Index a folder of `.md`/`.txt` notes |
| `/notes <question>` | Answer from your indexed notes |
| `/help` | List commands |
| `/quit` | Exit (Ctrl+C works too) |

`--fast` skips the boot animation delays.

## Tools

Rudo can act, not just talk. When a question needs it, the model emits a
one-line JSON tool call that rudo.py executes and feeds back:

- **shell** — run a Windows command (you confirm y/N first, always)
- **read** — read a text file
- **web** — DuckDuckGo search, no API key
- **notes** — semantic search over your `/index`ed notes
- **timer** — countdown that beeps

Every request also carries live system status (time, CPU, RAM, battery,
cwd), so "how's my battery?" just works.

## Customize

Edit the `SYSTEM` and `PARAMETER` lines in `Modelfile`, then rebuild:

    ollama create rudo -f Modelfile

## Tests

    python test_rudo.py

Smallest checks that fail if the tool-call parsing/dispatch breaks.

## Specs it was tuned for

Dell G15 5530 · i7-13650HX · 16 GB RAM · RTX 3050 6 GB.
Context set to 8192 to stay within RAM headroom.

# Rudo

Personal AI assistant (Jarvis-style) built on Gemma 4 via Ollama, with a
terminal chat UI: boot animation, streaming replies, markdown + code
rendering, and persistent memory.

## Requirements

- [Ollama](https://ollama.com) running locally
- `gemma4:latest` pulled
- Python 3.10+ with `pip install rich`

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
| `/help` | List commands |
| `/quit` | Exit (Ctrl+C works too) |

`--fast` skips the boot animation delays.

## Customize

Edit the `SYSTEM` and `PARAMETER` lines in `Modelfile`, then rebuild:

    ollama create rudo -f Modelfile

## Specs it was tuned for

Dell G15 5530 · i7-13650HX · 16 GB RAM · RTX 3050 6 GB.
Context set to 8192 to stay within RAM headroom.

"""Rudo — Jarvis-style terminal assistant on Ollama."""

import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # piped/legacy consoles default to cp1252

OLLAMA = os.environ.get("RUDO_OLLAMA", "http://localhost:11434")
MODEL = "rudo"
HISTORY_FILE = Path(__file__).parent / "history.json"
MAX_HISTORY = 40  # ponytail: message-count trim; token-accurate budgeting if 8k ctx ever overflows

console = Console()
# skip animation delays when piped or asked to hurry
FAST = "--fast" in sys.argv or not sys.stdout.isatty()
SESSION_ID = f"0x{random.randrange(16 ** 4):04X}"  # ghost-style session tag, new each launch


def stamp() -> str:
    return time.strftime("%H:%M:%S")

BANNER = """\
██████╗ ██╗   ██╗██████╗  ██████╗
██╔══██╗██║   ██║██╔══██╗██╔═══██╗
██████╔╝██║   ██║██║  ██║██║   ██║
██╔══██╗██║   ██║██║  ██║██║   ██║
██║  ██║╚██████╔╝██████╔╝╚██████╔╝
╚═╝  ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝"""


# ---------------------------------------------------------------- ollama api

def check_core() -> dict:
    """Ollama reachable? Returns /api/tags payload."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as r:
            return json.load(r)
    except OSError:
        raise RuntimeError("CORE OFFLINE — start Ollama and retry.")


def check_model(tags: dict) -> None:
    names = [m["name"] for m in tags.get("models", [])]
    if not any(n == MODEL or n.startswith(f"{MODEL}:") for n in names):
        raise RuntimeError(f"model '{MODEL}' not found — run: ollama create rudo -f Modelfile")


def warm_model() -> None:
    """Best-effort: make Ollama load the model while the boot animation plays."""
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        data=json.dumps({"model": MODEL, "keep_alive": "30m"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=120).read()
    except OSError:
        pass  # boot checks surface real errors; warmup stays silent


def stream_reply(messages: list[dict]):
    """Yield content tokens from a streaming /api/chat call."""
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps({"model": MODEL, "messages": messages, "stream": True,
                         "keep_alive": "30m"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        for line in r:
            chunk = json.loads(line)
            if content := chunk.get("message", {}).get("content"):
                yield content
            if chunk.get("done"):
                break


# ------------------------------------------------------------------- history

def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_history(history: list[dict]) -> None:
    HISTORY_FILE.write_text(json.dumps(history, indent=1), encoding="utf-8")


# ------------------------------------------------------------- system status

def sysinfo() -> str:
    """Live machine context injected into every request."""
    now = time.strftime("%A %d %B %Y, %H:%M")
    try:
        import psutil
    except ImportError:  # ponytail: degrade to clock-only rather than die over a stats lib
        return f"LIVE STATUS: {now}"
    batt = psutil.sensors_battery()
    power = f" · battery {batt.percent:.0f}%{'' if batt.power_plugged else ' UNPLUGGED'}" if batt else ""
    return (f"LIVE STATUS: {now} · CPU {psutil.cpu_percent():.0f}%"
            f" · RAM {psutil.virtual_memory().percent:.0f}%{power} · cwd {os.getcwd()}")


# ------------------------------------------------------- personal notes (rag)

RAG_FILE = Path(__file__).parent / "rag.json"
EMBED_MODEL = "nomic-embed-text"


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)["embeddings"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"embedding failed ({e.code}) — run: ollama pull {EMBED_MODEL}") from e


def index_folder(folder: str) -> int:
    """Chunk every .txt/.md under folder, embed, save to rag.json. Returns chunk count."""
    chunks = []
    for p in Path(folder).expanduser().rglob("*"):
        if p.suffix.lower() not in (".txt", ".md") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        # ponytail: fixed-size chunks with overlap; semantic splitting when retrieval disappoints
        for i in range(0, len(text), 1200):
            chunks.append({"src": p.name, "text": text[i:i + 1500]})
    for i in range(0, len(chunks), 32):
        batch = chunks[i:i + 32]
        for chunk, vec in zip(batch, embed([c["text"] for c in batch])):
            chunk["vec"] = vec
    RAG_FILE.write_text(json.dumps(chunks), encoding="utf-8")
    return len(chunks)


def tool_notes(query: str) -> str:
    if not RAG_FILE.exists():
        return "no notes indexed yet — the user must run /index <folder> first"
    chunks = json.loads(RAG_FILE.read_text(encoding="utf-8"))
    if not chunks:
        return "notes index is empty"
    try:
        qv = embed([query])[0]
    except RuntimeError as e:
        return str(e)

    def cos(a: list[float], b: list[float]) -> float:
        return math.sumprod(a, b) / (math.hypot(*a) * math.hypot(*b) + 1e-9)

    # ponytail: pure-python scan; numpy matrix math if the index outgrows a few thousand chunks
    top = sorted(chunks, key=lambda c: cos(qv, c["vec"]), reverse=True)[:3]
    return "\n\n".join(f"[{c['src']}]\n{c['text']}" for c in top)


# --------------------------------------------------------------------- tools

TOOL_PROMPT = """\
TOOLS — when the answer needs live data or an action, reply with ONLY one JSON line:
{"tool": "shell", "arg": "<windows command>"} — run a command on this PC (user confirms first)
{"tool": "read", "arg": "<file path>"} — read a text file
{"tool": "web", "arg": "<search query>"} — search the live web
{"tool": "notes", "arg": "<query>"} — search the user's indexed personal notes
{"tool": "timer", "arg": "<seconds>"} — set a countdown timer
A TOOL RESULT message comes back; then answer the user normally. No tool for chit-chat."""


def tool_shell(arg: str) -> str:
    console.print(Text(f"rudo wants to run: {arg}", "bold yellow"))
    if console.input("[yellow]allow? y/N [/]").strip().lower() != "y":
        return "user declined to run the command"
    done = subprocess.run(arg, shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    return (done.stdout + done.stderr).strip()[:4000] or "(command ran, no output)"


def tool_read(arg: str) -> str:
    try:
        return Path(arg.strip('"')).expanduser().read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError as e:
        return f"error: {e}"


def tool_web(arg: str) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "web search unavailable — pip install ddgs"
    try:
        hits = list(DDGS().text(arg, max_results=5))
    except Exception as e:
        return f"search failed: {e}"
    return "\n".join(f"- {h['title']}: {h['body']} [{h['href']}]" for h in hits) or "no results"


def tool_timer(arg: str) -> str:
    try:
        secs = float(arg.strip())
    except ValueError:
        return f"error: arg must be a number of seconds, got {arg!r}"

    def ding() -> None:  # ponytail: prints over whatever's on screen; it's an alarm, that's the job
        try:
            import winsound
            for _ in range(3):
                winsound.MessageBeep()
                time.sleep(0.4)
        except Exception:
            pass
        console.print(f"\n[bold green]⏰ TIMER DONE ({secs:.0f}s)[/]")

    t = threading.Timer(secs, ding)
    t.daemon = True  # ponytail: /quit kills pending timers; persistence needs a real scheduler
    t.start()
    return f"timer set for {secs:.0f} seconds"


TOOLS = {"shell": tool_shell, "read": tool_read, "web": tool_web,
         "notes": tool_notes, "timer": tool_timer}


def find_tool_call(reply: str) -> dict | None:
    """First JSON line in the reply that names a known tool, else None."""
    for line in reply.splitlines():
        line = line.strip().strip("`").strip()
        if line.startswith("{") and '"tool"' in line:
            try:
                call = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(call, dict) and call.get("tool") in TOOLS:
                return call
    return None


def run_tool(call: dict) -> str:
    try:
        return TOOLS[call["tool"]](str(call.get("arg", "")))
    except Exception as e:  # a tool crash becomes context for the model, not a crash
        return f"tool error: {e}"


# --------------------------------------------------------------------- voice

SPEAK = False  # toggled by /speak
_whisper = None


def voice_input() -> str:
    """Record mic until Enter, transcribe with faster-whisper. '' on any failure."""
    global _whisper
    try:
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel
    except ImportError:
        console.print("[bold red]voice needs:[/] pip install faster-whisper sounddevice")
        return ""
    frames: list = []
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                        callback=lambda data, *_: frames.append(data.copy())):
        console.input("[bold yellow]● recording[/] [dim]— Enter to stop[/] ")
    if not frames:
        return ""
    audio = np.concatenate(frames).flatten()
    if _whisper is None:
        with console.status("[yellow]loading whisper (first run downloads ~75MB)…[/]"):
            _whisper = WhisperModel("base", compute_type="int8")
    with console.status("[yellow]transcribing…[/]"):
        segments, _ = _whisper.transcribe(audio)
        return " ".join(seg.text for seg in segments).strip()


def speak(text: str) -> None:
    # windows SAPI via powershell — no tts dependency needed
    ps = ("Add-Type -AssemblyName System.Speech;"
          "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak([Console]::In.ReadToEnd())")
    try:
        subprocess.run(["powershell", "-NoProfile", "-c", ps],
                       input=re.sub(r"[*_`#|>\[\]]", "", text), text=True)
    except KeyboardInterrupt:
        pass  # ctrl+c stops the speech, not the app


# ---------------------------------------------------------------------- boot

def boot_sequence(history: list[dict]) -> None:
    threading.Thread(target=warm_model, daemon=True).start()  # load model during the theater
    if FAST:  # no theater when piped or hurried — just the health checks
        try:
            check_model(check_core())
        except RuntimeError as e:
            console.print(f"[bold red]{e}[/]")
            sys.exit(1)
        return

    log = Text()
    error = None
    # screen=True → alternate buffer: the whole boot scene vanishes when done
    with Live(screen=True, console=console, refresh_per_second=24) as live:

        def show(cursor: bool = True) -> None:
            frame = log.copy()
            if cursor:
                frame.append("█", "green")  # CRT block cursor rides the typing position
            live.update(Padding(frame, (2, 0, 0, 6)))  # top-left block like a real POST screen

        def type_line(text: str, style: str = "grey85", pause: float = 0.25) -> None:
            for ch in text:
                log.append(ch, style)
                show()
                time.sleep(0.012)
            log.append("\n")
            show()
            time.sleep(pause)

        type_line("BOOT UP SEQUENCE READY", pause=0.6)
        type_line("")
        try:
            type_line("NEURAL CORE", pause=0.15)
            tags = check_core()
            type_line("  LINK ESTABLISHED", "bold green")
            type_line("")
            type_line("PERSONALITY MATRIX", pause=0.15)
            check_model(tags)
            type_line("  RUDO LOADED", "bold green")
            type_line("")
            type_line("MEMORY BANKS", pause=0.15)
            type_line(f"  {len(history)} MESSAGES RESTORED", "bold green")
            type_line("")
            type_line("RUNNING DIAGNOSTICS", pause=0.4)
            type_line("")
            for label in ("INPUT", "RENDERER", "MEMORY"):  # ponytail: theater — nothing real to probe
                log.append(f"  {label:<14}", "grey85")
                type_line("OK", "bold green", pause=0.18)
            type_line("")
            type_line("ALL SYSTEMS OPERATIONAL", "bold green", pause=0.3)
            log.append("LOADING RUDO", "grey85")
            for _ in range(3):
                log.append(".", "grey85")
                show()
                time.sleep(0.3)
            # cursor blinks off/on twice, then the scene hands over to the banner
            for cur in (False, True, False, True):
                show(cursor=cur)
                time.sleep(0.25)
        except RuntimeError as e:
            error = str(e)
            type_line(f"  {e}", "bold red", pause=1.5)

        if not error:
            try:
                import winsound
                winsound.MessageBeep()  # ponytail: single boot chime; per-keystroke sounds are annoying
            except Exception:
                pass
            time.sleep(0.8)

    if error:
        console.print(f"[bold red]{error}[/]")
        sys.exit(1)


# ---------------------------------------------------------------------- chat

THINKING = ("Flabbergasting", "Pondering", "Ruminating", "Percolating", "Noodling",
            "Marinating", "Cogitating", "Conjuring", "Scheming", "Mulling", "Brewing")

COMMANDS = (
    ("/new", "wipe memory, start fresh"),
    ("cls", "clear the screen"),
    ("/v", "voice input — talk, Enter to stop"),
    ("/speak", "toggle spoken replies"),
    ("/clip", "ask about your clipboard"),
    ("/web q", "web-search q, answer from results"),
    ("/index d", "index folder d of .md/.txt notes"),
    ("/notes q", "answer q from your indexed notes"),
    ("/help", "list commands"),
    ("/quit", "exit · /exit · ctrl+c"),
)


def command_lines() -> Table:
    # two command pairs per row — full width, no box
    grid = Table.grid(padding=(0, 2))
    for i in range(0, len(COMMANDS), 2):
        row = []
        for cmd, desc in COMMANDS[i:i + 2]:
            row += [Text(f"{cmd:<9}", "bold cyan"), Text(f"{desc:<36}", "dim")]
        grid.add_row(*row)
    return grid


def sign_off() -> None:
    console.print("\n[dim green]RUDO OFFLINE.[/]")


def print_header(history: list[dict]) -> None:
    console.clear()  # chat owns the whole screen; no boot residue
    # ghost-style: logo top-left, session block top-right — theme: cyan rudo, green user, red alerts
    session = Text(justify="right")
    session.append(f"SESSION: {SESSION_ID} // PID: {os.getpid()}\n", "green")
    session.append(f"HOST: {OLLAMA.removeprefix('http://')}\n", "dim green")
    session.append(time.strftime("%Y-%m-%d %H:%M:%S"), "dim green")
    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column()
    top.add_column(justify="right")
    top.add_row(Text(BANNER, style="bold bright_cyan"), session)
    console.print(top)
    console.print()
    console.rule("[dim cyan][ TERMINAL // RUDO // MODE: CHAT ][/]", style="dim cyan", align="left")
    console.print()
    console.print(Text("—— SYSTEM ONLINE", "bold bright_green"))
    for line in (f"MODEL: {MODEL} // ollama",
                 f"MEMORY: {len(history)} messages restored",
                 f"NOTES: {'indexed' if RAG_FILE.exists() else 'none — /index <folder>'}"):
        console.print(Text(f"  • {line}", "green"))
    console.print()
    console.print(command_lines())


def converse(history: list[dict]) -> str:
    """Stream one reply (spinner + live markdown). Empty string on failure."""
    reply = ""
    console.print()
    try:
        messages = [{"role": "system", "content": f"{sysinfo()}\n\n{TOOL_PROMPT}"}] + history
        gen = stream_reply(messages)
        with console.status("", spinner="dots") as status:
            stop = threading.Event()

            def spin() -> None:  # claude-code-style whimsy while the model warms up
                word, beat = random.choice(THINKING), 0
                while not stop.is_set():
                    status.update(f"[yellow]{word}…[/] [dim]({beat}s · ctrl+c to interrupt)[/]")
                    if stop.wait(1):
                        break
                    beat += 1
                    if beat % 4 == 0:
                        word = random.choice(THINKING)

            threading.Thread(target=spin, daemon=True).start()
            try:
                reply = next(gen, "")
            finally:
                stop.set()
        console.print(Text.assemble((f"[{stamp()}] ", "dim"), ("< RUDO:", "bold bright_cyan")))
        with Live(Markdown(reply, style="cyan"), console=console, refresh_per_second=10,
                  vertical_overflow="visible") as live:
            for token in gen:
                reply += token
                live.update(Markdown(reply, style="cyan"))
    except KeyboardInterrupt:
        console.print("[dim]— interrupted[/]")
    except (OSError, json.JSONDecodeError) as e:
        console.print(f"[bold red]Link lost:[/] {e}")
    return reply


def chat(history: list[dict]) -> None:
    global SPEAK
    if not FAST:
        print_header(history)
    while True:
        console.print()
        try:
            user = console.input(f"[dim]\\[{stamp()}][/] [bold bright_green]>[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            sign_off()
            return
        if not user:
            continue
        if user in ("/quit", "/exit"):
            sign_off()
            return
        if user == "/help":
            console.print(command_lines())
            continue
        if user.lower() in ("cls", "/cls", "clear"):
            print_header(history)
            continue
        if user == "/new":
            history.clear()
            save_history(history)
            console.print("[dim]Memory banks wiped.[/]")
            continue
        if user == "/speak":
            SPEAK = not SPEAK
            console.print(f"[dim]spoken replies {'on' if SPEAK else 'off'}[/]")
            continue
        if user.split()[0] == "/index":
            folder = user.removeprefix("/index").strip() or "."
            try:
                with console.status("[yellow]indexing…[/]"):
                    n = index_folder(folder)
                console.print(f"[green]{n} chunks indexed from {folder}[/]")
            except (OSError, RuntimeError) as e:
                console.print(f"[bold red]index failed:[/] {e}")
            continue

        # commands that turn into a model prompt, then fall through to the reply
        if user == "/v":
            user = voice_input()
            if not user:
                continue
            console.print(Text.assemble((f"[{stamp()}] ", "dim"),
                                        ("> ", "bold bright_green"), (user, "italic")))
        elif user.split()[0] == "/clip":
            clip = subprocess.run(["powershell", "-NoProfile", "-c", "Get-Clipboard"],
                                  capture_output=True, text=True).stdout
            if not clip.strip():
                console.print("[dim]clipboard is empty[/]")
                continue
            ask = user.removeprefix("/clip").strip() or "Explain or summarize this for me."
            user = f"{ask}\n\nMy clipboard contents:\n```\n{clip[:4000]}\n```"
        elif user.startswith("/web "):
            q = user.removeprefix("/web ").strip()
            with console.status("[yellow]searching…[/]"):
                results = tool_web(q)
            user = f"Web search results for '{q}':\n{results}\n\nUsing these results, answer: {q}"
        elif user.startswith("/notes "):
            q = user.removeprefix("/notes ").strip()
            with console.status("[yellow]searching notes…[/]"):
                results = tool_notes(q)
            user = f"Relevant excerpts from my notes:\n{results}\n\nUsing these, answer: {q}"
        if user.startswith("/"):
            console.print(f"[red]unknown command:[/] {user} — try /help")
            continue

        history.append({"role": "user", "content": user})
        reply = converse(history)
        if not reply:
            history.pop()  # failed exchange — don't poison the context
        else:
            history.append({"role": "assistant", "content": reply})
            for _ in range(3):  # ponytail: 3 tool rounds max; enough for lookup + retry
                call = find_tool_call(reply)
                if not call:
                    break
                result = run_tool(call)
                console.print(Text(f"⚙ {call['tool']}: {' '.join(result.split())[:200]}", "dim"))
                history.append({"role": "user", "content": f"TOOL RESULT ({call['tool']}):\n{result}"})
                reply = converse(history)
                if not reply:
                    break
                history.append({"role": "assistant", "content": reply})
            if SPEAK and reply:
                speak(reply)
        del history[:-MAX_HISTORY]
        save_history(history)


def main() -> None:
    history = load_history()
    boot_sequence(history)
    chat(history)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sign_off()

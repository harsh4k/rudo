"""Rudo — Jarvis-style terminal assistant on Ollama."""

import json
import os
import random
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
from rich.panel import Panel
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
    ("/history", "show the conversation log"),
    ("/help", "list commands"),
    ("/quit", "exit · /exit · ctrl+c"),
)


def command_lines() -> Text:
    return Text("\n").join(
        Text.assemble((f"{cmd:<10}", "bold cyan"), (desc, "grey70")) for cmd, desc in COMMANDS
    )


def convo_panel(history: list[dict]) -> Panel:
    # ponytail: static snapshot; a live sidebar needs a full TUI rewrite
    prompts = [m["content"].replace("\n", " ") for m in history if m["role"] == "user"]
    cut = max(24, console.width // 4)  # one line per prompt at any terminal width
    lines = [Text("Recents", "bold grey70"), Text()] + ([
        Text.assemble(("▸ ", "cyan"), (p[:cut] + ("…" if len(p) > cut else ""), "grey85"))
        for p in prompts[-10:]
    ] or [Text("no conversations yet", "dim")])
    return Panel(Text("\n").join(lines), title="[bold cyan]CONVO LOG[/]",
                 subtitle=f"[dim]{len(prompts)} prompts[/]", border_style="cyan")


def sign_off() -> None:
    console.print("\n[dim cyan]RUDO OFFLINE.[/]")


def chat(history: list[dict]) -> None:
    if not FAST:
        console.clear()  # chat owns the whole screen; no boot residue
    info = Text()
    info.append("RUDO ONLINE\n", "bold bright_cyan")
    info.append(f"model {MODEL} · {len(history)} messages restored\n\n", "dim")
    info.append_text(command_lines())
    # chatgpt-style header stretched to the full terminal: sidebar | banner | status
    # ponytail: below ~100 cols the banner crops; fine for a full-screen terminal app
    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(ratio=2)
    grid.add_column(ratio=3, justify="center")
    grid.add_column(ratio=2)
    grid.add_row(convo_panel(history), Text(BANNER, style="bold bright_cyan"),
                 Panel(info, border_style="cyan"))
    console.print(grid)
    while True:
        console.print()
        try:
            user = console.input("[bold bright_cyan]❯ [/]").strip()
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
        if user == "/history":
            console.print(convo_panel(history))
            continue
        if user == "/new":
            history.clear()
            save_history(history)
            console.print("[dim]Memory banks wiped.[/]")
            continue
        if user.startswith("/"):
            console.print(f"[red]unknown command:[/] {user} — try /help")
            continue

        history.append({"role": "user", "content": user})
        reply = ""
        console.print()
        try:
            gen = stream_reply(history)
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
            with Live(Markdown(reply), console=console, refresh_per_second=10,
                      vertical_overflow="visible") as live:
                for token in gen:
                    reply += token
                    live.update(Markdown(reply))
        except KeyboardInterrupt:
            console.print("[dim]— interrupted[/]")
        except (OSError, json.JSONDecodeError) as e:
            console.print(f"[bold red]Link lost:[/] {e}")

        if reply:
            history.append({"role": "assistant", "content": reply})
        else:
            history.pop()  # failed exchange — don't poison the context
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

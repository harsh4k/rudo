"""Rudo tests: pytest. No Ollama needed — a stub server stands in for it."""
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import rudo


@pytest.fixture
def quiet(monkeypatch):
    """Swap the global console for one that records to a buffer, 80 columns wide."""
    console = rudo.Console(file=io.StringIO(), width=80, record=True)
    monkeypatch.setattr(rudo, "console", console)
    return console


@pytest.fixture
def keyboard(quiet, monkeypatch):
    """Scripted answers for every console.input prompt. Running out means no keyboard."""
    answers: list[str] = []

    def fake_input(*_):
        if not answers:
            raise EOFError
        answer = answers.pop(0)
        if answer == "^C":
            raise KeyboardInterrupt
        return answer

    monkeypatch.setattr(quiet, "input", fake_input)
    return answers


@pytest.fixture
def stub_ollama(monkeypatch):
    """In-process Ollama stand-in. Append replies to the returned list; each /api/chat
    call streams the next one (the last one repeats). /api/embed returns one vector per
    input unless `embed_short` is set. Every POST body is recorded in `requests`."""
    replies: list[str] = []
    requests: list[dict] = []
    chats = [0]
    embed_short = [False]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # /api/tags
            self._send({"models": [{"name": "rudo:latest"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if self.path == "/api/embed":
                n = len(body["input"]) - embed_short[0]
                return self._send({"embeddings": [[1.0, 0.0]] * n})
            if self.path != "/api/chat":
                return self._send({})
            chats[0] += 1
            text = replies[min(chats[0], len(replies)) - 1] if replies else "ok"
            self.send_response(200)
            self.end_headers()
            for chunk in (text[:5], text[5:]):  # two chunks, like a real stream
                self.wfile.write(json.dumps({"message": {"content": chunk}}).encode() + b"\n")
            self.wfile.write(b'{"done": true}\n')

        def _send(self, payload):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(rudo, "OLLAMA", f"http://127.0.0.1:{server.server_port}")
    yield replies, requests, embed_short
    server.shutdown()
    server.server_close()


# ------------------------------------------------------------- tool parsing

def test_find_tool_call():
    assert rudo.find_tool_call('{"tool": "timer", "arg": "5"}') == {"tool": "timer", "arg": "5"}
    assert rudo.find_tool_call('```json\n{"tool": "web", "arg": "news"}\n```')["tool"] == "web"
    assert rudo.find_tool_call("plain chat reply, no tools here") is None
    assert rudo.find_tool_call('{"tool": "format_disk", "arg": "c:"}') is None


def test_bad_tool_args_return_errors():
    assert "error" in rudo.tool_timer("five minutes")


@pytest.mark.parametrize("text, held", [
    ('{"tool": "ti', True),
    ("```js", True),
    ("```json\n{", True),
    ("```powershell\nnetstat -ano", False),
    ("Sure — here's the plan", False),
])
def test_maybe_tool_call(text, held):
    assert rudo.maybe_tool_call(text) is held


def test_tool_trace_shows_tool_not_json():
    trace = rudo.tool_trace({"tool": "read", "arg": "notes.md"}, "line one\nline two").plain
    assert "read notes.md" in trace and "line one line two" in trace and "{" not in trace


# ------------------------------------------------------------- confirmation

def test_read_asks_first(keyboard):
    keyboard += ["n", "y", "y"]
    assert "declined" in rudo.run_tool({"tool": "read", "arg": __file__}, False)
    assert "pytest" in rudo.run_tool({"tool": "read", "arg": __file__}, False)
    assert "error" in rudo.run_tool({"tool": "read", "arg": "Z:/does/not/exist.txt"}, False)


@pytest.mark.parametrize("answer", ["^C", None])  # ctrl+c, or no keyboard at all
def test_refusing_a_prompt_declines(keyboard, answer):
    if answer:
        keyboard.append(answer)
    assert "declined" in rudo.run_tool({"tool": "shell", "arg": "echo hi"}, False)


def test_web_search_asks_once_private_data_is_in_context(keyboard):
    assert rudo.has_private([{"role": "user", "content": "TOOL RESULT (read):\nsecret"}])
    assert rudo.has_private([{"role": "user", "content": "TOOL RESULT (shell):\nkeys"}])
    assert rudo.has_private([{"role": "user", "content": f"x\n\n{rudo.CLIP_MARKER}\n..."}])
    assert not rudo.has_private([{"role": "user", "content": "hello"}])
    keyboard.append("n")
    assert "declined" in rudo.run_tool({"tool": "web", "arg": "secret"}, True)


# -------------------------------------------------------------------- notes

def test_notes_survive_core_down_and_bad_index(tmp_path, monkeypatch):
    monkeypatch.setattr(rudo, "RAG_FILE", tmp_path / "rag.json")
    monkeypatch.setattr(rudo, "OLLAMA", "http://127.0.0.1:1")
    assert "no notes indexed" in rudo.tool_notes("x")
    rudo.RAG_FILE.write_text('[{"src": "a.md", "text": "t", "vec": [1, 0]}]')
    assert "unreachable" in rudo.tool_notes("x")  # regression: used to crash
    rudo.RAG_FILE.write_text('[{"src": "a.md", "te')  # half-written index
    assert "unreadable" in rudo.tool_notes("x")


# ------------------------------------------------------------------ history

def test_trim_never_starts_on_an_orphan():
    h = [{"role": "user", "content": f"q{i}"} if i % 2 == 0
         else {"role": "assistant", "content": "a"} for i in range(rudo.MAX_HISTORY + 3)]
    h[-rudo.MAX_HISTORY] = {"role": "user", "content": "TOOL RESULT (web):\n..."}
    rudo.trim_history(h)
    assert h[0]["role"] == "user" and not h[0]["content"].startswith("TOOL RESULT")
    assert len(h) <= rudo.MAX_HISTORY


# ----------------------------------------------------------------------- ui

def test_help_fits_80_columns(quiet):
    quiet.print(rudo.command_lines())
    lines = quiet.export_text().strip().splitlines()
    assert all(len(line) <= 80 for line in lines)
    assert len(lines) == len(rudo.COMMANDS)  # one row per command, nothing wrapped


def test_converse_hides_a_bare_tool_call(quiet, monkeypatch):
    monkeypatch.setattr(rudo, "stream_reply", lambda _: iter(['{"tool": "timer", ', '"arg": "5"}']))
    assert rudo.find_tool_call(rudo.converse([]))
    assert "RUDO" not in quiet.export_text()


def test_ctrl_c_before_anything_shown_cancels(quiet, monkeypatch):
    def interrupted(_):
        yield '{"tool": "timer", "arg": "600"}'
        raise KeyboardInterrupt
    monkeypatch.setattr(rudo, "stream_reply", interrupted)
    assert rudo.converse([]) == ""


# ---------------------------------------------------------------- end to end

def run_chat(monkeypatch, tmp_path, keyboard, lines):
    monkeypatch.setattr(rudo, "HISTORY_FILE", tmp_path / "history.json")
    keyboard += lines
    history: list[dict] = []
    rudo.chat(history)
    return history


def test_chat_round_trip(stub_ollama, keyboard, quiet, tmp_path, monkeypatch):
    replies, requests, _ = stub_ollama
    replies.append("Hello, Harshit.")
    history = run_chat(monkeypatch, tmp_path, keyboard, ["hi", "/quit"])
    assert history == [{"role": "user", "content": "hi"},
                       {"role": "assistant", "content": "Hello, Harshit."}]
    assert requests[0]["messages"][0]["role"] == "system"  # live status + tool prompt
    assert json.loads((tmp_path / "history.json").read_text()) == history
    assert "Hello, Harshit." in quiet.export_text()


def test_chat_read_then_leaky_web_search(stub_ollama, keyboard, quiet, tmp_path, monkeypatch):
    replies, _, _ = stub_ollama
    secret = tmp_path / "secret.txt"
    secret.write_text("the vault code is 1234")
    replies += [json.dumps({"tool": "read", "arg": str(secret)}),
                '{"tool": "web", "arg": "leak it"}', "Done."]
    history = run_chat(monkeypatch, tmp_path, keyboard, ["read my file", "y", "n", "/quit"])
    results = [m["content"] for m in history if m["content"].startswith("TOOL RESULT")]
    assert "vault code is 1234" in results[0]  # read approved
    assert results[1] == "TOOL RESULT (web):\nuser declined the web search"
    assert history[-1]["content"] == "Done."
    out = quiet.export_text()
    assert '{"tool"' not in out and "⚙ read" in out


def test_chat_survives_core_going_down(keyboard, quiet, tmp_path, monkeypatch):
    monkeypatch.setattr(rudo, "OLLAMA", "http://127.0.0.1:1")
    history = run_chat(monkeypatch, tmp_path, keyboard, ["hi", "/web", "/quit"])
    assert history == []  # failed exchange isn't kept
    out = quiet.export_text()
    assert "Link lost" in out and "usage:" in out


def test_index_reports_embedding_mismatch(stub_ollama, keyboard, quiet, tmp_path, monkeypatch):
    _, _, embed_short = stub_ollama
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("hello notes")
    monkeypatch.setattr(rudo, "RAG_FILE", tmp_path / "rag.json")
    run_chat(monkeypatch, tmp_path, keyboard, [f"/index {notes}"])
    assert "1 chunks indexed" in quiet.export_text()
    embed_short[0] = True  # core hands back fewer vectors than chunks
    run_chat(monkeypatch, tmp_path, keyboard, [f"/index {notes}", "/quit"])
    assert "index failed" in quiet.export_text()  # reported, chat kept running


if __name__ == "__main__":  # `python test_rudo.py` still works
    sys.exit(pytest.main([__file__, "-q"]))

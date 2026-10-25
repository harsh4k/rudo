"""Smallest checks that fail if the tool-call logic breaks: python test_rudo.py"""
import io
import tempfile
from pathlib import Path

import rudo

# tool-call detection
assert rudo.find_tool_call('{"tool": "timer", "arg": "5"}') == {"tool": "timer", "arg": "5"}
assert rudo.find_tool_call('```json\n{"tool": "web", "arg": "news"}\n```')["tool"] == "web"
assert rudo.find_tool_call("plain chat reply, no tools here") is None
assert rudo.find_tool_call('{"tool": "format_disk", "arg": "c:"}') is None  # unknown tool ignored

# bad tool args come back as error strings, not crashes
assert "error" in rudo.tool_timer("five minutes")

# privileged tools ask first; "no" (or no keyboard) means the tool does nothing
answers = []
rudo.console.input = lambda *_: answers.pop(0)
answers.append("n")
assert "declined" in rudo.run_tool({"tool": "read", "arg": __file__}, False)
answers.append("y")
assert "privileged tools ask first" in rudo.run_tool({"tool": "read", "arg": __file__}, False)
answers.append("y")
assert "error" in rudo.run_tool({"tool": "read", "arg": "Z:/does/not/exist.txt"}, False)


def no_keyboard(*_):
    raise EOFError


def ctrl_c(*_):
    raise KeyboardInterrupt


for refuse in (no_keyboard, ctrl_c):  # no keyboard or ctrl+c at the prompt both mean no
    rudo.console.input = refuse
    assert "declined" in rudo.run_tool({"tool": "shell", "arg": "echo hi"}, False)

# once private data is in context, a model-initiated web search needs the user's OK
private = [{"role": "user", "content": "TOOL RESULT (read):\nsecret"}]
assert rudo.has_private(private)
assert rudo.has_private([{"role": "user", "content": "TOOL RESULT (shell):\nkeys"}])
assert not rudo.has_private([{"role": "user", "content": "hello"}])
assert rudo.has_private([{"role": "user", "content": f"x\n\n{rudo.CLIP_MARKER}\n..."}])
assert "declined" in rudo.run_tool({"tool": "web", "arg": "secret"}, True)

# notes search reports an unreachable core instead of crashing (regression)
rudo.RAG_FILE = Path(tempfile.mkdtemp()) / "rag.json"
rudo.RAG_FILE.write_text('[{"src": "a.md", "text": "t", "vec": [1, 0]}]')
rudo.OLLAMA = "http://127.0.0.1:1"
assert "unreachable" in rudo.tool_notes("anything")
rudo.RAG_FILE.write_text('[{"src": "a.md", "te')  # half-written index
assert "unreadable" in rudo.tool_notes("anything")

# a reply that may still be a bare tool call is held back; real answers stream at once
assert rudo.maybe_tool_call('{"tool": "ti')
assert rudo.maybe_tool_call("```js")
assert rudo.maybe_tool_call("```json\n{")
assert not rudo.maybe_tool_call("```powershell\nnetstat -ano")
assert not rudo.maybe_tool_call("Sure — here's the plan")

# converse(): a bare tool call is returned without being shown; ctrl+c before anything
# was shown cancels it, so chat() never runs a call the user didn't see
quiet = rudo.Console(file=io.StringIO())
rudo.console, real_console = quiet, rudo.console
real_stream = rudo.stream_reply
try:
    rudo.stream_reply = lambda _: iter(['{"tool": "timer", ', '"arg": "5"}'])
    assert rudo.find_tool_call(rudo.converse([]))
    assert "RUDO" not in quiet.file.getvalue()

    def interrupted(_):
        yield '{"tool": "timer", "arg": "600"}'
        raise KeyboardInterrupt
    rudo.stream_reply = interrupted
    assert rudo.converse([]) == ""
finally:
    rudo.console, rudo.stream_reply = real_console, real_stream

# the tool trace line names the tool and its arg, not raw JSON
trace = rudo.tool_trace({"tool": "read", "arg": "notes.md"}, "line one\nline two").plain
assert "read notes.md" in trace and "line one line two" in trace and "{" not in trace

# history trimming never starts on an orphaned reply or tool result
h = [{"role": "user", "content": f"q{i}"} if i % 2 == 0 else {"role": "assistant", "content": "a"}
     for i in range(rudo.MAX_HISTORY + 3)]
h[-rudo.MAX_HISTORY] = {"role": "user", "content": "TOOL RESULT (web):\n..."}
rudo.trim_history(h)
assert h[0]["role"] == "user" and not h[0]["content"].startswith("TOOL RESULT")
assert len(h) <= rudo.MAX_HISTORY

# help fits an 80-column terminal
narrow = rudo.Console(width=80, record=True, file=io.StringIO())
rudo.console, real_console = narrow, rudo.console
try:
    narrow.print(rudo.command_lines())
finally:
    rudo.console = real_console
help_lines = narrow.export_text().strip().splitlines()
assert all(len(line) <= 80 for line in help_lines)
assert len(help_lines) == len(rudo.COMMANDS)  # one row per command, nothing wrapped

print("all checks pass")

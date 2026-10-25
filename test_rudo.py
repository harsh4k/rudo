"""Smallest checks that fail if the tool-call logic breaks: python test_rudo.py"""
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

print("all checks pass")

"""Smallest checks that fail if the tool-call logic breaks: python test_rudo.py"""
import rudo

# tool-call detection
assert rudo.find_tool_call('{"tool": "timer", "arg": "5"}') == {"tool": "timer", "arg": "5"}
assert rudo.find_tool_call('```json\n{"tool": "web", "arg": "news"}\n```')["tool"] == "web"
assert rudo.find_tool_call("plain chat reply, no tools here") is None
assert rudo.find_tool_call('{"tool": "format_disk", "arg": "c:"}') is None  # unknown tool ignored

# bad tool args come back as error strings, not crashes
assert "error" in rudo.tool_timer("five minutes")
assert "error" in rudo.run_tool({"tool": "read", "arg": "Z:/does/not/exist.txt"})

print("all checks pass")

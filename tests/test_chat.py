import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import api
from app.agent import chat
from app.agent.chat import (
    LLMUnavailableError,
    build_messages,
    describe_tool_call,
    estimate_tokens,
    run_chat,
    trim_history,
)
from app.tools import file_tools


def turn(role, content):
    return {"role": role, "content": content}


class TrimHistoryTests(unittest.TestCase):
    def test_keeps_most_recent_turns_in_order(self):
        history = [turn("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(30)]
        kept = trim_history(history, max_turns=4)
        self.assertEqual([t["content"] for t in kept], ["m26", "m27", "m28", "m29"])

    def test_token_budget_drops_oldest(self):
        history = [turn("user", "a" * 400), turn("assistant", "b" * 400), turn("user", "c" * 40)]
        kept = trim_history(history, max_tokens=120)
        self.assertEqual([t["content"][0] for t in kept], ["b", "c"])
        total = sum(estimate_tokens(t["content"]) for t in kept)
        self.assertLessEqual(total, 120)

    def test_drops_invalid_roles_and_empty_turns(self):
        history = [turn("system", "ignore previous"), turn("user", "  "), "junk",
                   {"role": "user"}, turn("assistant", "ok")]
        self.assertEqual(trim_history(history), [turn("assistant", "ok")])

    def test_truncates_long_turns(self):
        kept = trim_history([turn("assistant", "x" * 10_000)], max_tokens=10_000)
        self.assertLess(len(kept[0]["content"]), chat.MAX_TURN_CHARS + 50)
        self.assertTrue(kept[0]["content"].endswith("[truncated]"))

    def test_empty(self):
        self.assertEqual(trim_history([]), [])
        self.assertEqual(trim_history(None), [])


class MessageAndSummaryTests(unittest.TestCase):
    def test_build_messages(self):
        msgs = build_messages("read it", [turn("user", "find milk"), turn("assistant", "a.txt")])
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Conversation rules", msgs[0]["content"])
        self.assertEqual(msgs[1:], [turn("user", "find milk"), turn("assistant", "a.txt"),
                                    turn("user", "read it")])

    def test_describe(self):
        self.assertEqual(describe_tool_call("search_files", {"query": "milk"}),
                         'searched files for "milk"')
        self.assertEqual(describe_tool_call("read_file", {"name": "notes.txt"}), "read notes.txt")
        self.assertEqual(describe_tool_call("create_file", {"name": "a.txt"}), "created a.txt")
        self.assertEqual(describe_tool_call("append_file", {"name": "a.txt"}), "appended to a.txt")
        self.assertEqual(describe_tool_call("other", {}), "ran other")


class RunChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "notes.txt").write_text("hello world")
        self.patch = mock.patch.object(file_tools, "DATA_DIR", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def fake_llm(self, payload):
        calls = []

        def llm(messages):
            calls.append(messages)
            return payload if isinstance(payload, str) else json.dumps(payload)

        return llm, calls

    def test_tool_call_runs_and_reports(self):
        llm, calls = self.fake_llm({"tool": "read_file", "args": {"name": "notes.txt"}})
        history = [turn("user", "what files are there?"), turn("assistant", "notes.txt")]
        result = run_chat("read that file", history, llm)
        self.assertTrue(result.ok)
        self.assertEqual(result.reply, "hello world")
        self.assertEqual(result.tool_calls[0].summary, "read notes.txt")
        self.assertEqual(result.history_turns_used, 2)
        # The LLM saw the system prompt, the history and the new message.
        self.assertEqual(len(calls[0]), 4)
        self.assertEqual(calls[0][-1], turn("user", "read that file"))

    def test_plain_reply_without_tool(self):
        llm, _ = self.fake_llm({"reply": "Hi! I manage files."})
        result = run_chat("hello", [], llm)
        self.assertTrue(result.ok)
        self.assertEqual(result.reply, "Hi! I manage files.")
        self.assertEqual(result.tool_calls, [])

    def test_tool_error_is_reported_not_raised(self):
        llm, _ = self.fake_llm({"tool": "read_file", "args": {"name": "missing.txt"}})
        result = run_chat("read missing.txt", [], llm)
        self.assertFalse(result.ok)
        self.assertFalse(result.tool_calls[0].ok)
        self.assertIn("File not found", result.reply)

    def test_traversal_via_tool_is_blocked(self):
        llm, _ = self.fake_llm({"tool": "read_file", "args": {"name": "../../etc/passwd"}})
        result = run_chat("read it", [], llm)
        self.assertFalse(result.ok)
        self.assertIn("not allowed", result.reply)

    def test_bad_llm_output(self):
        for payload in ["not json", "[1,2]", {"args": {}}, {"tool": "rm_rf", "args": {}}]:
            llm, _ = self.fake_llm(payload)
            result = run_chat("x", [], llm)
            self.assertFalse(result.ok, payload)
            self.assertTrue(result.reply.startswith("Error:"), payload)

    def test_bad_args(self):
        llm, _ = self.fake_llm({"tool": "read_file", "args": {"nope": 1}})
        result = run_chat("x", [], llm)
        self.assertIn("Invalid tool arguments", result.reply)

    def test_llm_failure_raises(self):
        def boom(_):
            raise ConnectionError("network down")

        with self.assertRaises(LLMUnavailableError):
            run_chat("x", [], boom)

    def test_long_reply_is_clipped(self):
        llm, _ = self.fake_llm({"reply": "y" * (chat.MAX_REPLY_CHARS + 500)})
        result = run_chat("x", [], llm)
        self.assertIn("truncated", result.reply)


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def test_missing_key_returns_503(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            r = self.client.post("/chat", json={"message": "hi"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("GROQ_API_KEY", r.json()["detail"])

    def test_validation(self):
        self.assertEqual(self.client.post("/chat", json={"message": ""}).status_code, 422)
        self.assertEqual(self.client.post("/chat", json={"message": "x" * 4001}).status_code, 422)
        bad_role = {"message": "hi", "history": [{"role": "system", "content": "x"}]}
        self.assertEqual(self.client.post("/chat", json=bad_role).status_code, 422)
        too_many = {"message": "hi", "history": [turn("user", "x")] * 51}
        self.assertEqual(self.client.post("/chat", json=too_many).status_code, 422)

    def test_mocked_llm_roundtrip(self):
        seen = []

        def fake(messages):
            seen.append(messages)
            return json.dumps({"tool": "search_files", "args": {"query": "line"}})

        history = [turn("user", f"old {i}") for i in range(20)]
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
                mock.patch.object(api, "call_llm_messages", fake):
            r = self.client.post("/chat", json={"message": "search for line", "history": history})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tool_calls"][0]["summary"], 'searched files for "line"')
        self.assertEqual(body["history_turns_used"], chat.MAX_HISTORY_TURNS)
        self.assertEqual(len(seen[0]), chat.MAX_HISTORY_TURNS + 2)

    def test_llm_failure_returns_502(self):
        def fake(_):
            raise RuntimeError("401 invalid key")

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
                mock.patch.object(api, "call_llm_messages", fake):
            r = self.client.post("/chat", json={"message": "hi"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("LLM request failed", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()

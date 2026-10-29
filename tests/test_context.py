import ast
import json
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import os
from urllib.parse import urlsplit

import requests
from test_security import security


# Exercise the node's text/context contracts without loading GPU model packages.
SOURCE = ast.parse((Path(__file__).resolve().parents[1] / "llm_node.py").read_text(encoding="utf-8"))
NAMES = {"PROVIDERS", "_SUMMARY_PROMPT", "_history_tokens", "_compact_history", "_context_tokens", "_transcript", "_ConversationMemory", "_endpoint", "_resolve_key", "_payload", "_answer", "_error_detail"}
namespace = dict(OrderedDict=OrderedDict, sha256=sha256, Lock=Lock, monotonic=monotonic, urlsplit=urlsplit, os=os, json=json, requests=requests, parse_stop_sequences=lambda value: [], is_loopback=security.is_loopback, session_keys=security.SessionKeys())
definitions = [node for node in SOURCE.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in NAMES or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in NAMES for target in node.targets)]
exec(compile(ast.Module(body=definitions, type_ignores=[]), "llm_node.py", "exec"), namespace)
for class_name, method_name in (("UniversalLLM", "generate"), ("GOATUniversalTextGenerator", "execute"), ("GOATUniversalTextGenerator", "fingerprint_inputs")):
    cls = next(node for node in SOURCE.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), "llm_node.py", "exec"), namespace)
    namespace[class_name + "_" + method_name] = namespace[method_name]


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.memory = namespace["_ConversationMemory"]()

    def history(self, session="one", identity="model", reset=0, limit=1024, prompt="next"):
        return self.memory.history(session, identity, reset, limit, "system", prompt)

    def remember(self, session="one", identity="model", reset=0, history=None, prompt="hello", answer="world", limit=1024):
        return self.memory.remember(session, identity, reset, history or [], limit, "system", prompt, answer)

    def test_turns_are_ordered_and_sessions_are_isolated(self):
        self.history()
        self.remember()
        self.assertEqual(self.history(), [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}])
        self.assertEqual(self.history(session="two"), [])

    def test_reset_and_changed_system_or_model_clear_history(self):
        for kwargs in ({"reset": 1}, {"identity": "changed"}):
            self.history()
            self.remember()
            self.assertEqual(self.history(**kwargs), [])

    def test_context_budget_drops_oldest_whole_turn_first(self):
        self.history()
        self.remember(prompt="a" * 80, answer="b" * 80)
        history = self.history()
        self.remember(history=history, prompt="recent", answer="answer")
        result = self.history(limit=30)
        self.assertEqual([item["content"] for item in result], ["recent", "answer"])
        self.assertEqual(self.history(limit=4), [])

    def test_large_answers_do_not_remain_in_memory(self):
        self.history()
        self.remember(answer="x" * 300000, limit=1000000)
        self.assertEqual(self.history(limit=1000000), [])

    def test_sessions_are_capacity_bounded_and_expire(self):
        for index in range(70):
            self.history(session=str(index))
        self.assertEqual(len(self.memory.sessions), 64)
        self.assertNotIn("0", self.memory.sessions)
        state = self.memory.sessions["6"]
        self.memory.sessions["6"] = (monotonic() - 3601, *state[1:])
        self.history()
        self.assertNotIn("6", self.memory.sessions)

    def test_old_inflight_generation_cannot_restore_reset_history(self):
        self.history()
        self.history(reset=1)
        self.remember(reset=0)
        self.assertEqual(self.history(reset=1), [])

    def test_compaction_preserves_summary_and_recent_turns_within_budget(self):
        history = []
        for index in range(5):
            history.extend([{"role": "user", "content": f"idea {index}: " + "a" * 160}, {"role": "assistant", "content": "b" * 160}])
        summarize = Mock(return_value="Keep the blue cabin, snowy setting, and the user's latest corrections.")
        compacted = namespace["_compact_history"](history, 300, summarize)
        self.assertEqual(compacted[0]["content"], namespace["_SUMMARY_PROMPT"])
        self.assertEqual(compacted[-2:], history[-2:])
        self.assertLessEqual(namespace["_history_tokens"](compacted), 300)
        self.assertTrue(summarize.call_args.args[0])

    def test_repeated_compaction_includes_prior_summary_and_falls_back_on_failure(self):
        original = [{"role": "user", "content": namespace["_SUMMARY_PROMPT"]}, {"role": "assistant", "content": "Original constraints"}]
        original += [{"role": "user", "content": "a" * 1000}, {"role": "assistant", "content": "b" * 1000}]
        summarize = Mock(return_value="Merged constraints")
        compacted = namespace["_compact_history"](original, 200, summarize)
        self.assertIn("Original constraints", namespace["_transcript"](summarize.call_args.args[0]))
        self.assertEqual(sum(item["content"] == namespace["_SUMMARY_PROMPT"] for item in compacted), 1)
        for answer in ("", "x" * 5000):
            fallback = namespace["_compact_history"](original, 200, Mock(return_value=answer))
            self.assertLessEqual(namespace["_history_tokens"](fallback), 200)

    def test_summarization_does_not_hold_memory_lock_and_reset_still_wins(self):
        self.history()
        self.remember(prompt="a" * 1000, answer="b" * 1000)
        def summarize(earlier, tokens):
            self.assertTrue(self.memory.lock.acquire(blocking=False))
            self.memory.lock.release()
            self.memory.history("one", "model", 1, 1024, "system", "fresh")
            return "Earlier idea"
        self.memory.history("one", "model", 0, 256, "system", "next", summarize)
        self.assertEqual(self.history(reset=1), [])

    def test_positive_context_bypasses_execution_cache(self):
        fingerprint = namespace["GOATUniversalTextGenerator_fingerprint_inputs"]
        self.assertNotEqual(fingerprint(None, settings={"context_window": 1024}), fingerprint(None, settings={"context_window": 1024}))
        self.assertEqual(fingerprint(None, settings={"context_window": 0}), "")


class ContextPayloadTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock(status_code=200)
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.json.return_value = {"choices": [{"message": {"content": "answer"}}]}
        self.post = Mock(return_value=self.response)
        namespace["requests"] = SimpleNamespace(post=self.post, exceptions=requests.exceptions)
        namespace["add_media"] = Mock()
        namespace["generate_local"] = Mock(return_value="local answer")
        self.history = [{"role": "user", "content": "previous @image1"}, {"role": "assistant", "content": "prior answer"}]

    def generate(self, provider, **kwargs):
        return namespace["UniversalLLM_generate"](None, provider=provider, model="model", prompt_idea="current", system_prompt="system", api_key="key", history=self.history, **kwargs)

    def test_remote_preserves_roles_and_current_media(self):
        def attach(body, *args):
            body["messages"][-1]["content"] = [{"type": "text", "text": "current"}, {"type": "image_url", "image_url": {"url": "data:test"}}]
        namespace["add_media"].side_effect = attach
        self.generate("OpenRouter")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[1:3], self.history)
        self.assertEqual(messages[-1]["content"][-1]["image_url"]["url"], "data:test")

    def test_native_lm_studio_replays_text_without_storing_on_server(self):
        self.response.json.return_value = {"output": [{"type": "message", "content": "answer"}]}
        self.generate("LM Studio")
        body = self.post.call_args.kwargs["json"]
        self.assertIn("Assistant: prior answer", body["input"])
        self.assertTrue(body["input"].endswith("User: current"))
        self.assertFalse(body["store"])

    def test_native_lm_studio_preserves_media_input(self):
        self.response.json.return_value = {"output": [{"type": "message", "content": "answer"}]}
        def attach(body, *args):
            body["input"] = [{"type": "text", "content": "current"}, {"type": "image", "data_url": "data:test"}]
        namespace["add_media"].side_effect = attach
        self.generate("LM Studio")
        inputs = self.post.call_args.kwargs["json"]["input"]
        self.assertIn("Assistant: prior answer", inputs[0]["content"])
        self.assertEqual(inputs[-1], {"type": "image", "data_url": "data:test"})

    def test_local_history_does_not_resolve_old_media_tags(self):
        self.assertEqual(self.generate("ComfyUI Local"), ("local answer",))
        prompt = namespace["generate_local"].call_args.args[1]
        self.assertIn("previous ＠image1", prompt)
        self.assertIn("Assistant: prior answer", prompt)


class GeneratorContextTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"provider": "OpenAI-compatible", "model": "test", "base_url": "", "context_window": 1024, "max_output_tokens": 20}
        fields = [SimpleNamespace(id=key, default=value) for key, value in self.settings.items()]
        namespace["GOATTextGeneratorSettings"] = SimpleNamespace(define_schema=lambda: SimpleNamespace(inputs=fields))
        namespace["combine_system_prompt"] = lambda system, guide, reference_names, **kwargs: system + guide
        namespace["_CONTEXT_MEMORY"] = namespace["_ConversationMemory"]()
        self.generate = Mock(return_value=("answer",))
        namespace["UniversalLLM"] = SimpleNamespace(generate=self.generate)
        namespace["io"] = SimpleNamespace(NodeOutput=lambda text, info, ui: SimpleNamespace(text=text, info=info, ui=ui))
        namespace["ui"] = SimpleNamespace(PreviewText=lambda text: {"text": (text,)})

    def execute(self, **kwargs):
        options = dict(prompt_idea="idea", system_prompt="system", prompt_guide="guide", settings=self.settings, context_session="session")
        options.update(kwargs)
        return namespace["GOATUniversalTextGenerator_execute"](None, **options)

    def test_repeated_generation_remembers_and_reports_context(self):
        first = self.execute()
        self.assertEqual(self.generate.call_args.kwargs["history"], [])
        self.assertEqual(first.ui["context_usage"][0]["limit"], 1024)
        self.assertTrue(first.ui["context_usage"][0]["estimated"])
        self.execute(prompt_idea="next")
        self.assertEqual([item["content"] for item in self.generate.call_args.kwargs["history"]], ["idea", "answer"])

    def test_context_1024_remembers_when_generation_maximum_is_1024(self):
        self.settings["max_output_tokens"] = 1024
        self.execute()
        self.execute(prompt_idea="follow up")
        self.assertEqual([item["content"] for item in self.generate.call_args.kwargs["history"]], ["idea", "answer"])

    def test_new_generation_preserves_current_instructions(self):
        self.execute()
        self.execute(context_reset=1, prompt_idea="fresh")
        kwargs = self.generate.call_args.kwargs
        self.assertEqual(kwargs["history"], [])
        self.assertEqual(kwargs["system_prompt"], "systemguide")
        self.assertEqual(kwargs["prompt_idea"], "fresh")

    def test_zero_context_is_stateless_and_discards_previous_history(self):
        self.execute()
        self.settings["context_window"] = 0
        output = self.execute()
        self.assertEqual(self.generate.call_args.kwargs["history"], [])
        self.assertEqual(output.ui, {"text": ("answer",)})
        self.settings["context_window"] = 1024
        self.execute()
        self.assertEqual(self.generate.call_args.kwargs["history"], [])

    def test_failed_generation_does_not_become_history(self):
        self.generate.side_effect = RuntimeError("request failed")
        with self.assertRaises(RuntimeError):
            self.execute()
        self.generate.side_effect = None
        self.execute()
        self.assertEqual(self.generate.call_args.kwargs["history"], [])

    def test_overflow_summarizes_old_turns_before_the_next_generation(self):
        self.settings["context_window"] = 512
        self.generate.side_effect = [("a" * 400,), ("Keep the blue cabin and snow.",), ("refined",)]
        self.execute(prompt_idea="p" * 320)
        output = self.execute(prompt_idea="q" * 1500)
        calls = self.generate.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("Summarize the conversation", calls[1].kwargs["system_prompt"])
        self.assertFalse(calls[1].kwargs["json_output"])
        self.assertNotIn("images", calls[1].kwargs)
        self.assertEqual(calls[2].kwargs["history"][1]["content"], "Keep the blue cabin and snow.")
        self.assertEqual(json.loads(output.info)["context"]["summary"], {"requests": 1, "status": "summarized"})

    def test_summary_failure_keeps_generation_working(self):
        self.settings["context_window"] = 512
        self.generate.side_effect = [("a" * 400,), RuntimeError("summary unavailable"), ("refined",)]
        self.execute(prompt_idea="p" * 320)
        output = self.execute(prompt_idea="q" * 1500)
        self.assertEqual(output.text, "refined")
        self.assertEqual(self.generate.call_args.kwargs["history"], [])
        self.assertEqual(json.loads(output.info)["context"]["summary"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

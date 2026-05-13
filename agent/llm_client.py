"""LLM client used by cluster_narrative, remediation_content, bank_generator.

Wraps the `ollama` Python package against the local Ollama daemon
(default http://localhost:11434) and provides three modes of use:

  - generate(...)        free-form completion with optional Pydantic schema
                         for structured JSON output
  - generate_json(...)   same, returns a parsed dict / Pydantic instance
  - agent_loop(...)      tool-use loop: model calls tools, we execute them,
                         feed results back, until model finishes or budget

The default model is `gemma4:e4b`. Gemma 4 supports native function-calling
and a configurable thinking mode (enabled by prepending `<|think|>` to the
system prompt). We enable thinking by default for math reasoning, but it
can be disabled per-call.

A MockClient with identical interface is provided for offline testing.
Swap MockClient for OllamaClient when you have the daemon running; the
downstream modules don't care which they got.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import ollama
from pydantic import BaseModel, ValidationError


DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OPTIONS = {
    # From Gemma 3n/4 model card recommendations. Override per-call if needed.
    "temperature": 1.0,
    "top_k": 64,
    "top_p": 0.95,
    "min_p": 0.0,
    "num_ctx": 8192,
}


# --------------------------------------------------------------------------
# Tool definition helpers
# --------------------------------------------------------------------------

@dataclass
class Tool:
    """A callable tool the LLM may invoke during an agent_loop.

    `handler` receives the parsed kwargs dict and returns any JSON-serialisable
    value, which is fed back to the model as the tool result.
    """
    name: str
    description: str
    parameters: dict          # JSON schema for the tool's parameters
    handler: Callable[[dict], Any]

    def to_ollama_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentResult:
    final_message: str
    iterations: int
    tool_calls: list[dict] = field(default_factory=list)
    finished_cleanly: bool = True


# --------------------------------------------------------------------------
# Real client
# --------------------------------------------------------------------------

class OllamaClient:
    """Synchronous client targeting a local Ollama daemon."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        options: dict | None = None,
        thinking: bool = True,
    ):
        self.model = model
        self.host = host
        self.client = ollama.Client(host=host)
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.thinking = thinking

    # --- internal helpers ---

    def _prep_system(self, system: str | None,
                     thinking: bool | None = None) -> str | None:
        if system is None:
            return None
        use_thinking = self.thinking if thinking is None else thinking
        return (
            f"<|think|>\n{system}" if use_thinking
            and not system.startswith("<|think|>") else system
        )

    def _messages(self, prompt: str, system: str | None,
                  thinking: bool | None = None) -> list[dict]:
        msgs = []
        if system:
            msgs.append({"role": "system",
                         "content": self._prep_system(system, thinking)})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip Gemma's <|channel>thought ...<channel|> blocks from output.

        Gemma 4 with thinking enabled emits thoughts then the final answer.
        We only want the answer."""
        return re.sub(
            r"<\|channel\|?>thought.*?<\|?channel\|>",
            "",
            text,
            flags=re.DOTALL,
        ).strip()

    # --- public API ---

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        options: dict | None = None,
    ) -> str:
        """Single-turn completion, returns raw text (thinking stripped)."""
        merged_options = {**self.options, **(options or {})}
        response = self.client.chat(
            model=self.model,
            messages=self._messages(prompt, system),
            options=merged_options,
        )
        return self._strip_thinking(response["message"]["content"])

    def generate_json(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
        options: dict | None = None,
        max_retries: int = 2,
    ) -> BaseModel:
        """Generate and parse into a Pydantic model. Retries on validation error.

        Uses Ollama's `format` parameter to constrain output to JSON matching
        the schema (when supported by the model)."""
        merged_options = {**self.options, **(options or {})}
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            response = self.client.chat(
                model=self.model,
                messages=self._messages(prompt, system),
                options=merged_options,
                format=schema.model_json_schema(),
            )
            text = self._strip_thinking(response["message"]["content"])
            # Strip ```json fences if the model added them
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(),
                          flags=re.MULTILINE)
            try:
                data = json.loads(text)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                # Add a corrective message and retry
                prompt = (
                    prompt + f"\n\nPrevious output was invalid: {e}. "
                    f"Return ONLY valid JSON matching the schema, no prose."
                )
                time.sleep(0.1)
        raise RuntimeError(
            f"generate_json: failed after {max_retries + 1} attempts: {last_err}"
        )

    def agent_loop(
        self,
        initial_prompt: str,
        tools: list[Tool],
        *,
        system: str | None = None,
        max_iterations: int = 30,
        on_tool_call: Callable[[str, dict, Any], None] | None = None,
        verbose: bool = False,
        tool_temperature: float = 0.2,
        continuation_check: Callable[[str], str | None] | None = None,
        thinking: bool | None = None,
    ) -> AgentResult:
        """Tool-use loop. Model can call any of the provided tools; we
        execute them and return results; loop continues until the model
        emits a plain message (no tool calls) and `continuation_check`
        accepts the exit, or until max_iterations is hit.

        Tool calling needs a lower temperature than free-form generation
        (the default top-level 1.0 makes the model wander into prose
        instead of structured tool calls). `tool_temperature=0.2` is the
        empirical sweet spot for Gemma 4 doing math-tool tasks.

        `thinking=None` uses the client's default (set at construction).
        Pass `thinking=False` to disable Gemma 4's <|think|> mode for
        the agent loop -- substantially faster per iteration on weak
        hardware, sometimes at the cost of math quality. Pass True to
        force on. Recommended: try False first; flip to True if you see
        a lot of arithmetic_mismatch failures.

        `on_tool_call(name, args, result)` is invoked after every tool
        call so the caller can log or stream progress.

        `continuation_check(final_text)` is invoked when the model emits
        a message with no tool calls. Returning None means "accept this
        as the final answer, exit the loop". Returning a string means
        "the work isn't really done; inject this as a follow-up user
        message and keep going". Use it to refuse premature DONE.

        `verbose=True` prints each iteration's response content + tool
        call summary so it's possible to see what the model is doing.
        """
        messages = self._messages(initial_prompt, system, thinking)
        tool_schemas = [t.to_ollama_schema() for t in tools]
        tool_map = {t.name: t for t in tools}
        tool_log: list[dict] = []
        agent_options = {**self.options, "temperature": tool_temperature}

        for iteration in range(1, max_iterations + 1):
            response = self.client.chat(
                model=self.model,
                messages=messages,
                tools=tool_schemas,
                options=agent_options,
            )
            msg = response["message"]
            tool_calls = msg.get("tool_calls") or []

            if verbose:
                content_preview = (msg.get("content") or "").strip()[:200]
                print(f"  [iter {iteration}] content={content_preview!r}  "
                      f"tool_calls={len(tool_calls)}", flush=True)

            if not tool_calls:
                content = self._strip_thinking(msg.get("content", ""))

                # Ask the caller whether this is really a clean exit
                if continuation_check is not None:
                    follow_up = continuation_check(content)
                    if follow_up:
                        if verbose:
                            print(f"  [continuation rejected exit] "
                                  f"injecting: {follow_up[:150]!r}", flush=True)
                        messages.append({"role": "assistant",
                                         "content": content or "(no content)"})
                        messages.append({"role": "user", "content": follow_up})
                        continue

                # Clean exit
                if verbose and not tool_log:
                    print("  [WARNING] model exited without ever calling a tool. "
                          "Full content follows:", flush=True)
                    print("  " + (content or "(empty)").replace("\n", "\n  "),
                          flush=True)
                return AgentResult(
                    final_message=content,
                    iterations=iteration,
                    tool_calls=tool_log,
                    finished_cleanly=True,
                )

            # Append the assistant turn to history. Strip thinking blocks
            # before appending -- per Gemma 4 docs, historical model output
            # should contain only the final response, never the thoughts.
            # Leaving them in bloats context AND degrades subsequent turns.
            messages.append({
                "role": "assistant",
                "content": self._strip_thinking(msg.get("content", "")),
                "tool_calls": tool_calls,
            })

            # Execute each tool call and append its result
            for call in tool_calls:
                name = call["function"]["name"]
                args_raw = call["function"].get("arguments", {})
                args = args_raw if isinstance(args_raw, dict) else json.loads(args_raw)

                if name not in tool_map:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = tool_map[name].handler(args)
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}

                tool_log.append({"name": name, "args": args, "result": result})
                if on_tool_call:
                    on_tool_call(name, args, result)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, default=str),
                })

        return AgentResult(
            final_message="(agent loop exceeded iteration budget)",
            iterations=max_iterations,
            tool_calls=tool_log,
            finished_cleanly=False,
        )


# --------------------------------------------------------------------------
# Mock client for offline tests
# --------------------------------------------------------------------------

class MockClient:
    """Same interface as OllamaClient, but responses come from a scripted
    queue or a callable. Used for unit tests where no daemon is available.

    Usage:
        m = MockClient()
        m.queue_json(MySchema(...))             # for generate_json
        m.queue_text("some plain text")         # for generate
        m.queue_tool_call("tool_x", {"arg": 1}) # for agent_loop
        m.queue_text("final message")           # ends the agent loop
    """

    def __init__(self, model: str = "mock"):
        self.model = model
        self.host = "mock://"
        self._queue: list[dict] = []

    def queue_text(self, text: str):
        self._queue.append({"type": "text", "text": text})

    def queue_json(self, instance):
        if isinstance(instance, BaseModel):
            data = instance.model_dump()
        else:
            data = instance
        self._queue.append({"type": "json", "data": data})

    def queue_tool_call(self, name: str, args: dict):
        self._queue.append({"type": "tool_call", "name": name, "args": args})

    def _pop(self) -> dict:
        if not self._queue:
            raise RuntimeError("MockClient queue empty")
        return self._queue.pop(0)

    def generate(self, prompt: str, **kwargs) -> str:
        item = self._pop()
        if item["type"] != "text":
            raise RuntimeError(f"expected text, got {item['type']}")
        return item["text"]

    def generate_json(self, prompt: str, schema, **kwargs):
        item = self._pop()
        if item["type"] != "json":
            raise RuntimeError(f"expected json, got {item['type']}")
        return schema.model_validate(item["data"])

    def agent_loop(self, initial_prompt, tools, *, max_iterations=30,
                   on_tool_call=None, continuation_check=None,
                   **kwargs) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        tool_log = []
        iters = 0
        while iters < max_iterations:
            iters += 1
            item = self._pop()
            if item["type"] == "tool_call":
                if item["name"] not in tool_map:
                    result = {"error": f"unknown tool: {item['name']}"}
                else:
                    try:
                        result = tool_map[item["name"]].handler(item["args"])
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                tool_log.append({"name": item["name"], "args": item["args"],
                                 "result": result})
                if on_tool_call:
                    on_tool_call(item["name"], item["args"], result)
            elif item["type"] == "text":
                # Same continuation logic as the real client
                if continuation_check is not None:
                    follow_up = continuation_check(item["text"])
                    if follow_up:
                        # Pretend we re-prompted the model; keep popping.
                        continue
                return AgentResult(
                    final_message=item["text"],
                    iterations=iters,
                    tool_calls=tool_log,
                    finished_cleanly=True,
                )
            else:
                raise RuntimeError(f"unexpected item type {item['type']}")
        return AgentResult(
            final_message="(mock iterations exceeded)",
            iterations=max_iterations,
            tool_calls=tool_log,
            finished_cleanly=False,
        )


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def make_client(use_mock: bool = False, **kwargs):
    """Return either a real or mock client. Tests pass use_mock=True; the
    CLI entry points in the downstream modules always pass use_mock=False."""
    return MockClient(**kwargs) if use_mock else OllamaClient(**kwargs)

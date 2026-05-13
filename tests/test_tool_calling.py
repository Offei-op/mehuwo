"""Run me first. Tells us whether gemma4:e4b honours tool calls at all
through your local Ollama install. If this prints a tool_calls list,
tool calling works and the bank_generator problem is in our prompt or
options. If it prints None for tool_calls, function calling isn't
wired up in your Ollama+Gemma4 combo and we need a different approach.
"""
from ollama import Client
c = Client()

resp = c.chat(
    model="gemma4:e4b",
    messages=[{"role": "user",
               "content": "What is the weather in Tarkwa? Call the tool."}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Returns the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }],
    options={"temperature": 0.3},
)
print("=== content ===")
print(repr(resp["message"].get("content", "")))
print("=== tool_calls ===")
print(resp["message"].get("tool_calls"))
print("=== model ===")
print(resp.get("model"))

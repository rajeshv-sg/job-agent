"""
core/llm.py
Thin wrapper around OpenRouter — shared by all agents.
Handles retries, token logging, and structured JSON extraction.
"""
from __future__ import annotations
import json, os, re
from typing import Any
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

_client: OpenAI | None = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def chat(
    system: str,
    user: str,
    model: str = "openai/gpt-4o-mini",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """Single-turn chat. Returns the assistant text content."""
    response = get_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content


def chat_json(
    system: str,
    user: str,
    **kwargs,
) -> Any:
    """
    Like chat() but parses the response as JSON.
    Strips markdown code fences if the model wraps the output.
    """
    raw = chat(system, user + "\n\nRespond with valid JSON only.", **kwargs)
    # strip ```json ... ``` fences if present
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    return json.loads(clean)

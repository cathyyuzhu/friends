"""Claude API 调用封装。"""
import os

from anthropic import Anthropic

_MODEL = os.environ.get("FRIENDS_MODEL", "claude-sonnet-5")
_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "没有找到 ANTHROPIC_API_KEY。请复制 .env.example 为 .env 并填入你的 key，"
                "或者 export ANTHROPIC_API_KEY=xxx 后再运行。"
            )
        _client = Anthropic(api_key=api_key)
    return _client


def complete(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> str:
    client = get_client()
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()

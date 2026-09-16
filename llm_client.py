"""LLM 调用封装，支持 Anthropic 和阿里百炼(DashScope, OpenAI 兼容接口)两种后端。"""
import os

_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
_MODEL = os.environ.get("FRIENDS_MODEL", "claude-sonnet-5" if _PROVIDER == "anthropic" else "qwen-flash-character")
_client = None


def _get_anthropic_client():
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "没有找到 ANTHROPIC_API_KEY。请在 .env 里填入你的 key，"
            "或者 export ANTHROPIC_API_KEY=xxx 后再运行。"
        )
    return Anthropic(api_key=api_key)


def _get_dashscope_client():
    import httpx
    from openai import OpenAI

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "没有找到 DASHSCOPE_API_KEY。请在 .env 里填入你的阿里百炼 API key，"
            "或者 export DASHSCOPE_API_KEY=xxx 后再运行。"
        )
    base_url = os.environ.get(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    # DashScope 是国内服务，系统代理（通常为访问境外服务配置）走它反而会导致连接失败，
    # 所以这里显式忽略系统代理环境变量（trust_env=False），只对这个 client 生效。
    http_client = httpx.Client(trust_env=False)
    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def get_client():
    global _client
    if _client is None:
        if _PROVIDER == "dashscope":
            _client = _get_dashscope_client()
        elif _PROVIDER == "anthropic":
            _client = _get_anthropic_client()
        else:
            raise RuntimeError(
                f"未知的 LLM_PROVIDER: {_PROVIDER!r}，目前支持 'anthropic' 或 'dashscope'。"
            )
    return _client


def _complete_anthropic(client, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()


def _complete_dashscope(client, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        model=_MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def complete(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> str:
    client = get_client()
    if _PROVIDER == "dashscope":
        return _complete_dashscope(client, system_prompt, user_prompt, max_tokens)
    return _complete_anthropic(client, system_prompt, user_prompt, max_tokens)

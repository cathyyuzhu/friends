"""人设状态卡 + 关系事实：让朋友有连续的生活，也记得住你说过的事。

解决两个问题：
1. 以前每次回复都是无状态的角色扮演，"我现在在干嘛"全靠现编，同一个人两分钟内
   可以既在图书馆走回宿舍、又已经躺在床上。状态卡给每个人设一份会过期、会推进的生活线。
2. 以前聊过的内容只存在滚动窗口里，超出就丢。事实库把值得记住的东西提炼出来长期留着。
"""
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from llm_client import complete

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

STATE_TTL_SECONDS = 4 * 3600  # 状态卡活多久，过期就重新生成一段新的生活
FACTS_EVERY_N_MESSAGES = 10  # 每积累多少条新消息提炼一次事实
MAX_FACTS = 60
DISTILL_WINDOW = 24  # 提炼时回看多少条消息

_io_lock = threading.Lock()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_json_reply(text: str, default):
    """模型经常把 JSON 包在 ```json 里，或者前后带一句废话，这里宽松地抠出来。"""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return default
    return default


# ---------- 状态卡：这个朋友此刻在过什么样的生活 ----------


def state_path(persona_id: str) -> Path:
    return DATA_DIR / f"persona_state_{persona_id}.json"


def load_state(persona_id: str) -> dict:
    return _read_json(state_path(persona_id), {})


def save_state(persona_id: str, state: dict) -> None:
    with _io_lock:
        _write_json(state_path(persona_id), state)


def _generate_state(persona, time_label: str, previous: dict) -> dict:
    prev_note = ""
    if previous.get("now"):
        prev_note = (
            f"\n几小时前你在做的事：{previous['now']}\n"
            "新的状态要跟它衔接得上（时间过去了几小时，人会换个地方、做别的事），不要凭空跳到毫无关系的场景。"
        )
    recent = previous.get("recent") or []
    if recent:
        prev_note += "\n这几天你已经经历过的事（不要重复，可以延续）：\n" + "\n".join(f"- {r}" for r in recent[-4:])

    system = f"""你在为一个虚拟人物生成"此刻的生活状态"。

这个人物：
名字：{persona.name}
性格：{persona.personality}
兴趣：{"、".join(persona.interests)}

现在是 {time_label}。{prev_note}

生成一段符合这个时间点、符合 ta 性格的真实日常。要具体、琐碎、有生活质感，
像真人随口会说的那种（"刚被楼下装修吵醒""在便利店等关东煮"），
不要写成"正在深度思考人生意义"这种概念化的描述。

只输出 JSON，不要任何解释：
{{"now": "此刻在干嘛，一句话", "mood": "当下心情，几个字", "recent": ["最近一两天发生的具体小事", "另一件"]}}"""

    raw = complete(system, "生成 JSON。", max_tokens=300)
    data = _parse_json_reply(raw, {})
    if not isinstance(data, dict) or not data.get("now"):
        return previous or {}

    merged_recent = list(previous.get("recent") or [])
    for item in data.get("recent") or []:
        if isinstance(item, str) and item not in merged_recent:
            merged_recent.append(item)

    return {
        "now": str(data.get("now", "")).strip(),
        "mood": str(data.get("mood", "")).strip(),
        "recent": merged_recent[-6:],
        "updated_at": time.time(),
    }


def ensure_state(persona, time_label: str) -> dict:
    """取这个朋友的当前状态，过期或没有就重新生成一段。生成失败时退回旧状态，不阻断聊天。"""
    state = load_state(persona.id)
    fresh = state and (time.time() - state.get("updated_at", 0)) < STATE_TTL_SECONDS
    if fresh:
        return state
    try:
        new_state = _generate_state(persona, time_label, state)
    except Exception:  # noqa: BLE001
        return state
    if new_state:
        save_state(persona.id, new_state)
        return new_state
    return state


def format_state(state: dict) -> str:
    if not state or not state.get("now"):
        return ""
    lines = [f"你现在正在：{state['now']}"]
    if state.get("mood"):
        lines.append(f"你此刻的心情：{state['mood']}")
    recent = state.get("recent") or []
    if recent:
        lines.append("你这几天经历的事（聊到相关的话题时可以自然带出来，别硬塞）：")
        lines += [f"- {r}" for r in recent[-4:]]
    return "【你此刻的真实状态】\n" + "\n".join(lines)


# ---------- 事实库：关于用户和你们关系的长期记忆 ----------


def facts_path(scope: str) -> Path:
    return DATA_DIR / f"facts_{scope}.json"


def load_facts(scope: str) -> list:
    facts = _read_json(facts_path(scope), [])
    return facts if isinstance(facts, list) else []


def save_facts(scope: str, facts: list) -> None:
    with _io_lock:
        _write_json(facts_path(scope), facts[-MAX_FACTS:])


def format_facts(scope: str, limit: int = 12) -> str:
    facts = load_facts(scope)
    if not facts:
        return ""
    # 重要的排前面，同等重要的取最近的
    ranked = sorted(facts, key=lambda f: (f.get("importance", 1), f.get("ts", 0)), reverse=True)
    lines = "\n".join(f"- {f['text']}" for f in ranked[:limit] if f.get("text"))
    if not lines:
        return ""
    return (
        "【你记得的关于 ta 的事】（这是你们相处积累下来的，可以自然地用，"
        "但别一条条复述出来，那样像在查档案）\n" + lines
    )


def _distill(scope: str, log: list) -> None:
    existing = load_facts(scope)
    known = "\n".join(f"- {f['text']}" for f in existing[-25:]) or "（还没有）"
    recent = "\n".join(f"{m['speaker']}: {m['text']}" for m in log[-DISTILL_WINDOW:])

    system = f"""你在维护一份"关于这个人的长期记忆"，从聊天记录里挑出值得长期记住的事。

值得记的：ta 的处境和正在做的事、喜好和厌恶、在意的人、说过的计划和约定、
情绪上的重要时刻、能体现 ta 是个什么样的人的细节。
不值得记的：寒暄、天气、一次性的闲聊、朋友自己说的话。

已经记住的（不要重复）：
{known}

只输出 JSON 数组，没有新东西就输出 []，不要任何解释：
[{{"text": "一句话描述这件事", "importance": 1到5的整数}}]"""

    raw = complete(system, f"聊天记录：\n{recent}", max_tokens=400)
    new_items = _parse_json_reply(raw, [])
    if not isinstance(new_items, list):
        return

    seen = {f.get("text") for f in existing}
    added = False
    for item in new_items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        try:
            importance = int(item.get("importance", 2))
        except (TypeError, ValueError):
            importance = 2
        existing.append({"text": text, "importance": max(1, min(5, importance)), "ts": time.time()})
        added = True

    if added:
        save_facts(scope, existing)


_distill_counters = {}
_distill_running = set()


def maybe_distill_facts(scope: str, log: list) -> None:
    """攒够一批新消息就在后台提炼一次事实。放后台是因为提炼要多调一次模型，
    不能让用户在聊天时干等几秒。"""
    count = _distill_counters.get(scope, 0) + 1
    if count < FACTS_EVERY_N_MESSAGES:
        _distill_counters[scope] = count
        return
    _distill_counters[scope] = 0

    if scope in _distill_running or len(log) < 4:
        return
    _distill_running.add(scope)
    snapshot = list(log)

    def run():
        try:
            _distill(scope, snapshot)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _distill_running.discard(scope)

    threading.Thread(target=run, daemon=True).start()


def reset_scope(scope: str, persona_ids: Optional[list] = None) -> None:
    """/reset 时连带把事实库和状态卡清掉，否则清了聊天记录却还记着你说过的事，很割裂。"""
    with _io_lock:
        facts_path(scope).unlink(missing_ok=True)
        for pid in persona_ids or []:
            state_path(pid).unlink(missing_ok=True)
    _distill_counters.pop(scope, None)


def now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

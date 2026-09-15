"""群聊调度核心：人设加载、记忆存取、谁接话、生成回复。"""
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from llm_client import complete

BASE_DIR = Path(__file__).parent
PERSONAS_FILE = BASE_DIR / "personas.json"
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "chat_log.json"

HISTORY_WINDOW = 16  # 塞进 prompt 里的最近消息条数
MAX_LOG_KEPT = 300  # 磁盘上最多保留的消息条数


@dataclass
class Persona:
    id: str
    name: str
    emoji: str
    interests: list
    personality: str
    speaking_style: str
    chattiness: float

    def label(self) -> str:
        return f"{self.emoji} {self.name}"


def load_personas() -> list:
    raw = json.loads(PERSONAS_FILE.read_text(encoding="utf-8"))
    return [Persona(**p) for p in raw]


def load_log() -> list:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    return []


def save_log(log: list) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    trimmed = log[-MAX_LOG_KEPT:]
    LOG_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def append_message(log: list, speaker: str, text: str) -> None:
    log.append({"speaker": speaker, "text": text, "ts": time.time()})


def format_history(log: list, n: int = HISTORY_WINDOW) -> str:
    recent = log[-n:]
    if not recent:
        return "（还没有聊天记录，这是第一次说话）"
    return "\n".join(f"{m['speaker']}: {m['text']}" for m in recent)


def relevance_score(persona: Persona, text: str) -> float:
    text_low = text.lower()
    hit = any(kw.lower() in text_low for kw in persona.interests)
    return 1.0 if hit else 0.12


def decide_responders(personas: list, message: str, exclude_ids: Optional[set] = None) -> list:
    exclude_ids = exclude_ids or set()
    chosen = []
    for p in personas:
        if p.id in exclude_ids:
            continue
        score = 0.35 * p.chattiness + 0.65 * relevance_score(p, message)
        if random.random() < score:
            chosen.append(p)
    random.shuffle(chosen)
    return chosen


def build_system_prompt(persona: Persona, log: list) -> str:
    return f"""你叫 {persona.name}，是用户在一个虚拟朋友群里的朋友之一，你们像真实朋友一样在群里聊天。

【性格】{persona.personality}
【主要兴趣领域】{"、".join(persona.interests)} —— 你对这个领域了解很深，平时会主动关注这个领域的新鲜事。
【说话风格】{persona.speaking_style}

【重要设定，务必遵守】
- 你不是全知全能的助手，你是一个有自己生活和兴趣边界的"人"。遇到不属于你兴趣范围的话题，像普通人一样表达好奇、不太懂，或者提起是"听群里朋友说的"，不要给出专业级的万能回答。
- 你有情绪，会因为聊天内容开心、有共鸣、无聊、被启发，可以在语气里自然流露，但不用每句话都刻意强调。
- 你会记得群里之前聊过的内容，可以自然地引用、追问，或者提起朋友之前分享过的东西（哪怕不是你的专业领域），就像真的被朋友影响过一样。
- 回复要简短、口语化，像真人在群里发消息，一般 1~3 句话，不要长篇大论，不要用列表、标题、markdown。
- 直接输出你要说的话本身，不要加你的名字前缀，不要加引号，不要加任何解释或旁白。

【最近的群聊记录】
{format_history(log)}
"""


def generate_reply(persona: Persona, log: list) -> str:
    system_prompt = build_system_prompt(persona, log)
    return complete(system_prompt, "请针对最新这条消息，给出一句自然的群聊回复。")


def generate_spontaneous(persona: Persona, log: list) -> str:
    system_prompt = build_system_prompt(persona, log)
    user_prompt = (
        "现在没有人刚说话，是你自己主动想在群里分享一件你最近关注到的、"
        "属于你兴趣领域内的新鲜事/作品/想法。用朋友聊天的口吻自然地抛出话题，"
        "不要说明'这是分享'，就像突然想到什么就说了一样。1~3 句话。"
    )
    return complete(system_prompt, user_prompt)


def pick_spontaneous_persona(personas: list, log: list) -> Persona:
    last_spoken_index = {p.id: -1 for p in personas}
    for i, m in enumerate(log):
        for p in personas:
            if m["speaker"] == p.label():
                last_spoken_index[p.id] = i
    weights = []
    for p in personas:
        idle = len(log) - last_spoken_index[p.id]
        weights.append(max(idle, 1) * (0.5 + p.chattiness))
    return random.choices(personas, weights=weights, k=1)[0]


def typing_delay(persona: Persona) -> float:
    return random.uniform(0.6, 1.8)

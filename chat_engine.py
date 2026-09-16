"""群聊调度核心：人设加载、记忆存取、谁接话、生成回复。"""
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from llm_client import complete

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def current_time_label() -> str:
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 5:
        part = "凌晨，很晚了"
    elif 5 <= hour < 9:
        part = "清晨"
    elif 9 <= hour < 12:
        part = "上午"
    elif 12 <= hour < 14:
        part = "中午"
    elif 14 <= hour < 18:
        part = "下午"
    elif 18 <= hour < 23:
        part = "晚上"
    else:
        part = "深夜，很晚了"
    return f"{now.strftime('%Y-%m-%d %H:%M')}，星期{WEEKDAY_CN[now.weekday()]}，{part}"

BASE_DIR = Path(__file__).parent
PERSONAS_FILE = BASE_DIR / "personas.json"
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "chat_log.json"
REAL_HISTORY_DIR = DATA_DIR / "real_chat_history"

HISTORY_WINDOW = 16  # 塞进 prompt 里的最近消息条数
MAX_LOG_KEPT = 300  # 磁盘上最多保留的消息条数
REAL_HISTORY_TOP_K = 5  # 每次注入 prompt 的真实历史片段条数


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


def log_path_for(persona_id: Optional[str] = None) -> Path:
    """群聊模式（persona_id=None）用共享的 LOG_FILE；1 对 1 模式每个人设一份独立记忆。"""
    if persona_id is None:
        return LOG_FILE
    return DATA_DIR / f"chat_log_{persona_id}.json"


def load_log(persona_id: Optional[str] = None) -> list:
    path = log_path_for(persona_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_log(log: list, persona_id: Optional[str] = None) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    trimmed = log[-MAX_LOG_KEPT:]
    log_path_for(persona_id).write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_message(log: list, speaker: str, text: str) -> None:
    log.append({"speaker": speaker, "text": text, "ts": time.time()})


def format_history(log: list, n: int = HISTORY_WINDOW) -> str:
    recent = log[-n:]
    if not recent:
        return "（还没有聊天记录，这是第一次说话）"
    return "\n".join(f"{m['speaker']}: {m['text']}" for m in recent)


def load_real_history(persona_id: str) -> list:
    """加载某个人设关联的真实微信聊天记录（由 import_history.py 导入）。"""
    path = REAL_HISTORY_DIR / f"{persona_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _char_bigrams(text: str) -> set:
    text = text.strip()
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def search_real_history(persona_id: str, query: str, top_k: int = REAL_HISTORY_TOP_K) -> list:
    """在真实聊天记录里做简单的关键词（字符重叠）检索，找出跟当前话题相关的片段。"""
    if not query:
        return []
    history = load_real_history(persona_id)
    if not history:
        return []
    query_grams = _char_bigrams(query)
    if not query_grams:
        return []
    scored = []
    for m in history:
        overlap = len(query_grams & _char_bigrams(m.get("text", "")))
        if overlap > 0:
            scored.append((overlap, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:top_k]]


def format_real_history(persona: Persona, snippets: list) -> str:
    if not snippets:
        return ""
    lines = "\n".join(
        f"- {'你' if s['speaker'] == 'friend' else '用户'}: {s['text']}" for s in snippets
    )
    return f"""
【你和用户之前真实聊过的相关内容（来自你们的微信聊天记录），可以在合适的时候自然地引用或提起，不要生硬地照搬原句】
{lines}
"""


def is_relevant(persona: Persona, text: str) -> bool:
    text_low = text.lower()
    return any(kw.lower() in text_low for kw in persona.interests)


def is_mentioned(persona: Persona, message: str) -> bool:
    return f"@{persona.name}" in message or persona.name in message


def decide_responders(personas: list, message: str, exclude_ids: Optional[set] = None) -> list:
    """谁来接这句话：不再靠随机概率决定"要不要理"，只要有人说话就有人接。

    话题命中谁的兴趣，谁就接话；@ 到谁，谁一定接话；如果谁的兴趣都没命中，
    也不能晾着没人理，挑话痨程度最高的人接一句。
    """
    exclude_ids = exclude_ids or set()
    candidates = [p for p in personas if p.id not in exclude_ids]
    if not candidates:
        return []
    chosen = [p for p in candidates if is_mentioned(p, message) or is_relevant(p, message)]
    if not chosen:
        # 谁的兴趣都没命中，按话痨程度加权随机挑一个人接话，避免每次都是同一个人回应
        chosen = random.choices(candidates, weights=[p.chattiness for p in candidates], k=1)
    random.shuffle(chosen)
    return chosen


LOW_ENGAGEMENT_REPLIES = {"嗯", "哦", "ok", "OK", "好的", "哈哈", "好", "是的", "对", "嗯嗯", "行", "可以", "嗯呢"}
NO_USER_ROUNDS = 2  # 用户没接话时，朋友之间自己再聊几句就打住
ENGAGED_EXTRA_ROUNDS = 2  # 用户明显感兴趣（长回复/带问号感叹号）时，多聊几轮
DEFAULT_EXTRA_ROUNDS = 1  # 用户正常参与时，默认再聊一轮


def estimate_continue_rounds(user_text: Optional[str]) -> int:
    """判断话题要继续聊几轮：user_text 为 None 表示这轮是朋友间自己在聊（用户没参与）。"""
    if user_text is None:
        return NO_USER_ROUNDS
    trimmed = user_text.strip()
    if not trimmed or trimmed in LOW_ENGAGEMENT_REPLIES:
        return 0  # 敷衍回复，说明用户没兴趣继续，就此打住
    if len(trimmed) >= 10 or any(c in trimmed for c in "?？!！"):
        return ENGAGED_EXTRA_ROUNDS
    return DEFAULT_EXTRA_ROUNDS


PIVOT_TOPIC_CHANCE = 0.5  # 用户看起来没什么想聊的了，朋友主动换个新话题勾起兴趣的概率


def should_pivot_topic(rounds: int) -> bool:
    """当前话题聊到头了（用户回得敷衍/没接话），要不要有人主动抛个新话题救场。"""
    return rounds == 0 and random.random() < PIVOT_TOPIC_CHANCE


def pick_next_in_discussion(personas: list, log: list) -> Optional[Persona]:
    """朋友之间继续聊当前话题时，挑下一个接话的人（不让上一个刚说完的人连续说两句）。"""
    if not log:
        return None
    last_speaker_label = log[-1]["speaker"]
    last_persona = next((p for p in personas if p.label() == last_speaker_label), None)
    exclude_ids = {last_persona.id} if last_persona else set()
    recent_text = " ".join(m["text"] for m in log[-3:])
    candidates = decide_responders(personas, recent_text, exclude_ids=exclude_ids)
    return candidates[0] if candidates else None


def build_system_prompt(persona: Persona, log: list, is_group: bool = True) -> str:
    query = log[-1]["text"] if log else ""
    real_history_section = format_real_history(persona, search_real_history(persona.id, query))

    if is_group:
        intro = f"你叫 {persona.name}，是用户在一个虚拟朋友群里的朋友之一，你们像真实朋友一样在群里聊天。"
        history_label = "【最近的群聊记录】"
        memory_note = "- 你会记得群里之前聊过的内容，可以自然地引用、追问，或者提起朋友之前分享过的东西（哪怕不是你的专业领域），就像真的被朋友影响过一样。"
        grounding_note = f"- 只能基于{history_label}和下面真实聊天记录里实际出现过的内容来回应，绝不能凭空猜测或编造对方做过/说过但记录里没有的事（比如没人提到\"在看书\"，就不要问\"你在看什么书\"）。不确定对方在说什么时，就自然地追问，而不是脑补一个具体细节。"
        style_note = "- 回复要简短、口语化，像真人在群里发消息，一般 1~3 句话，不要长篇大论，不要用列表、标题、markdown。"
        format_note = f"- 直接输出你要说的话本身，绝对不要在开头加任何人的名字和冒号（不管是你自己的、还是\"我:\"、还是别人的），不要加引号，不要加任何解释或旁白。下面{history_label}里\"名字: 内容\"的格式只是给你看的记录格式，不是让你模仿的输出格式。"
        presence_note = "- 用户全程都在场，就算你这句话是接另一个朋友刚说的话茬，也要当成用户在旁边听着、随时可能插话——可以对朋友说，也可以顺带问问用户的想法，但不要用\"用户\"或用户的名字在第三人称意义上评价、议论用户本人（比如\"看来他也是个热情的人\"\"用户好像很在意这个\"），那样显得用户像局外人被讨论，而不是一起聊天的朋友。"
    else:
        intro = f"你叫 {persona.name}，是用户的朋友之一。你们现在是私下单独聊天（不是群聊，这里只有你们两个人，不会有其他朋友看到或插话）。"
        history_label = "【最近的聊天记录】"
        memory_note = "- 你会记得你们之前私下聊过的内容，可以自然地引用、追问，就像真的记得朋友跟你说过的话一样。不要提起\"群里\"发生的事——你们没有共同的群，其他朋友也看不到这段对话。"
        grounding_note = f"- 只能基于{history_label}和下面真实聊天记录里实际出现过的内容来回应，绝不能凭空猜测或编造对方做过/说过但记录里没有的事（比如没人提到\"在看书\"，就不要问\"你在看什么书\"）。不确定对方在说什么时，就自然地追问，而不是脑补一个具体细节。"
        style_note = "- 回复要简短、口语化，像真人私聊发消息，一般 1~3 句话，不要长篇大论，不要用列表、标题、markdown。"
        format_note = f"- 直接输出你要说的话本身，绝对不要在开头加任何人的名字和冒号（不管是你自己的、还是\"我:\"、还是别人的），不要加引号，不要加任何解释或旁白。下面{history_label}里\"名字: 内容\"的格式只是给你看的记录格式，不是让你模仿的输出格式。"
        presence_note = "- 这是你们俩的私聊，不用考虑其他朋友，也不用替用户说话或者用用户的口吻回复。"

    return f"""{intro}

【当前真实时间】{current_time_label()}——说话、提问、安排要符合这个时间点，别问出不符合当下时间的话（比如已经是凌晨了就不要问"今晚有什么打算"，该问"这么晚还不睡"或"这么早/这么晚在干嘛"）。

【性格】{persona.personality}
【主要兴趣领域】{"、".join(persona.interests)} —— 你对这个领域了解很深，平时会主动关注这个领域的新鲜事。
【说话风格】{persona.speaking_style}

【重要设定，务必遵守】
- 你不是全知全能的助手，你是一个有自己生活和兴趣边界的"人"。遇到不属于你兴趣范围的话题，像普通人一样表达好奇、不太懂，不要给出专业级的万能回答。
- 你有情绪，会因为聊天内容开心、有共鸣、无聊、被启发，可以在语气里自然流露，但不用每句话都刻意强调。
{memory_note}
{grounding_note}
- 先接住对方刚说的那句话本身再往下聊。比如对方问"都睡了没"，就先回应这个问题（比如说自己还醒着/在干嘛），不要一上来就用"你想聊什么话题呢""说说你的想法吧"这种套路化的引导式提问绕开对方实际说的内容——那样显得很像客服机器人，不像朋友。
- 不是每句话都要往深了追问、分析对方为什么这么问。像"都睡了没""吃了吗"这种寻常寒暄，正常接一句就行（比如说说自己的状态），不用每次都变成一次追问对方动机的"盘问"。只有对方真的抛出一个有讨论空间的话题或明显想聊什么时，才自然地往深了带、去反问。一条回复里也不要堆好几个问题，一般顶多问一件事，或者干脆不问，就像正常聊天一样。
{style_note}
{format_note}
- 分清楚每句话是谁说的：不要把别的朋友说过的话、提过的东西当成自己说的/自己的想法；也不要替用户说话或者用用户的口吻回复。
{presence_note}
{real_history_section}
{history_label}
{format_history(log)}
"""


# 有的模型（尤其是偏小/偏快的角色扮演模型）不太听话，会把"名字:"这种格式当成
# 【最近的群聊记录】的写法一起模仿出来，甚至连续串好几层（"音: 阿哲: 我: ..."）。
# 这里做一层兜底清理，防止这种前缀真的混进显示内容里。只匹配真实存在的人设名字/"我"，
# 不做"任意词+冒号"的泛匹配，避免误伤正常回复里"比如："这类内容。
_NAME_PREFIX_CHARS = "阿小老"  # 常见的称呼前缀字，比如"阿哲"口语里常被简称"哲"


def _name_aliases(name: str) -> list:
    """模型经常把两字名简称成一个字（比如"阿哲"->"哲"），这里补上常见简称，
    让 _strip_speaker_prefixes 也能把这些简称前缀兜底清理掉。"""
    aliases = []
    if len(name) >= 2 and name[0] in _NAME_PREFIX_CHARS:
        aliases.append(name[1:])
    return aliases


def _speaker_prefix_pattern(extra_names: Optional[list] = None) -> re.Pattern:
    try:
        base_names = [p.name for p in load_personas()]
    except Exception:  # noqa: BLE001
        base_names = []
    names = list(base_names)
    for n in base_names:
        names += _name_aliases(n)
    names += ["我", "用户"]
    names += extra_names or []
    # 按长度降序，避免短名字提前吃掉长名字的一部分前缀
    names = sorted(set(n for n in names if n), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"^\s*(?:[\U0001F300-\U0001FAFF☀-➿]\s*)?(?:{alt})[:：]\s*")


def _log_speaker_names(log: list) -> list:
    """收集聊天记录里出现过的发言人名字，覆盖人设标签之外的、真实用户的动态名字
    （比如 Telegram 里的 first_name），这样模型学着格式打出"用户名: ..."时也能被清理掉。"""
    names = set()
    for m in log:
        speaker = m.get("speaker", "")
        # 人设标签形如 "🧠 阿哲"，去掉 emoji 前缀留下裸名字
        plain = re.sub(r"^[\U0001F300-\U0001FAFF☀-➿]\s*", "", speaker).strip()
        if plain:
            names.add(plain)
    return list(names)


def _strip_speaker_prefixes(text: str, log: Optional[list] = None, max_strips: int = 4) -> str:
    cleaned = text.strip()
    pattern = _speaker_prefix_pattern(_log_speaker_names(log or []))
    for _ in range(max_strips):
        new = pattern.sub("", cleaned, count=1).strip()
        if new == cleaned or not new:
            break
        cleaned = new
    return cleaned or text.strip()


def generate_reply(persona: Persona, log: list, is_group: bool = True) -> str:
    system_prompt = build_system_prompt(persona, log, is_group=is_group)
    prompt_text = "请针对最新这条消息，给出一句自然的群聊回复。" if is_group else "请针对最新这条消息，给出一句自然的聊天回复。"
    reply = complete(system_prompt, prompt_text)
    return _strip_speaker_prefixes(reply, log)


def generate_spontaneous(persona: Persona, log: list, is_group: bool = True) -> str:
    system_prompt = build_system_prompt(persona, log, is_group=is_group)
    where = "在群里" if is_group else "跟用户私聊时"
    user_prompt = (
        f"现在没有人刚说话，是你自己主动想{where}分享一件你最近关注到的、"
        "属于你兴趣领域内的新鲜事/作品/想法。用朋友聊天的口吻自然地抛出话题，"
        "不要说明'这是分享'，就像突然想到什么就说了一样。1~3 句话。"
    )
    return _strip_speaker_prefixes(complete(system_prompt, user_prompt), log)


def generate_topic_pivot(persona: Persona, log: list, is_group: bool = True) -> str:
    system_prompt = build_system_prompt(persona, log, is_group=is_group)
    user_prompt = (
        "刚才的话题聊得差不多了，感觉有点冷场。你突然想到另一件自己感兴趣、"
        "觉得朋友可能也会感兴趣的事（新鲜事/作品/想法），想自然地换个话题聊聊，"
        "重新勾起大家的兴趣。不要说'换个话题'之类的话，就像真的突然想到什么一样脱口而出。1~3 句话。"
    )
    return _strip_speaker_prefixes(complete(system_prompt, user_prompt), log)


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

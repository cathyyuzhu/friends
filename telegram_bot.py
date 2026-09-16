"""虚拟朋友群聊 - Telegram 版本。

前置准备（一次性）：
1. 在 Telegram 里找 @BotFather，对 personas.json 里的每个人设执行 /newbot，
   创建一个 bot，拿到形如 123456:ABC-DEF... 的 token。
2. 对用作"监听者"的那个 bot（默认是 personas.json 里第一个人设）执行：
   /setprivacy -> 选中这个 bot -> Disable
   这样它才能看到群里所有消息，而不是只有 /命令 或 @提到它的消息。
3. 建一个 Telegram 群，把所有 bot 都拉进去。
4. 把每个 token 填进 .env，变量名是 TG_TOKEN_<人设ID大写>，比如：
   TG_TOKEN_AZHE=123456:xxxx
   TG_TOKEN_XIAOLU=234567:xxxx
   TG_TOKEN_LINCHUAN=345678:xxxx
5. 第一次运行不用填 TG_GROUP_CHAT_ID：在群里发一条消息，终端会打印检测到的 chat_id，
   把它填进 .env，之后重启就只监听这一个群，不会误连别的群。

运行：
    python telegram_bot.py

在群里（群聊模式，共享记忆）：
- 正常发消息，朋友们会按人设和话题相关度决定谁接话、要不要接话
- 发 /tick，不聊天也能让某个朋友自己冒泡分享新鲜事（用来测试，不用真等后台定时器）

私聊某个朋友的 bot（1 对 1 模式，记忆跟群聊分开存）：
- 启动日志会打印每个朋友 bot 的 @username，直接找 ta 私聊即可，只有这一个朋友会回复
- 私聊里也支持 /tick
"""
import asyncio
import os
import random
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from telegram import Bot  # noqa: E402
from telegram.constants import ChatAction  # noqa: E402
from telegram.error import TelegramError  # noqa: E402

from chat_engine import (  # noqa: E402
    Persona,
    append_message,
    decide_responders,
    estimate_continue_rounds,
    format_history,
    generate_reply,
    generate_spontaneous,
    generate_topic_pivot,
    load_log,
    load_personas,
    pick_next_in_discussion,
    pick_spontaneous_persona,
    save_log,
    should_pivot_topic,
    typing_delay,
)

HELP_TEXT = (
    "可以直接打字聊天，我们会按话题接话。\n"
    "命令：\n"
    "/tick 让朋友主动冒泡分享一件事\n"
    "/who 列出所有朋友\n"
    "/history 查看最近的聊天记录\n"
    "/reset 清空这里的聊天记忆\n"
    "/help 查看这条帮助"
)

SPONTANEOUS_MIN_MINUTES = float(os.environ.get("TG_SPONTANEOUS_MIN_MINUTES", "90"))
SPONTANEOUS_MAX_MINUTES = float(os.environ.get("TG_SPONTANEOUS_MAX_MINUTES", "240"))


class PersonaBot:
    def __init__(self, persona: Persona, token: str):
        self.persona = persona
        self.bot = Bot(token=token)


def load_persona_bots(personas):
    bots = {}
    missing = []
    for p in personas:
        env_key = f"TG_TOKEN_{p.id.upper()}"
        token = os.environ.get(env_key)
        if not token:
            missing.append(env_key)
            continue
        bots[p.id] = PersonaBot(p, token)
    if missing:
        print("缺少以下 Telegram bot token，请在 .env 里补上（参考 .env.example）：")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)
    return bots


class TelegramFriendsService:
    def __init__(self):
        self.personas = load_personas()
        self.persona_bots = load_persona_bots(self.personas)

        listener_id = os.environ.get("TG_LISTENER_PERSONA_ID", self.personas[0].id)
        if listener_id not in self.persona_bots:
            print(f"TG_LISTENER_PERSONA_ID={listener_id} 不在 personas.json 的人设列表里")
            sys.exit(1)
        self.listener = self.persona_bots[listener_id]

        self.group_chat_id = None
        env_chat_id = os.environ.get("TG_GROUP_CHAT_ID")
        if env_chat_id:
            self.group_chat_id = int(env_chat_id)

        self.log = load_log()
        self.lock = asyncio.Lock()
        self.offset = None

    async def verify_bots(self):
        for pb in self.persona_bots.values():
            try:
                me = await pb.bot.get_me()
            except Exception as e:  # noqa: BLE001
                print(f"连不上 Telegram（{pb.persona.name}）：{e}")
                print(
                    "常见原因：1) token 填错了 2) 本机访问不了 api.telegram.org"
                    "（比如在国内没开代理/VPN，Telegram 在国内是被墙的）"
                )
                sys.exit(1)
            print(f"  已连接：{pb.persona.label()} -> @{me.username}")

    @staticmethod
    async def _drain_bot_backlog(bot: Bot) -> Optional[int]:
        """把某个 bot 积压的旧消息翻过去，返回应该从哪个 offset 开始监听。"""
        updates = await bot.get_updates(timeout=1)
        if updates:
            return updates[-1].update_id + 1
        return None

    async def drain_backlog(self):
        """启动时先把监听者 bot 的旧积压消息翻过去，避免一上线就回复几天前的消息。"""
        self.offset = await self._drain_bot_backlog(self.listener.bot)

    async def speak(self, persona: Persona, generate=generate_reply):
        pb = self.persona_bots.get(persona.id)
        if pb is None or self.group_chat_id is None:
            return
        try:
            await pb.bot.send_chat_action(chat_id=self.group_chat_id, action=ChatAction.TYPING)
        except TelegramError as e:
            print(f"[警告] {persona.name} 发送 typing 状态失败：{e}")

        await asyncio.sleep(typing_delay(persona))

        try:
            text = await asyncio.to_thread(generate, persona, self.log, True)
            await pb.bot.send_message(chat_id=self.group_chat_id, text=text)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] {persona.name} 生成或发送消息失败：{e}")
            return

        append_message(self.log, persona.label(), text)
        save_log(self.log)

    async def speak_private(self, persona: Persona, chat_id: int, log: list, generate=generate_reply):
        """1 对 1 私聊版的 speak：发给指定 chat_id，操作调用方传入的独立 log，不碰群聊的 self.log。"""
        pb = self.persona_bots.get(persona.id)
        if pb is None:
            return
        try:
            await pb.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except TelegramError as e:
            print(f"[警告] {persona.name} 发送 typing 状态失败：{e}")

        await asyncio.sleep(typing_delay(persona))

        try:
            text = await asyncio.to_thread(generate, persona, log, False)
            await pb.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] {persona.name} 生成或发送消息失败：{e}")
            return

        append_message(log, persona.label(), text)

    async def send_plain(self, persona: Persona, chat_id: int, text: str):
        """直接发一句固定文案（不经过 LLM），用来回命令，比如 /who /history。"""
        pb = self.persona_bots.get(persona.id)
        if pb is None:
            return
        try:
            await pb.bot.send_message(chat_id=chat_id, text=text)
        except TelegramError as e:
            print(f"[警告] {persona.name} 发送消息失败：{e}")

    def _who_text(self) -> str:
        return "\n".join(f"{p.label()} —— {p.personality}" for p in self.personas)

    async def handle_private_message(self, persona: Persona, chat_id: int, text: str):
        """私聊某个朋友的 bot：只有这一个朋友回复，记忆存在 ta 专属的 chat_log_<id>.json。"""
        log = load_log(persona.id)

        if text in ("/start", "/help"):
            await self.send_plain(persona, chat_id, f"嗨，我是{persona.label()}。\n{HELP_TEXT}")
            return
        if text == "/who":
            await self.send_plain(persona, chat_id, self._who_text())
            return
        if text == "/history":
            await self.send_plain(persona, chat_id, format_history(log, n=30))
            return
        if text == "/reset":
            log = []
            save_log(log, persona.id)
            await self.send_plain(persona, chat_id, "记忆已清空。")
            return
        if text == "/tick":
            await self.speak_private(persona, chat_id, log, generate=generate_spontaneous)
            save_log(log, persona.id)
            return

        append_message(log, "我", text)
        await self.speak_private(persona, chat_id, log)

        rounds = estimate_continue_rounds(text)
        if should_pivot_topic(rounds):
            await self.speak_private(persona, chat_id, log, generate=generate_topic_pivot)

        save_log(log, persona.id)

    async def handle_incoming_text(self, text: str, sender_name: str):
        async with self.lock:
            append_message(self.log, sender_name, text)
            responders = decide_responders(self.personas, text)
            for p in responders:
                await self.speak(p)

            rounds = estimate_continue_rounds(text)
            for _ in range(rounds):
                speaker = pick_next_in_discussion(self.personas, self.log)
                if not speaker:
                    break
                await self.speak(speaker)

            if should_pivot_topic(rounds):
                persona = pick_spontaneous_persona(self.personas, self.log)
                await self.speak(persona, generate=generate_topic_pivot)

            save_log(self.log)

    async def handle_tick(self):
        async with self.lock:
            persona = pick_spontaneous_persona(self.personas, self.log)
            await self.speak(persona, generate=generate_spontaneous)

    async def handle_group_command(self, text: str) -> bool:
        """处理群里的非聊天命令（/who /history /reset /help），处理了就返回 True。
        用监听者的 bot 身份直接回一句固定文案，不经过 LLM。"""
        if text not in ("/who", "/history", "/reset", "/help", "/start"):
            return False
        if self.group_chat_id is None:
            return True
        if text in ("/help", "/start"):
            await self.send_plain(self.listener.persona, self.group_chat_id, HELP_TEXT)
        elif text == "/who":
            await self.send_plain(self.listener.persona, self.group_chat_id, self._who_text())
        elif text == "/history":
            await self.send_plain(self.listener.persona, self.group_chat_id, format_history(self.log, n=30))
        elif text == "/reset":
            self.log = []
            save_log(self.log)
            await self.send_plain(self.listener.persona, self.group_chat_id, "群聊记忆已清空。")
        return True

    async def spontaneous_loop(self):
        while True:
            wait_minutes = random.uniform(SPONTANEOUS_MIN_MINUTES, SPONTANEOUS_MAX_MINUTES)
            await asyncio.sleep(wait_minutes * 60)
            if self.group_chat_id is None:
                continue
            await self.handle_tick()

    async def poll_loop(self):
        print("开始监听群消息...（在群里发消息试试，或者发 /tick 让朋友主动冒泡）")
        while True:
            try:
                updates = await self.listener.bot.get_updates(
                    offset=self.offset, timeout=30, allowed_updates=["message"]
                )
            except TelegramError as e:
                print(f"[警告] 拉取消息失败：{e}，5 秒后重试")
                await asyncio.sleep(5)
                continue

            for update in updates:
                self.offset = update.update_id + 1
                msg = update.message
                if not msg or not msg.text:
                    continue
                if msg.from_user and msg.from_user.is_bot:
                    continue  # 忽略所有 bot（包括我们自己）发的消息，避免自我触发死循环

                if msg.chat.type == "private":
                    # 有人直接私聊监听者 bot，走 1 对 1 流程，不当群消息处理
                    await self.handle_private_message(self.listener.persona, msg.chat.id, msg.text.strip())
                    continue

                if self.group_chat_id is None:
                    self.group_chat_id = msg.chat.id
                    print(
                        f"[提示] 检测到群聊 chat_id = {self.group_chat_id}，"
                        "建议写入 .env 的 TG_GROUP_CHAT_ID，重启后就只监听这个群"
                    )
                elif msg.chat.id != self.group_chat_id:
                    continue

                text = msg.text.strip()
                sender_name = msg.from_user.first_name if msg.from_user else "朋友"

                if text == "/tick":
                    await self.handle_tick()
                    continue
                if await self.handle_group_command(text):
                    continue

                await self.handle_incoming_text(text, sender_name)

    async def poll_private_loop(self, pb: PersonaBot):
        """给监听者以外的每个人设 bot 单独轮询私聊消息（各自 token 独立，offset 不会互相打架）。"""
        offset = await self._drain_bot_backlog(pb.bot)
        print(f"开始监听 {pb.persona.label()} 的私聊...")
        while True:
            try:
                updates = await pb.bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            except TelegramError as e:
                print(f"[警告] {pb.persona.name} 拉取私聊消息失败：{e}，5 秒后重试")
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update.update_id + 1
                msg = update.message
                if not msg or not msg.text or msg.chat.type != "private":
                    continue
                if msg.from_user and msg.from_user.is_bot:
                    continue

                await self.handle_private_message(pb.persona, msg.chat.id, msg.text.strip())

    async def run(self):
        print("正在连接各个朋友的 bot...")
        await self.verify_bots()
        print("清空历史积压消息...")
        await self.drain_backlog()
        tasks = [self.poll_loop(), self.spontaneous_loop()]
        tasks += [
            self.poll_private_loop(pb)
            for pb in self.persona_bots.values()
            if pb.persona.id != self.listener.persona.id
        ]
        await asyncio.gather(*tasks)


def main():
    service = TelegramFriendsService()
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()

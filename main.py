"""虚拟朋友 - 终端 MVP。

用法：
    python main.py

启动时先选模式：
    1. 群聊（所有朋友一起聊，谁接话由话题相关度决定）
    2. 1 对 1（只和你选的那个朋友单独聊天，记忆也单独存一份）

命令（两种模式通用）：
    /tick    让朋友主动分享点新鲜事（模拟"没人找他，他自己冒泡"）
    /who     列出所有朋友
    /history 查看最近的聊天记录
    /reset   清空当前记忆，重新开始
    /quit    退出
1 对 1 模式下还有：
    /switch  切换要聊天的朋友
其他输入都会被当作你发的消息。
"""
import sys
from functools import partial

from dotenv import load_dotenv

load_dotenv()

from chat_engine import (  # noqa: E402
    append_message,
    decide_responders,
    estimate_continue_rounds,
    format_history,
    generate_reply,
    generate_spontaneous,
    generate_topic_pivot,
    load_log,
    load_personas,
    log_path_for,
    pick_next_in_discussion,
    pick_spontaneous_persona,
    save_log,
    should_pivot_topic,
    typing_delay,
)
import time  # noqa: E402


def speak_all(chosen, log, generate=generate_reply):
    for p in chosen:
        print(f"{p.label()} 正在输入...", end="\r", flush=True)
        time.sleep(typing_delay(p))
        try:
            reply = generate(p, log)
        except Exception as e:  # noqa: BLE001
            print(f"\n[出错了：{e}]")
            continue
        print(" " * 30, end="\r")
        print(f"{p.label()}: {reply}")
        append_message(log, p.label(), reply)


def choose_persona(personas):
    print("选一个朋友：")
    for i, p in enumerate(personas, 1):
        print(f"  {i}. {p.label()} —— {p.personality}")
    while True:
        raw = input("输入序号或名字: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(personas):
            return personas[int(raw) - 1]
        match = next((p for p in personas if p.name == raw or p.id == raw), None)
        if match:
            return match
        print("没找到这个人，再试一次。")


def choose_mode():
    print("选一个聊天模式：")
    print("  1. 群聊（所有朋友一起聊）")
    print("  2. 1 对 1（只和一个朋友聊）")
    while True:
        raw = input("输入 1 或 2: ").strip()
        if raw in ("1", "2"):
            return "group" if raw == "1" else "1on1"
        print("请输入 1 或 2。")


# ---------- 群聊模式 ----------


def handle_user_message(text, personas, log):
    append_message(log, "我", text)
    responders = decide_responders(personas, text)
    speak_all(responders, log)

    rounds = estimate_continue_rounds(text)
    for _ in range(rounds):
        speaker = pick_next_in_discussion(personas, log)
        if not speaker:
            break
        speak_all([speaker], log)

    if should_pivot_topic(rounds):
        persona = pick_spontaneous_persona(personas, log)
        speak_all([persona], log, generate=generate_topic_pivot)

    save_log(log)


def handle_tick(personas, log):
    persona = pick_spontaneous_persona(personas, log)
    speak_all([persona], log, generate=generate_spontaneous)
    save_log(log)


def run_group_mode(personas):
    log = load_log()

    print("=" * 50)
    print("虚拟朋友群聊")
    for p in personas:
        print(f"  {p.label()} —— {'、'.join(p.interests[:3])}")
    print(f"记忆文件：{log_path_for()}")
    print("输入 /help 查看命令，直接打字就是发消息。")
    print("=" * 50)

    while True:
        try:
            text = input("\n我: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见~")
            break

        if not text:
            continue
        if text in ("/quit", "/exit"):
            print("再见~")
            break
        if text == "/help":
            print(__doc__)
            continue
        if text == "/who":
            for p in personas:
                print(f"  {p.label()} - {p.personality}")
            continue
        if text == "/history":
            print(format_history(log, n=30))
            continue
        if text == "/reset":
            log = []
            save_log(log)
            print("记忆已清空。")
            continue
        if text == "/tick":
            handle_tick(personas, log)
            continue

        handle_user_message(text, personas, log)


# ---------- 1 对 1 模式 ----------


def handle_user_message_1on1(text, persona, log):
    append_message(log, "我", text)
    speak_all([persona], log, generate=partial(generate_reply, is_group=False))

    rounds = estimate_continue_rounds(text)
    if should_pivot_topic(rounds):
        speak_all([persona], log, generate=partial(generate_topic_pivot, is_group=False))

    save_log(log, persona.id)


def handle_tick_1on1(persona, log):
    speak_all([persona], log, generate=partial(generate_spontaneous, is_group=False))
    save_log(log, persona.id)


def run_1on1_mode(personas):
    active_persona = choose_persona(personas)
    log = load_log(active_persona.id)

    print("=" * 50)
    print(f"正在和 {active_persona.label()} 1 对 1 聊天")
    print(f"记忆文件：{log_path_for(active_persona.id)}")
    print("输入 /help 查看命令，直接打字就是发消息。")
    print("=" * 50)

    while True:
        try:
            text = input(f"\n我 -> {active_persona.label()}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见~")
            break

        if not text:
            continue
        if text in ("/quit", "/exit"):
            print("再见~")
            break
        if text == "/help":
            print(__doc__)
            continue
        if text == "/who":
            for p in personas:
                print(f"  {p.label()} - {p.personality}")
            continue
        if text == "/history":
            print(format_history(log, n=30))
            continue
        if text == "/reset":
            log = []
            save_log(log, active_persona.id)
            print("记忆已清空。")
            continue
        if text == "/tick":
            handle_tick_1on1(active_persona, log)
            continue
        if text == "/switch":
            active_persona = choose_persona(personas)
            log = load_log(active_persona.id)
            print(f"已切换到 {active_persona.label()}。")
            continue

        handle_user_message_1on1(text, active_persona, log)


def main():
    try:
        personas = load_personas()
    except Exception as e:  # noqa: BLE001
        print(f"加载人设失败：{e}")
        sys.exit(1)

    mode = choose_mode()
    if mode == "group":
        run_group_mode(personas)
    else:
        run_1on1_mode(personas)


if __name__ == "__main__":
    main()

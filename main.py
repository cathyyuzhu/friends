"""虚拟朋友群聊 - 终端 MVP。

用法：
    python main.py

命令：
    /tick    让某个朋友主动分享点新鲜事（模拟"没人找他，他自己冒泡"）
    /who     列出所有朋友
    /history 查看最近的聊天记录
    /reset   清空记忆，重新开始
    /quit    退出
其他输入都会被当作你在群里发的消息。
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from chat_engine import (  # noqa: E402
    LOG_FILE,
    append_message,
    decide_responders,
    format_history,
    generate_reply,
    generate_spontaneous,
    load_log,
    load_personas,
    pick_spontaneous_persona,
    save_log,
    typing_delay,
)
import time  # noqa: E402


def speak_all(chosen, log):
    for p in chosen:
        print(f"{p.label()} 正在输入...", end="\r", flush=True)
        time.sleep(typing_delay(p))
        try:
            reply = generate_reply(p, log)
        except Exception as e:  # noqa: BLE001
            print(f"\n[出错了：{e}]")
            continue
        print(" " * 30, end="\r")
        print(f"{p.label()}: {reply}")
        append_message(log, p.label(), reply)


def handle_user_message(text, personas, log):
    append_message(log, "我", text)
    responders = decide_responders(personas, text)
    speak_all(responders, log)

    if responders:
        already = {p.id for p in responders}
        recent_text = " ".join(m["text"] for m in log[-len(responders):])
        extra = decide_responders(personas, recent_text, exclude_ids=already)[:1]
        speak_all(extra, log)
    else:
        print("...（这次好像没人看到消息，过一会儿也许有人会回）")

    save_log(log)


def handle_tick(personas, log):
    persona = pick_spontaneous_persona(personas, log)
    print(f"{persona.label()} 正在输入...", end="\r", flush=True)
    time.sleep(typing_delay(persona))
    try:
        text = generate_spontaneous(persona, log)
    except Exception as e:  # noqa: BLE001
        print(f"\n[出错了：{e}]")
        return
    print(" " * 30, end="\r")
    print(f"{persona.label()}: {text}")
    append_message(log, persona.label(), text)
    save_log(log)


def main():
    try:
        personas = load_personas()
    except Exception as e:  # noqa: BLE001
        print(f"加载人设失败：{e}")
        sys.exit(1)

    log = load_log()

    print("=" * 50)
    print("虚拟朋友群 MVP")
    for p in personas:
        print(f"  {p.label()} —— {'、'.join(p.interests[:3])}")
    print(f"记忆文件：{LOG_FILE}")
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


if __name__ == "__main__":
    main()

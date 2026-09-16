"""导入微信聊天记录（已导出为 CSV/JSON），转换成某个人设的"真实历史记忆"。

用法：
    python import_history.py --persona azhe --friend-name "老王" --file export.csv

导出文件要求（CSV 或 JSON，字段名不区分大小写，支持常见别名）：
    时间列：time / 时间 / 日期
    发送人列：sender / 发送人 / 昵称 / 发言人
    内容列：content / 内容 / 消息 / text

--friend-name 是导出文件里朋友的微信昵称/备注，用来判断哪些消息是朋友说的，
其余发送人（通常就是你自己）会被记为 "user"。可以多次运行来追加导入，已导入过的
消息（按 时间+说话人+内容 判重）不会重复写入。
"""
import argparse
import csv
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REAL_HISTORY_DIR = DATA_DIR / "real_chat_history"
PERSONAS_FILE = BASE_DIR / "personas.json"

TIME_KEYS = {"time", "时间", "日期", "datetime", "date"}
SENDER_KEYS = {"sender", "发送人", "昵称", "发言人", "name"}
CONTENT_KEYS = {"content", "内容", "消息", "text", "message"}


def _pick(row: dict, keys: set) -> str:
    for k, v in row.items():
        if k and k.strip().lower() in {x.lower() for x in keys}:
            return (v or "").strip()
    return ""


def parse_csv(path: Path) -> list:
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [
            {
                "time": _pick(row, TIME_KEYS),
                "sender": _pick(row, SENDER_KEYS),
                "content": _pick(row, CONTENT_KEYS),
            }
            for row in reader
        ]


def parse_json(path: Path) -> list:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "time": _pick(row, TIME_KEYS),
            "sender": _pick(row, SENDER_KEYS),
            "content": _pick(row, CONTENT_KEYS),
        }
        for row in raw
    ]


def load_persona_ids() -> set:
    raw = json.loads(PERSONAS_FILE.read_text(encoding="utf-8"))
    return {p["id"] for p in raw}


def main():
    parser = argparse.ArgumentParser(description="导入微信聊天记录到某个人设的真实历史记忆")
    parser.add_argument("--persona", required=True, help="人设 id（对应 personas.json 里的 id）")
    parser.add_argument("--friend-name", required=True, help="导出文件里朋友的昵称/备注")
    parser.add_argument("--file", required=True, help="导出的 CSV 或 JSON 文件路径")
    args = parser.parse_args()

    persona_ids = load_persona_ids()
    if args.persona not in persona_ids:
        raise SystemExit(f"personas.json 里没有 id={args.persona} 的人设")

    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"文件不存在: {file_path}")

    rows = parse_json(file_path) if file_path.suffix.lower() == ".json" else parse_csv(file_path)

    out_path = REAL_HISTORY_DIR / f"{args.persona}.json"
    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    seen = {(m["ts"], m["speaker"], m["text"]) for m in existing}

    imported = 0
    for row in rows:
        content = row["content"]
        if not content:
            continue
        speaker = "friend" if row["sender"] == args.friend_name else "user"
        key = (row["time"], speaker, content)
        if key in seen:
            continue
        existing.append({"ts": row["time"], "speaker": speaker, "text": content})
        seen.add(key)
        imported += 1

    REAL_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导入完成：新增 {imported} 条，{args.persona} 现有历史记忆共 {len(existing)} 条 -> {out_path}")


if __name__ == "__main__":
    main()

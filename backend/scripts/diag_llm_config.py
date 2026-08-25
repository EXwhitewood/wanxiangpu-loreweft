"""查询 fbi_review_blueprint agent 的 LLM 配置。"""
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "loreweft.db"


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in ("api_key", "token", "secret", "password")):
                redacted[key] = "[configured]" if item else item
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 找到 agent 配置表
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE '%agent%' OR name LIKE '%config%' OR name LIKE '%llm%')"
    )
    tables = [r[0] for r in cur.fetchall()]
    print(f"相关表: {tables}")

    for table in tables:
        print(f"\n=== {table} 表结构 ===")
        cur.execute(f"PRAGMA table_info({table})")
        cols = cur.fetchall()
        for c in cols:
            print(f"  {c['name']} ({c['type']})")

        # 如果是 agent 配置表，查 fbi_review_blueprint
        if "agent" in table.lower():
            cur.execute(f"SELECT * FROM {table} LIMIT 20")
            rows = cur.fetchall()
            for r in rows:
                d = _redact(dict(r))
                # 找 name 字段
                name_val = None
                for k in ["name", "agent_name", "key"]:
                    if k in d:
                        name_val = d[k]
                        break
                if name_val and "fbi" in str(name_val).lower():
                    print(f"\n=== {table} 中 fbi 相关配置 ===")
                    for k, v in d.items():
                        print(f"  {k}: {v}")

    # 也查 settings 表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%setting%'")
    setting_tables = [r[0] for r in cur.fetchall()]
    print(f"\nsettings 表: {setting_tables}")
    for st in setting_tables:
        cur.execute(f"SELECT * FROM {st} LIMIT 30")
        rows = cur.fetchall()
        for r in rows:
            d = _redact(dict(r))
            # 打印包含 llm/model/api 的配置
            s = json.dumps(d, ensure_ascii=False, default=str).lower()
            if any(kw in s for kw in ["llm", "model", "api", "fbi", "blueprint"]):
                print(f"  {st}: {json.dumps(d, ensure_ascii=False, default=str)[:500]}")

    conn.close()


if __name__ == "__main__":
    main()

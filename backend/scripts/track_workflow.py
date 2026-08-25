"""跟踪最新工作流状态。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "loreweft.db"


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT id, status, current_layer, error_message, created_at, updated_at "
        "FROM workflow_executions ORDER BY datetime(created_at) DESC LIMIT 1"
    )
    exec_row = cur.fetchone()
    if not exec_row:
        print("[ERR] no execution found")
        return 1

    exec_id = exec_row["id"]
    print("=" * 80)
    print(f"[最新 execution] {exec_id}")
    print(f"  status: {exec_row['status']}")
    print(f"  current_layer: {exec_row['current_layer']}")
    print(f"  error_message: {exec_row['error_message']}")
    print(f"  created_at: {exec_row['created_at']}")
    print(f"  updated_at: {exec_row['updated_at']}")

    # 所有 step 状态
    cur.execute(
        "SELECT layer, agent_name, status, error_message, "
        "length(output_snapshot) as snapshot_len "
        "FROM workflow_steps WHERE execution_id=? ORDER BY layer, agent_name",
        (exec_id,),
    )
    print("\n[所有 step 状态]")
    for row in cur.fetchall():
        d = dict(row)
        err = d.get("error_message") or ""
        print(
            f"  L{d['layer']:2d} {d['agent_name']:<35} "
            f"status={d['status']:<12} snapshot={d['snapshot_len'] or 0:>6}B "
            f"err={err[:50]}"
        )

    # L6 trace
    cur.execute(
        "SELECT output_snapshot FROM workflow_steps WHERE execution_id=? AND layer=6",
        (exec_id,),
    )
    row = cur.fetchone()
    if row:
        out = json.loads(row["output_snapshot"])
        rb = out.get("repair_strategy_summary", {}).get("revision_blueprint", {})
        agent_trace = rb.get("llm_blueprint_agent", {}).get("trace", {})
        print("\n" + "=" * 80)
        print("[L6 llm_blueprint_agent.trace]")
        print(json.dumps(agent_trace, ensure_ascii=False, indent=2))

        agent_wus = rb.get("llm_blueprint_agent", {}).get("work_units") or []
        if isinstance(agent_wus, list):
            print(f"\n[L6 llm_blueprint_agent.work_units] count={len(agent_wus)}")
            for i, wu in enumerate(agent_wus):
                if isinstance(wu, dict):
                    print(f"  [{i}] blueprint_source={wu.get('blueprint_source')} work_unit_id={wu.get('work_unit_id')}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

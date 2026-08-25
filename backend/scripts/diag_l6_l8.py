"""深入诊断 L6 agent 失败原因 + L8 复检失败详情。"""
import sqlite3
import json
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "loreweft.db"
EXEC_ID = sys.argv[1] if len(sys.argv) > 1 else None


def main():
    if not EXEC_ID:
        raise SystemExit("usage: py backend/scripts/diag_l6_l8.py <execution-id>")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ===== L6 深入 =====
    print("=" * 80)
    print("===== L6 fbi_repair_plan 完整 repair_strategy_summary =====")
    cur.execute(
        "SELECT output_snapshot FROM workflow_steps "
        "WHERE execution_id=? AND layer=6 AND agent_name='fbi_repair_plan'",
        [EXEC_ID],
    )
    row = cur.fetchone()
    if row:
        data = json.loads(row["output_snapshot"])
        rss = data.get("repair_strategy_summary", {})
        rb = rss.get("revision_blueprint", {})
        print(f"revision_blueprint.status: {rb.get('status')}")
        print(f"revision_blueprint.work_units: {rb.get('work_units')}")
        print(f"revision_blueprint.tool_commands: {rb.get('tool_commands')}")
        print(f"revision_blueprint.agent_status: {rb.get('agent_status')}")
        print(f"revision_blueprint.agent_mode: {rb.get('agent_mode')}")
        print(f"revision_blueprint.agent_session_status: {rb.get('agent_session_status')}")
        print(f"revision_blueprint.agent_failure_reason: {rb.get('agent_failure_reason')}")
        print()
        # 完整 revision_blueprint
        print("revision_blueprint full keys:", list(rb.keys()))
        rb_str = json.dumps(rb, ensure_ascii=False, indent=2)
        if len(rb_str) > 4000:
            rb_str = rb_str[:4000] + "...[truncated]"
        print(f"revision_blueprint full:\n{rb_str}")
        print()
        # llm_blueprint_agent trace
        lba = rb.get("llm_blueprint_agent") or {}
        if lba:
            print(f"llm_blueprint_agent: {json.dumps(lba, ensure_ascii=False, indent=2)[:2000]}")
        else:
            print("llm_blueprint_agent: <empty/missing>")

        # work_units 详情
        wus = rb.get("work_units_detail") or rb.get("work_units_list") or []
        if not wus:
            # 尝试从 orders 里找
            orders = data.get("orders", [])
            print(f"\norders count: {len(orders)}")
            for i, o in enumerate(orders[:10]):
                if isinstance(o, dict):
                    print(f"  order[{i}]: id={o.get('order_id')} status={o.get('status')} "
                          f"repair_type={o.get('repair_type')} work_unit_id={o.get('work_unit_id')}")

    # ===== L8 深入 =====
    print("\n" + "=" * 80)
    print("===== L8 parallel_recheck_1 复检失败详情 =====")
    cur.execute(
        "SELECT output_snapshot, error_message FROM workflow_steps "
        "WHERE execution_id=? AND layer=8 AND agent_name='parallel_recheck_1'",
        [EXEC_ID],
    )
    row = cur.fetchone()
    if row:
        print(f"error_message: {row['error_message']}")
        data = json.loads(row["output_snapshot"])
        print(f"top keys: {list(data.keys())}")
        print(f"error_code: {data.get('error_code')}")

        # final_violations
        fv = data.get("final_violations") or []
        print(f"\nfinal_violations count: {len(fv)}")
        for i, v in enumerate(fv[:10]):
            if isinstance(v, dict):
                print(f"  fv[{i}]: type={v.get('type')} metric={v.get('metric')} "
                      f"severity={v.get('severity')} blocks_commit={v.get('blocks_commit')}")
                detail = v.get("detail", "")
                if detail:
                    print(f"    detail: {detail[:200]}")
                target = v.get("target_span", "")
                if target:
                    print(f"    target_span: {target[:200]}")

        # attempts
        atts = data.get("attempts") or []
        print(f"\nattempts count: {len(atts)}")
        for i, a in enumerate(atts[:5]):
            if isinstance(a, dict):
                print(f"  attempt[{i}].keys: {list(a.keys())}")
                status = a.get("status")
                order_id = a.get("order_id")
                err = a.get("error") or a.get("error_message") or ""
                print(f"    order_id={order_id} status={status} err={err[:300]}")

        # review_case_delta
        rcd = data.get("review_case_delta") or {}
        if isinstance(rcd, dict):
            print(f"\nreview_case_delta.keys: {list(rcd.keys())}")
            rcd_str = json.dumps(rcd, ensure_ascii=False)
            if len(rcd_str) > 2000:
                rcd_str = rcd_str[:2000] + "...[truncated]"
            print(f"review_case_delta: {rcd_str}")

    conn.close()


if __name__ == "__main__":
    main()

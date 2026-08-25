"""临时诊断脚本：查询指定 execution_id 的 L5-L8 详细 snapshot。"""
import sqlite3
import json
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "loreweft.db"
EXEC_ID = sys.argv[1] if len(sys.argv) > 1 else None
LAYERS = sys.argv[2] if len(sys.argv) > 2 else "5,6,7,8"


def main():
    if not EXEC_ID:
        raise SystemExit("usage: py backend/scripts/diag_workflow.py <execution-id> [layers]")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    layers = [int(x) for x in LAYERS.split(",")]
    placeholders = ",".join("?" * len(layers))
    cur.execute(
        f"SELECT layer, agent_name, status, output_snapshot, error_message "
        f"FROM workflow_steps WHERE execution_id=? AND layer IN ({placeholders}) "
        f"ORDER BY layer, agent_name",
        [EXEC_ID, *layers],
    )
    rows = cur.fetchall()
    for r in rows:
        print(f"\n{'='*80}")
        print(f"=== L{r['layer']} {r['agent_name']} [{r['status']}] ===")
        err = r["error_message"] or ""
        print(f"err: {err[:800]}")
        snap = r["output_snapshot"] or ""
        print(f"snapshot_len: {len(snap)}")
        if not snap:
            continue
        try:
            data = json.loads(snap)
        except Exception as e:
            print(f"snapshot parse fail: {e}")
            print(f"raw (first 1000): {snap[:1000]}")
            continue
        # 打印顶层键
        print(f"top keys: {list(data.keys())}")
        # 精简打印关键字段
        for key in ["repair_strategy_summary", "revision_blueprint", "work_units",
                    "blueprint_source", "trace", "llm_blueprint_agent",
                    "failed_orders", "blocking_failed_orders", "recheck_result",
                    "case_delta", "delta_merge", "violations", "orders"]:
            if key in data:
                val = data[key]
                if isinstance(val, (dict, list)):
                    val_str = json.dumps(val, ensure_ascii=False)
                    if len(val_str) > 1500:
                        val_str = val_str[:1500] + "...[truncated]"
                    print(f"  {key}: {val_str}")
                else:
                    print(f"  {key}: {val}")
        # 如果是 L6，特别打印 plan 结构
        if r["layer"] == 6:
            plan = data.get("plan") or data.get("repair_plan") or {}
            if isinstance(plan, dict):
                print(f"  plan.keys: {list(plan.keys())}")
                wus = plan.get("work_units") or []
                print(f"  plan.work_units count: {len(wus)}")
                for i, wu in enumerate(wus[:5]):
                    bs = wu.get("blueprint_source") if isinstance(wu, dict) else None
                    print(f"    wu[{i}].blueprint_source: {bs}")
                orders = plan.get("orders") or []
                print(f"  plan.orders count: {len(orders)}")
                for i, o in enumerate(orders[:5]):
                    if isinstance(o, dict):
                        print(f"    order[{i}]: id={o.get('order_id')} status={o.get('status')} repair_type={o.get('repair_type')}")
        # 如果是 L7，打印 work_unit 来源
        if r["layer"] == 7:
            attempts = data.get("attempts") or data.get("repair_attempts") or []
            print(f"  attempts count: {len(attempts)}")
            for i, a in enumerate(attempts[:3]):
                if isinstance(a, dict):
                    print(f"    attempt[{i}].keys: {list(a.keys())}")
                    bs = a.get("blueprint_source")
                    if bs:
                        print(f"      blueprint_source: {bs}")
        # 如果是 L8，深入查看失败 orders
        if r["layer"] == 8:
            print(f"  L8 full data keys: {list(data.keys())}")
            # 尝试找到失败原因
            for k in ["failed_orders", "blocking_failed_orders", "failed_order_ids",
                       "recheck_errors", "violation_details", "order_results"]:
                if k in data:
                    print(f"  {k}: {json.dumps(data[k], ensure_ascii=False)[:2000]}")
    conn.close()


if __name__ == "__main__":
    main()

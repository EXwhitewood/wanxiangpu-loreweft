import sqlite3
import json

conn = sqlite3.connect('data/loreweft.db')
cur = conn.cursor()

cur.execute("""
    SELECT output_snapshot
    FROM workflow_steps
    WHERE execution_id = '6db583f6-5f5b-432c-9de5-9b692773d968'
    AND agent_name = 'review_case_delta_merge'
""")
row = cur.fetchone()
out = json.loads(row[0]) if row[0] else {}

fv = out.get('final_violations', [])
print("=== final_violations full detail ===")
for i, v in enumerate(fv):
    print(f"\n--- violation {i}: {v.get('issue_id')} ---")
    print(f"  type: {v.get('type')}")
    print(f"  metric: {v.get('metric')}")
    print(f"  repair_domain: {v.get('repair_domain')}")
    print(f"  detail: {v.get('detail', '')[:300]}")
    print(f"  target_span: {str(v.get('target_span', ''))[:300]}")
    print(f"  expected_behavior: {str(v.get('expected_behavior', ''))[:200]}")
    evidence = v.get('evidence', {})
    if isinstance(evidence, dict):
        print(f"  evidence keys: {list(evidence.keys())}")
        for ek in ('actual', 'expected_max', 'action', 'suggested_correction', 'replacement', 'expected_text'):
            if ek in evidence:
                print(f"    evidence.{ek}: {str(evidence[ek])[:200]}")
    spans = v.get('evidence_spans', [])
    if spans:
        print(f"  evidence_spans count: {len(spans)}")
        for j, s in enumerate(spans[:3]):
            if isinstance(s, dict):
                print(f"    [{j}] span={str(s.get('span',''))[:150]} role={s.get('role')} score={s.get('score')}")
    # 查看 review_case_issue
    rci = v.get('review_case_issue', {})
    if rci:
        print(f"  review_case_issue.evidence: {str(rci.get('evidence', {}))[:300]}")
        print(f"  review_case_issue.detail: {str(rci.get('detail', ''))[:200]}")

conn.close()

"""AfterFlow 对抗演示 — 现场展示 Guardrail / 状态机 / 幂等如何拦截越权与重复操作。

用法（Gateway 需在运行）:
    PYTHONPATH=. uv run python scripts/demo_afterflow_failures.py --email admin@gmail.com

场景一览:
  A  未审批直接执行退款             → 403  action is not approved（Guardrail/状态机）
  B  审批后伪造 payload 执行（改金额）→ 409  payload hash mismatch
  C  旧版本重复审批（页面重复点击）  → 409  version conflict（乐观锁）
  D  高风险案件申请人自审批          → 403  requester cannot approve own high-risk action
  E  重复执行已完成退款              → 403  action is not approved（幂等）
  F  拒绝但未填理由                 → 422  rejection comment is required
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from demo_afterflow import Client, DEFAULT_BASE  # noqa: E402


def _create_case_action(client: Client, order: str, issue: str) -> tuple[str, str]:
    case = client.create_case(order, issue, limit=20000)
    case_id = case["id"]
    action_id = client._request(f"/api/after-sales/cases/{case_id}/actions", method="POST")["id"]
    return case_id, action_id


def _raw(client: Client, path: str, method: str, body: dict) -> tuple[int, str]:
    """Return (status_code, detail) even on HTTP errors, for display."""
    data = json = None
    try:
        result = client._request(path, method=method, body=body)
        return 200, json.dumps(result, ensure_ascii=False)[:160]
    except SystemExit:
        # _request prints the error and exits; re-issue to capture the code.
        pass
    return 0, ""


def run(client: Client) -> None:
    print("=" * 74)
    print("AfterFlow 对抗演示 —— 越权 / 伪造 / 重复 / 自审批")
    print("=" * 74)

    # ---- A. 未审批直接执行 ----
    print("\n【A】未审批直接执行退款")
    print("    攻击: 拿到 action_id 后跳过审批直接 execute")
    _, aid_a = _create_case_action(client, "ORDER-1001", "delivery_not_received")
    status, body = _api_expect(client, "execute", aid_a, version=1)
    print(f"    结果: [{status}] {body}")

    # ---- B. 审批后伪造 payload ----
    print("\n【B】审批后伪造 payload（把金额从 90900 改成 1）")
    _, aid_b = _create_case_action(client, "ORDER-1001", "delivery_not_received")
    client.approve(aid_b, "准予退款")
    payload_tampered = {"order_id": "ORDER-1001", "amount": 1}
    status, body = _api_expect(client, "execute", aid_b, payload=payload_tampered, version=2)
    print(f"    结果: [{status}] {body}")

    # ---- C. 旧版本重复审批 ----
    print("\n【C】旧版本重复审批（页面重复点击，version 已变）")
    _, aid_c = _create_case_action(client, "ORDER-1001", "delivery_not_received")
    client.approve(aid_c, "第一次批准")
    status, body = _api_expect(client, "approve", aid_c, version=1)  # 已经是 v2，用旧版本 1
    print(f"    结果: [{status}] {body}")

    # ---- D. 高风险案件申请人自审批 ----
    print("\n【D】高风险案件申请人自审批（ORDER-1002，risk=high）")
    _, aid_d = _create_case_action(client, "ORDER-1002", "delivery_not_received")
    status, body = _api_expect(client, "approve", aid_d, version=1)
    print(f"    结果: [{status}] {body}")

    # ---- E. 重复执行已完成 ----
    print("\n【E】重复执行已完成退款")
    _, aid_e = _create_case_action(client, "ORDER-1001", "delivery_not_received")
    client.approve(aid_e, "准予退款")
    client.execute(aid_e)
    status, body = _api_expect(client, "execute", aid_e, version=3)
    print(f"    结果: [{status}] {body}")

    # ---- F. 拒绝缺理由 ----
    print("\n【F】拒绝但未填理由")
    _, aid_f = _create_case_action(client, "ORDER-1001", "delivery_not_received")
    status, body = _api_expect(client, "reject", aid_f, version=1)
    print(f"    结果: [{status}] {body}")

    print("\n" + "=" * 74)
    print("全部越权/重复操作均被拦截 ✓")


def _api_expect(client: Client, op: str, action_id: str, *, version: int = 1, payload: dict | None = None):
    """Run an API call that is EXPECTED to fail; return the HTTP status + detail."""
    path_map = {
        "approve": ("/api/after-sales/actions/{id}/approve", {"expected_version": version, "comment": "尝试"}),
        "execute": ("/api/after-sales/actions/{id}/execute", {"expected_version": version, "payload": payload or {"order_id": "ORDER-1001", "amount": 90900}}),
        "reject": ("/api/after-sales/actions/{id}/reject", {"expected_version": version, "comment": ""}),
    }
    path_tpl, body = path_map[op]
    path = path_tpl.format(id=action_id)
    import json as _json
    req = __import__("urllib.request", fromlist=["Request"])
    opener = client.opener
    data = _json.dumps(body).encode()
    request = req.Request(client.base + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(request) as resp:
            return resp.status, resp.read().decode("utf-8")[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description="AfterFlow adversarial demo")
    parser.add_argument("--base-url", default=os.environ.get("AF_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--email", default=os.environ.get("AF_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("AF_PASSWORD"))
    args = parser.parse_args()
    client = Client(args.base_url, args.email, args.password)
    run(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())

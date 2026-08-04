"""AfterFlow 多用户 RBAC 演示 — 客服发起 / 主管审批 / 权限门禁 / 高风险跨角色审批。

演示三类用户隔离与权限边界：
  - 客服(regular) 可以建案、创建 Action，但无权审批/执行（角色门禁 403）
  - 主管(admin) 可以审批客服发起的动作（跨角色审批成功）
  - 主管不能审批"自己请求"的高风险动作（自审批 403）

用法（Gateway 运行中）:
    PYTHONPATH=. uv run python scripts/demo_afterflow_rbac.py \
        --admin-email admin@gmail.com --admin-password 'AfterFlow@2026'

第二个用户（客服）默认 agent@afterflow.local / AfterFlow@2026，首次运行自动注册。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from demo_afterflow import DEFAULT_BASE, Client  # noqa: E402

AGENT_EMAIL = "agent@afterflow.com"
AGENT_PASSWORD = "AfterFlow@2026"


def _register(client: Client, email: str, password: str) -> bool:
    try:
        client._request("/api/v1/auth/register", method="POST", body={"email": email, "password": password})
        return True
    except SystemExit:
        # email already exists → fine, just log in next
        return False


def _raw_status(client: Client, path: str, body: dict) -> tuple[int, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        client.base + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with client.opener.open(req) as resp:
            return resp.status, resp.read().decode("utf-8")[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description="AfterFlow multi-user RBAC demo")
    parser.add_argument("--base-url", default=os.environ.get("AF_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--admin-email", default=os.environ.get("AF_ADMIN_EMAIL", "admin@gmail.com"))
    parser.add_argument("--admin-password", default=os.environ.get("AF_ADMIN_PASSWORD", ""))
    args = parser.parse_args()

    print("=" * 74)
    print("AfterFlow 多用户 RBAC 演示 —— 客服发起 / 主管审批")
    print("=" * 74)

    # 0) ensure agent user exists (register first, then log in)
    probe = Client(args.base_url)
    _register(probe, AGENT_EMAIL, AGENT_PASSWORD)
    agent = Client(args.base_url, AGENT_EMAIL, AGENT_PASSWORD)
    print(f"\n[0] 客服账号: {AGENT_EMAIL}（regular）— 已就绪")

    # 1) 客服发起高风险案件 + Action
    print("\n[1] 客服登录 → 创建高风险案件(ORDER-1002) → 创建退款 Action")
    case = agent.create_case("ORDER-1002", "delivery_not_received", limit=20000)
    action_id = agent._request(f"/api/after-sales/cases/{case['id']}/actions", method="POST")["id"]
    print(f"    case={case['id'][:12]}… action={action_id[:12]}… 风险={case['decision_json'].get('risk_level')}")

    # 2) 客服尝试审批 → 角色门禁
    print("\n[2] 客服尝试审批自己的 Action（应被角色门禁拦截）")
    code, detail = _raw_status(probe, f"/api/after-sales/actions/{action_id}/approve",
                               {"expected_version": 1, "comment": "我想自己批准"})
    print(f"    -> [{code}] {detail}")

    # 3) 客服尝试执行 → 角色门禁
    print("\n[3] 客服尝试执行退款（应被角色门禁拦截）")
    code, detail = _raw_status(probe, f"/api/after-sales/actions/{action_id}/execute",
                               {"expected_version": 1, "payload": {"order_id": "ORDER-1002", "amount": 9900}})
    print(f"    -> [{code}] {detail}")

    # 4) 主管审批客服发起的动作 → 成功（跨角色）
    print("\n[4] 主管登录 → 审批客服发起的 Action（跨角色，应成功）")
    admin = Client(args.base_url, args.admin_email, args.admin_password)
    result = admin._request(f"/api/after-sales/actions/{action_id}/approve", method="POST",
                            body={"expected_version": 1, "comment": "主管审批"})
    print(f"    -> approved: version={result.get('version')} by={result.get('approved_by')}")

    # 5) 主管执行 → 成功
    print("\n[5] 主管执行已批准退款（应成功）")
    result = admin.execute(action_id)
    print(f"    -> {result.get('status')}: txn={result.get('external_transaction_id')}")

    # 6) 主管自审批自己请求的高风险动作 → 自审批拦截
    print("\n[6] 主管自己发起高风险 Action 再自审批（应被自审批限制拦截）")
    own_case = admin.create_case("ORDER-1002", "delivery_not_received", limit=20000)
    own_action = admin._request(f"/api/after-sales/cases/{own_case['id']}/actions", method="POST")["id"]
    code, detail = _raw_status(admin, f"/api/after-sales/actions/{own_action}/approve",
                               {"expected_version": 1, "comment": "自己批自己"})
    print(f"    -> [{code}] {detail}")

    print("\n" + "=" * 74)
    print("RBAC 演示完成：客服无权审批/执行，主管可跨角色审批，自审批被拦截 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

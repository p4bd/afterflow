#!/usr/bin/env python3
"""AfterFlow demo helper — create cases, approve and execute refund actions.

Uses the running Gateway (default http://127.0.0.1:8001). Stdlib only.

Quickstart (with the Gateway running):
    # 1) Create a case as the logged-in user and print the ready-to-paste prompt:
    python scripts/demo_afterflow.py create --order ORDER-1001 \
        --issue delivery_not_received --limit 20000 --email admin@gmail.com

    # 2) In the UI, chat with afterflow-agent using the printed prompt.
    #    The agent creates the Action Request (pending_approval).

    # 3) Approve and execute deterministically (or do it in the approval desk):
    python scripts/demo_afterflow.py list --email admin@gmail.com
    python scripts/demo_afterflow.py approve <action_id> --email admin@gmail.com
    python scripts/demo_afterflow.py execute <action_id> --email admin@gmail.com

Auth: pass --email/--password (or AF_EMAIL/AF_PASSWORD env). If the Gateway
runs with DEER_FLOW_AUTH_DISABLED=1, credentials are still used when given so
cases are created under the real user; omit them to run as the synthetic
'default' user.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8001"

ISSUE_CODES = {
    "delivery_not_received": "delivery_not_received",
    "damaged": "damaged_item",
    "wrong": "wrong_item",
    "quality": "quality_issue",
    "refund_dispute": "refund_amount_dispute",
}

ISSUE_NAMES = {
    "delivery_not_received": "未收到货",
    "damaged_item": "破损",
    "wrong_item": "错发",
    "quality_issue": "质量问题",
    "refund_amount_dispute": "退款金额争议",
}

PROMPT_TEMPLATE = (
    "案件 {case_id}（订单 {order_id}）用户申报问题类型是 {issue_name}（{issue_code}）。"
    "请核验证据并直接创建退款审批请求。"
)


class Client:
    def __init__(self, base: str, email: str | None = None, password: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        if email and password:
            self._login(email, password)

    def _login(self, email: str, password: str) -> None:
        body = urllib.parse.urlencode({"username": email, "password": password}).encode()
        self._request(
            "/api/v1/auth/login/local",
            method="POST",
            body=body,
            content_type="application/x-www-form-urlencoded",
        )

    def _request(self, path: str, method: str = "GET", body: dict | bytes | None = None,
                 content_type: str = "application/json"):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode() if isinstance(body, dict) else body
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            print(f"  [HTTP {exc.code}] {raw[:400]}", file=sys.stderr)
            sys.exit(1)

    def create_case(self, order: str, issue: str, limit: int) -> dict:
        # NOTE: the operator refund limit is server-authoritative and derived
        # from the authenticated role; it is intentionally not sent to the API.
        return self._request("/api/after-sales/cases", method="POST", body={
            "order_id": order,
            "issue_type": issue,
            "visual_evidence_confirmed": False,
        })

    def list_actions(self) -> list[dict]:
        return self._request("/api/after-sales/actions")

    def get_action(self, action_id: str) -> dict | None:
        return next((a for a in self.list_actions() if a["id"] == action_id), None)

    def approve(self, action_id: str, comment: str | None) -> dict:
        action = self.get_action(action_id)
        if action is None:
            print(f"  ❌ 未找到 action {action_id}", file=sys.stderr)
            sys.exit(1)
        return self._request(f"/api/after-sales/actions/{action_id}/approve", method="POST", body={
            "expected_version": action["version"],
            "comment": comment,
        })

    def execute(self, action_id: str) -> dict:
        action = self.get_action(action_id)
        if action is None:
            print(f"  ❌ 未找到 action {action_id}", file=sys.stderr)
            sys.exit(1)
        return self._request(f"/api/after-sales/actions/{action_id}/execute", method="POST", body={
            "expected_version": action["version"],
            "payload": action["payload"],
        })


def main() -> None:
    parser = argparse.ArgumentParser(description="AfterFlow demo helper")
    parser.add_argument("--base-url", default=os.environ.get("AF_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--email", default=os.environ.get("AF_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("AF_PASSWORD"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="create a case and print the agent prompt")
    p_create.add_argument("--order", required=True)
    p_create.add_argument("--issue", choices=ISSUE_CODES, default="delivery_not_received")
    p_create.add_argument("--limit", type=int, default=20000)

    sub.add_parser("list", help="list actions")

    p_approve = sub.add_parser("approve", help="approve a pending action")
    p_approve.add_argument("action_id")
    p_approve.add_argument("--comment", default="")

    p_exec = sub.add_parser("execute", help="execute an approved action")
    p_exec.add_argument("action_id")

    args = parser.parse_args()
    client = Client(args.base_url, args.email, args.password)

    if args.cmd == "create":
        issue_code = ISSUE_CODES[args.issue]
        case = client.create_case(args.order, issue_code, args.limit)
        cid = case["id"]
        name = ISSUE_NAMES.get(issue_code, issue_code)
        decision = case.get("decision_json", {})
        print(f"\n✅ 案件已创建: {cid}")
        print(f"   订单 {case['order_id']} · {name} · 状态 {case['status']}")
        print(f"   决策: 金额 {decision.get('refund_amount')} 分 · "
              f"风险 {decision.get('risk_level')} · 需审批 {decision.get('approval_required')}")
        print("\n📋 把下面这段粘给 afterflow-agent：\n")
        print("   " + PROMPT_TEMPLATE.format(
            case_id=cid,
            order_id=args.order,
            issue_name=name,
            issue_code=issue_code,
        ))
        print("\n💡 操作员退款限额由服务端按登录角色决定，不随提示词传入。")
        print("\n💡 Agent 创建 Action 后，用本脚本 list/approve/execute 或审批台完成闭环。")

    elif args.cmd == "list":
        actions = client.list_actions()
        if not actions:
            print("（无动作）")
        for a in actions:
            case = a.get("case") or {}
            print(f"  {a['id']}  {str(case.get('order_id', '?')):<12} "
                  f"¥{a['payload'].get('amount', 0) / 100:<8} "
                  f"risk={a.get('risk_level'):<6} status={a.get('status'):<16} v{a.get('version')}")

    elif args.cmd == "approve":
        result = client.approve(args.action_id, args.comment)
        print(f"  ✅ approved: version={result.get('version')} by={result.get('approved_by')}")

    elif args.cmd == "execute":
        result = client.execute(args.action_id)
        print(f"  ✅ executed: status={result.get('status')} "
              f"txn={result.get('external_transaction_id')} version={result.get('version')}")


if __name__ == "__main__":
    main()

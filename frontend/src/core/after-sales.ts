import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export type AfterSalesAction = {
  id: string;
  case_id: string;
  action_type: string;
  payload: { order_id: string; amount: number };
  payload_hash: string;
  status: "pending_approval" | "approved" | "rejected" | "completed";
  risk_level: "low" | "medium" | "high";
  requested_by: string;
  approved_by: string | null;
  comment: string | null;
  expires_at: string;
  external_transaction_id: string | null;
  version: number;
  case: {
    order_id: string;
    issue_type: string;
    evidence_json: Record<string, unknown>;
    decision_json: {
      approval_reasons: string[];
      policy_refs: string[];
      signals: string[];
    };
  } | null;
};

const base = () => `${getBackendBaseURL()}/api/after-sales`;

async function request(path: string, init?: RequestInit) {
  const response = await fetch(`${base()}${path}`, init);
  if (!response.ok) {
    await throwGatewayApiError(
      response,
      `After-sales request failed: ${response.statusText}`,
    );
  }
  return response.json();
}

export const fetchAfterSalesActions = (): Promise<AfterSalesAction[]> =>
  request("/actions");

export const approveAfterSalesAction = (
  action: AfterSalesAction,
  comment?: string,
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_version: action.version,
      comment: comment ?? null,
    }),
  });

export const rejectAfterSalesAction = (
  action: AfterSalesAction,
  comment: string,
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_version: action.version, comment }),
  });

export const executeAfterSalesAction = (
  action: AfterSalesAction,
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_version: action.version,
      payload: action.payload,
    }),
  });

export function formatAfterSalesMoney(
  amount: number,
  locale = "zh-CN",
): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "CNY",
  }).format(amount / 100);
}

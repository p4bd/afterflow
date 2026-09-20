import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

/**
 * Mint a fresh ``Idempotency-Key`` for a state-changing after-sales request.
 *
 * The Stripe-style contract on the gateway (``IdempotencyMiddleware``) keys
 * the persisted response on this header so that a network-retry of the SAME
 * logical click replays the original result instead of re-executing the
 * mutation. A retry MUST reuse the same key the original call carried; a
 * separate user-initiated action MUST use a fresh key so the backend treats
 * it as a new request and the optimistic-lock ``expected_version`` check
 * rejects it as a conflict rather than silently double-acting.
 *
 * Two consecutive calls always produce different keys — UUIDv4's collision
 * probability after ``2^21`` ids is ~10^-9, well within tolerance for
 * per-click generation.
 *
 * Native ``crypto.randomUUID`` (Chromium / Firefox / Safari 15.4+) is used
 * when available; the manual fallback matches the same v4 layout so tests
 * and SSR (where ``crypto`` is undefined) stay green.
 */
export function generateIdempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export type AfterSalesAction = {
  id: string;
  case_id: string;
  action_type: string;
  payload: {
    order_id: string;
    amount?: number;
    sku?: string;
    kind?: string;
    estimated_cost?: number;
  };
  payload_hash: string;
  status: "pending_approval" | "approved" | "rejected" | "completed";
  risk_level: "low" | "medium" | "high";
  requested_by: string;
  approved_by: string | null;
  comment: string | null;
  expires_at: string;
  external_transaction_id: string | null;
  version: number;
  case: AfterSalesCase | null;
};

export type AfterSalesEvidence = {
  claims?: string[];
  facts?: { source: string; value: unknown; acquired_at: string }[];
  sources?: string[];
  missing?: string[];
  conflicts?: string[];
  snapshot_at?: string;
  revision?: number;
  intake?: Record<string, unknown>;
  [key: string]: unknown;
};

export type AfterSalesDecision = {
  route?: "reverse";
  reverse?: {
    outcome: string;
    action: string;
    estimated_resolution_cost: number;
    requires_return: boolean;
    signals: string[];
    missing_evidence: string[];
  };
  refund_amount?: number;
  eligibility?: string;
  action?: string;
  risk_level?: "low" | "medium" | "high";
  approval_required?: boolean;
  approval_reasons?: string[];
  policy_refs?: string[];
  signals?: string[];
  missing_evidence?: string[];
};

export type AfterSalesCase = {
  id: string;
  user_id: string;
  thread_id: string | null;
  order_id: string | null;
  issue_type: string | null;
  status: string;
  complaint_text: string;
  customer_expectation: "refund" | "replacement" | null;
  next_step: string;
  reply_draft: string;
  evidence_json: AfterSalesEvidence;
  decision_json: AfterSalesDecision;
  version: number;
  created_at: string;
  updated_at: string;
  actions?: AfterSalesAction[];
  events?: {
    id: string;
    event_type: string;
    actor: string;
    event_metadata: Record<string, unknown>;
    created_at: string;
  }[];
};

export type CaseIntakeInput = {
  complaint_text: string;
  order_id?: string;
  issue_type?: string;
  customer_expectation?: "refund" | "replacement";
  thread_id?: string;
};

export type CaseSupplementInput = {
  complaint_text?: string;
  order_id?: string;
  issue_type?: string;
  customer_expectation?: "refund" | "replacement";
  visual_evidence_confirmed?: boolean;
  damage_level?: "none" | "minor" | "major" | "destroyed";
  serial_matches?: boolean;
  note: string;
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

export const fetchAfterSalesCases = (): Promise<AfterSalesCase[]> =>
  request("/cases");

export const fetchAfterSalesCase = (caseId: string): Promise<AfterSalesCase> =>
  request(`/cases/${encodeURIComponent(caseId)}`);

export const createAfterSalesCase = (
  input: CaseIntakeInput,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesCase> =>
  request("/cases/intake", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(input),
  });

export const supplementAfterSalesCase = (
  caseId: string,
  input: CaseSupplementInput,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesCase> =>
  request(`/cases/${encodeURIComponent(caseId)}/supplements`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(input),
  });

export const createAfterSalesAction = (
  caseId: string,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesAction> =>
  request(`/cases/${encodeURIComponent(caseId)}/actions`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
  });

export const afterSalesCasePath = (caseId: string) =>
  `/workspace/after-sales/${encodeURIComponent(caseId)}`;

export const buildCaseIntakeInput = (
  complaintText: string,
  orderId: string,
): CaseIntakeInput => ({
  complaint_text: complaintText.trim(),
  ...(orderId.trim() ? { order_id: orderId.trim() } : {}),
});

export const approveAfterSalesAction = (
  action: AfterSalesAction,
  comment?: string,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/approve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      expected_version: action.version,
      comment: comment ?? null,
    }),
  });

export const rejectAfterSalesAction = (
  action: AfterSalesAction,
  comment: string,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/reject`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ expected_version: action.version, comment }),
  });

export const executeAfterSalesAction = (
  action: AfterSalesAction,
  idempotencyKey: string = generateIdempotencyKey(),
): Promise<AfterSalesAction> =>
  request(`/actions/${encodeURIComponent(action.id)}/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
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

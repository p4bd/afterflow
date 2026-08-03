"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Fingerprint,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import {
  approveAfterSalesAction,
  type AfterSalesAction,
  executeAfterSalesAction,
  fetchAfterSalesActions,
  formatAfterSalesMoney,
  rejectAfterSalesAction,
} from "@/core/after-sales";

const statusText = {
  pending_approval: "待审批",
  approved: "待执行",
  rejected: "已拒绝",
  completed: "已完成",
};

export default function AfterSalesPage() {
  const queryClient = useQueryClient();
  const [comments, setComments] = useState<Record<string, string>>({});
  const query = useQuery({
    queryKey: ["after-sales-actions"],
    queryFn: fetchAfterSalesActions,
    refetchInterval: 15_000,
  });
  const mutate = useMutation({
    mutationFn: async ({
      action,
      operation,
    }: {
      action: AfterSalesAction;
      operation: "approve" | "reject" | "execute";
    }) => {
      if (operation === "approve")
        return approveAfterSalesAction(action, comments[action.id]);
      if (operation === "reject")
        return rejectAfterSalesAction(action, comments[action.id] ?? "");
      return executeAfterSalesAction(action);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["after-sales-actions"] }),
    onError: (error: Error) => toast.error(error.message),
  });
  const actions = query.data ?? [];
  const pending = actions.filter(
    (action) => action.status === "pending_approval",
  ).length;

  return (
    <WorkspaceContainer className="bg-[radial-gradient(circle_at_top_right,hsl(var(--muted))_0,transparent_32%)]">
      <WorkspaceHeader />
      <WorkspaceBody className="overflow-y-auto">
        <div className="w-full max-w-6xl px-5 py-8 sm:px-8">
          <div className="mb-8 flex flex-col justify-between gap-5 border-b pb-6 sm:flex-row sm:items-end">
            <div>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-[0.18em] text-amber-700 uppercase dark:text-amber-400">
                <ShieldCheck className="size-4" /> AfterFlow control desk
              </div>
              <h1 className="text-3xl font-semibold tracking-tight">
                售后审批台
              </h1>
              <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
                核对证据、金额和政策版本。批准后仍会再次校验余额、版本和载荷哈希。
              </p>
            </div>
            <div className="flex items-center gap-3">
              <div className="border-border bg-background flex items-center gap-3 rounded-lg border px-4 py-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {pending}
                </span>
                <span className="text-muted-foreground text-xs leading-tight">
                  等待
                  <br />
                  主管决定
                </span>
              </div>
              <Button
                variant="outline"
                size="icon"
                onClick={() => void query.refetch()}
                aria-label="刷新审批队列"
              >
                <RefreshCw
                  className={
                    query.isFetching ? "size-4 animate-spin" : "size-4"
                  }
                />
              </Button>
            </div>
          </div>

          {query.isLoading && (
            <p className="text-muted-foreground py-16 text-center">
              正在加载审批队列…
            </p>
          )}
          {query.error && (
            <div className="border-destructive/30 bg-destructive/5 rounded-xl border p-5 text-sm">
              无法加载审批队列：{query.error.message}
            </div>
          )}
          {!query.isLoading && !query.error && actions.length === 0 && (
            <div className="border-border bg-card rounded-2xl border border-dashed py-20 text-center">
              <CheckCircle2 className="mx-auto mb-3 size-8 text-emerald-600" />
              <p className="font-medium">审批队列已清空</p>
              <p className="text-muted-foreground mt-1 text-sm">
                新的高金额或高风险申请会出现在这里。
              </p>
            </div>
          )}
          <div className="grid gap-5">
            {actions.map((action) => (
              <ApprovalCard
                key={action.id}
                action={action}
                comment={comments[action.id] ?? ""}
                onComment={(value) =>
                  setComments((current) => ({ ...current, [action.id]: value }))
                }
                onAction={(operation) => mutate.mutate({ action, operation })}
                busy={
                  mutate.isPending && mutate.variables?.action.id === action.id
                }
              />
            ))}
          </div>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function ApprovalCard({
  action,
  comment,
  onComment,
  onAction,
  busy,
}: {
  action: AfterSalesAction;
  comment: string;
  onComment: (value: string) => void;
  onAction: (operation: "approve" | "reject" | "execute") => void;
  busy: boolean;
}) {
  const decision = action.case?.decision_json;
  const stripe =
    action.status === "pending_approval"
      ? "bg-amber-500"
      : action.status === "completed"
        ? "bg-emerald-500"
        : "bg-muted-foreground/40";
  return (
    <article className="border-border bg-card relative overflow-hidden rounded-2xl border shadow-sm">
      <div className={`absolute inset-y-0 left-0 w-1.5 ${stripe}`} />
      <div className="grid gap-6 p-6 pl-8 lg:grid-cols-[1.3fr_0.7fr]">
        <div>
          <div className="mb-5 flex flex-wrap items-center gap-2">
            <Badge variant="outline">{statusText[action.status]}</Badge>
            <Badge
              variant={
                action.risk_level === "high" ? "destructive" : "secondary"
              }
            >
              风险 {action.risk_level}
            </Badge>
            <span className="text-muted-foreground ml-auto font-mono text-xs">
              v{action.version}
            </span>
          </div>
          <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
            <div>
              <p className="text-muted-foreground text-xs">
                订单 {action.payload.order_id}
              </p>
              <h2 className="mt-1 text-xl font-semibold break-words">
                {action.case?.issue_type ?? action.action_type}
              </h2>
            </div>
            <div className="text-left sm:text-right">
              <p className="text-muted-foreground text-xs">原路退款</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {formatAfterSalesMoney(action.payload.amount)}
              </p>
            </div>
          </div>
          <div className="mt-6 grid gap-3 text-sm sm:grid-cols-2">
            <Fact
              icon={<PackageCheck />}
              label="证据状态"
              value={action.case ? "订单、支付、物流已快照" : "案件快照缺失"}
            />
            <Fact
              icon={<Clock3 />}
              label="审批有效期"
              value={new Date(action.expires_at).toLocaleString("zh-CN")}
            />
            <Fact
              icon={<ShieldCheck />}
              label="审批原因"
              value={
                decision?.approval_reasons.length
                  ? decision.approval_reasons.join("、")
                  : "权限内自动批准"
              }
            />
            <Fact
              icon={<Fingerprint />}
              label="政策版本"
              value={
                decision?.policy_refs.length
                  ? decision.policy_refs.join("、")
                  : "—"
              }
            />
          </div>
          {decision?.signals?.length ? (
            <div className="mt-5 flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />{" "}
              {decision.signals.join("、")}
            </div>
          ) : null}
        </div>
        <div className="border-border flex flex-col justify-between border-t pt-5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-6">
          <div className="space-y-3 text-xs">
            <p>
              <span className="text-muted-foreground">申请人</span>
              <br />
              <span className="font-mono">{action.requested_by}</span>
            </p>
            <p className="break-all">
              <span className="text-muted-foreground">载荷指纹</span>
              <br />
              <span className="font-mono">
                {action.payload_hash.slice(0, 20)}…
              </span>
            </p>
            {action.external_transaction_id && (
              <p>
                <span className="text-muted-foreground">支付凭证</span>
                <br />
                <span className="font-mono">
                  {action.external_transaction_id}
                </span>
              </p>
            )}
          </div>
          {action.status === "pending_approval" && (
            <div className="mt-6 space-y-3">
              <Input
                value={comment}
                onChange={(event) => onComment(event.target.value)}
                placeholder="审批备注；拒绝时必填"
              />
              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant="outline"
                  disabled={busy || !comment.trim()}
                  onClick={() => onAction("reject")}
                >
                  拒绝
                </Button>
                <Button disabled={busy} onClick={() => onAction("approve")}>
                  批准退款
                </Button>
              </div>
            </div>
          )}
          {action.status === "approved" && (
            <Button
              className="mt-6"
              disabled={busy}
              onClick={() => onAction("execute")}
            >
              执行已批准退款
            </Button>
          )}
          {action.status === "rejected" && (
            <p className="bg-muted mt-6 rounded-lg p-3 text-sm">
              拒绝原因：{action.comment}
            </p>
          )}
        </div>
      </div>
    </article>
  );
}

function Fact({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="bg-muted/45 flex gap-3 rounded-lg p-3">
      <span className="text-muted-foreground [&>svg]:size-4">{icon}</span>
      <div>
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="mt-0.5">{value}</p>
      </div>
    </div>
  );
}

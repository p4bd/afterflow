"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Plus,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  afterSalesCasePath,
  approveAfterSalesAction,
  type AfterSalesAction,
  buildCaseIntakeInput,
  createAfterSalesCase,
  executeAfterSalesAction,
  fetchAfterSalesActions,
  fetchAfterSalesCases,
  formatAfterSalesMoney,
  rejectAfterSalesAction,
} from "@/core/after-sales";

const caseStatus: Record<string, string> = {
  awaiting_clarification: "待澄清",
  awaiting_evidence: "待补证",
  decided: "方案已生成",
  pending_approval: "待审批",
  approved: "待执行",
  rejected: "已拒绝",
  execution_processing: "退款处理中",
  completed: "已完成",
};

export default function AfterSalesPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [complaint, setComplaint] = useState("");
  const [orderId, setOrderId] = useState("");
  const cases = useQuery({
    queryKey: ["after-sales-cases"],
    queryFn: fetchAfterSalesCases,
  });
  const actions = useQuery({
    queryKey: ["after-sales-actions"],
    queryFn: fetchAfterSalesActions,
    refetchInterval: 15_000,
  });
  const intake = useMutation({
    mutationFn: () =>
      createAfterSalesCase(buildCaseIntakeInput(complaint, orderId)),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["after-sales-cases"] });
      router.push(afterSalesCasePath(created.id));
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody className="overflow-y-auto">
        <main className="mx-auto w-full max-w-6xl space-y-10 px-5 py-8 sm:px-8">
          <header className="flex flex-col justify-between gap-4 border-b pb-6 sm:flex-row sm:items-end">
            <div>
              <p className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-[0.18em] text-amber-700 uppercase dark:text-amber-400">
                <ShieldCheck className="size-4" /> AfterFlow case workspace
              </p>
              <h1 className="text-3xl font-semibold tracking-tight">
                售后案件工作台
              </h1>
              <p className="text-muted-foreground mt-2 text-sm">
                从客户原话开始，证据、方案、审批与执行结果都留在同一案件。
              </p>
            </div>
            <Button
              variant="outline"
              size="icon"
              onClick={() => {
                void cases.refetch();
                void actions.refetch();
              }}
              aria-label="刷新案件工作台"
            >
              <RefreshCw
                className={
                  cases.isFetching || actions.isFetching
                    ? "size-4 animate-spin"
                    : "size-4"
                }
              />
            </Button>
          </header>

          <section className="border-border bg-card rounded-2xl border p-5 shadow-sm sm:p-6">
            <div className="mb-4 flex items-center gap-2">
              <Plus className="size-5" />
              <h2 className="text-lg font-semibold">新建案件</h2>
            </div>
            <label className="text-sm font-medium" htmlFor="complaint">
              客户诉求原文
            </label>
            <textarea
              id="complaint"
              className="border-input bg-background focus-visible:ring-ring mt-2 min-h-28 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
              value={complaint}
              onChange={(event) => setComplaint(event.target.value)}
              placeholder="例如：买的耳机右边没声音，我想换一个。订单是 ORDER-1003。"
            />
            <div className="mt-3 flex flex-col gap-3 sm:flex-row">
              <Input
                value={orderId}
                onChange={(event) => setOrderId(event.target.value)}
                placeholder="订单号（可选，原文已包含则不用填）"
              />
              <Button
                disabled={!complaint.trim() || intake.isPending}
                onClick={() => intake.mutate()}
                className="sm:min-w-32"
              >
                创建并评估
              </Button>
            </div>
          </section>

          <section>
            <h2 className="mb-4 text-xl font-semibold">我的案件</h2>
            {cases.isLoading && (
              <p className="text-muted-foreground py-8 text-sm">
                正在加载案件…
              </p>
            )}
            {cases.error && (
              <p className="text-destructive text-sm">
                无法加载案件：{cases.error.message}
              </p>
            )}
            {!cases.isLoading && !cases.error && !cases.data?.length && (
              <div className="border-border text-muted-foreground rounded-xl border border-dashed py-10 text-center text-sm">
                还没有案件，从上方粘贴一段客户投诉开始。
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {cases.data?.map((item) => (
                <Link
                  key={item.id}
                  href={afterSalesCasePath(item.id)}
                  className="border-border bg-card hover:border-foreground/30 rounded-xl border p-5 transition-colors"
                >
                  <div className="flex items-center justify-between gap-3">
                    <Badge variant="outline">
                      {caseStatus[item.status] ?? item.status}
                    </Badge>
                    <span className="text-muted-foreground font-mono text-xs">
                      {item.order_id ?? "待确认订单"}
                    </span>
                  </div>
                  <p className="mt-4 line-clamp-2 font-medium">
                    {item.complaint_text || item.issue_type}
                  </p>
                  <p className="text-muted-foreground mt-3 flex items-center justify-between text-xs">
                    下一步：{item.next_step}
                    <ArrowRight className="size-4" />
                  </p>
                </Link>
              ))}
            </div>
          </section>

          <section className="border-t pt-8">
            <h2 className="text-xl font-semibold">主管审批队列</h2>
            <p className="text-muted-foreground mt-1 mb-4 text-sm">
              审批身份来自登录会话，不接受聊天中的授权声明。
            </p>
            {!actions.isLoading && !actions.error && !actions.data?.length && (
              <div className="border-border rounded-xl border border-dashed py-10 text-center">
                <CheckCircle2 className="mx-auto mb-2 size-6 text-emerald-600" />
                <p className="text-sm">审批队列已清空</p>
              </div>
            )}
            <div className="grid gap-4">
              {actions.data?.map((action) => (
                <ApprovalCard key={action.id} action={action} />
              ))}
            </div>
          </section>
        </main>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function ApprovalCard({ action }: { action: AfterSalesAction }) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const mutation = useMutation({
    mutationFn: (operation: "approve" | "reject" | "execute") => {
      if (operation === "approve")
        return approveAfterSalesAction(action, comment);
      if (operation === "reject")
        return rejectAfterSalesAction(action, comment);
      return executeAfterSalesAction(action);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["after-sales-actions"] });
      void queryClient.invalidateQueries({ queryKey: ["after-sales-cases"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const amount = action.payload.amount ?? action.payload.estimated_cost;
  return (
    <article className="border-border bg-card rounded-xl border p-5 shadow-sm">
      <div className="flex flex-col justify-between gap-4 sm:flex-row">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{caseStatus[action.status] ?? action.status}</Badge>
            <Badge variant="secondary">风险 {action.risk_level}</Badge>
            <span className="text-muted-foreground font-mono text-xs">
              v{action.version}
            </span>
          </div>
          <p className="mt-3 font-semibold">
            {action.action_type === "resend" ? "免退补发" : "原路退款"}
          </p>
          <p className="text-muted-foreground mt-1 text-sm">
            订单 {action.payload.order_id}
            {amount !== undefined ? ` · ${formatAfterSalesMoney(amount)}` : ""}
          </p>
          {action.case && (
            <Link
              className="mt-3 inline-flex items-center gap-1 text-sm underline"
              href={afterSalesCasePath(action.case_id)}
            >
              查看案件证据 <ArrowRight className="size-3" />
            </Link>
          )}
        </div>
        <div className="flex min-w-56 flex-col justify-end gap-2">
          {action.status === "pending_approval" && (
            <Input
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="审批备注；拒绝时必填"
            />
          )}
          {action.status === "pending_approval" && (
            <div className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                disabled={!comment.trim() || mutation.isPending}
                onClick={() => mutation.mutate("reject")}
              >
                拒绝
              </Button>
              <Button
                disabled={mutation.isPending}
                onClick={() => mutation.mutate("approve")}
              >
                批准
              </Button>
            </div>
          )}
          {action.status === "approved" && (
            <Button
              disabled={mutation.isPending}
              onClick={() => mutation.mutate("execute")}
            >
              执行已批准动作
            </Button>
          )}
          {action.external_transaction_id && (
            <p className="font-mono text-xs break-all">
              凭证 {action.external_transaction_id}
            </p>
          )}
        </div>
      </div>
    </article>
  );
}

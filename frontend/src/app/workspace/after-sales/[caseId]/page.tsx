"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
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
  createAfterSalesAction,
  fetchAfterSalesCase,
  formatAfterSalesMoney,
  supplementAfterSalesCase,
} from "@/core/after-sales";

export default function AfterSalesCasePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [orderId, setOrderId] = useState("");
  const [issueType, setIssueType] = useState("");
  const [expectation, setExpectation] = useState<"" | "refund" | "replacement">(
    "",
  );
  const [confirmed, setConfirmed] = useState(false);
  const [damageLevel, setDamageLevel] = useState<
    "none" | "minor" | "major" | "destroyed"
  >("major");
  const query = useQuery({
    queryKey: ["after-sales-case", caseId],
    queryFn: () => fetchAfterSalesCase(caseId),
    refetchInterval: 15_000,
  });
  const supplement = useMutation({
    mutationFn: () =>
      supplementAfterSalesCase(caseId, {
        note,
        ...(orderId.trim() ? { order_id: orderId.trim() } : {}),
        ...(issueType ? { issue_type: issueType } : {}),
        ...(expectation ? { customer_expectation: expectation } : {}),
        ...(confirmed
          ? { visual_evidence_confirmed: true, damage_level: damageLevel }
          : {}),
      }),
    onSuccess: () => {
      setNote("");
      void queryClient.invalidateQueries({
        queryKey: ["after-sales-case", caseId],
      });
      void queryClient.invalidateQueries({ queryKey: ["after-sales-cases"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const createAction = useMutation({
    mutationFn: () => createAfterSalesAction(caseId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["after-sales-case", caseId],
      });
      void queryClient.invalidateQueries({ queryKey: ["after-sales-actions"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const item = query.data;
  if (query.isLoading)
    return (
      <Shell>
        <p className="text-muted-foreground p-8">正在加载案件…</p>
      </Shell>
    );
  if (query.error || !item)
    return (
      <Shell>
        <p className="text-destructive p-8">
          无法加载案件：{query.error?.message ?? "案件不存在"}
        </p>
      </Shell>
    );
  const evidence = item.evidence_json;
  const decision = item.decision_json;
  const reverse = decision.reverse;
  const activeAction = item.actions?.find((action) =>
    ["pending_approval", "approved"].includes(action.status),
  );

  return (
    <Shell>
      <main className="mx-auto w-full max-w-6xl space-y-6 px-5 py-8 sm:px-8">
        <Link
          href="/workspace/after-sales"
          className="text-muted-foreground inline-flex items-center gap-1 text-sm"
        >
          <ArrowLeft className="size-4" /> 返回案件工作台
        </Link>
        <header className="flex flex-col justify-between gap-4 border-b pb-6 sm:flex-row sm:items-end">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{item.status}</Badge>
              <span className="text-muted-foreground font-mono text-xs">
                {item.id}
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold">
              {item.order_id ?? "待确认订单"} ·{" "}
              {item.issue_type ?? "待确认问题"}
            </h1>
            <p className="mt-2 max-w-3xl">{item.complaint_text}</p>
          </div>
          <Button
            variant="outline"
            size="icon"
            onClick={() => void query.refetch()}
            aria-label="刷新案件"
          >
            <RefreshCw
              className={query.isFetching ? "size-4 animate-spin" : "size-4"}
            />
          </Button>
        </header>

        <div className="grid gap-6 lg:grid-cols-[1.4fr_0.6fr]">
          <div className="space-y-6">
            <Card title="证据包">
              <p className="text-muted-foreground text-xs">
                快照版本 {evidence.revision ?? 1} ·{" "}
                {evidence.snapshot_at
                  ? new Date(evidence.snapshot_at).toLocaleString("zh-CN")
                  : "等待取证"}
              </p>
              <h3 className="mt-4 text-sm font-semibold">客户主张</h3>
              <List items={evidence.claims} />
              <h3 className="mt-4 text-sm font-semibold">已查事实</h3>
              <div className="mt-2 grid gap-2">
                {evidence.facts?.map((fact) => (
                  <div
                    key={fact.source}
                    className="bg-muted rounded-lg p-3 text-xs"
                  >
                    <strong>{fact.source}</strong>
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(fact.value, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
              {!!evidence.missing?.length && (
                <Notice title="缺失证据" items={evidence.missing} />
              )}
              {!!evidence.conflicts?.length && (
                <Notice
                  title="事实冲突（不等同欺诈）"
                  items={evidence.conflicts}
                />
              )}
            </Card>

            <Card title="确定性处理方案">
              {reverse ? (
                <div className="grid gap-2 text-sm sm:grid-cols-2">
                  <Fact label="逆向动作" value={reverse.action} />
                  <Fact
                    label="估算履约成本"
                    value={formatAfterSalesMoney(
                      reverse.estimated_resolution_cost,
                    )}
                  />
                  <Fact
                    label="是否需要退回"
                    value={
                      reverse.requires_return
                        ? "需要；未收货/质检前不可结算"
                        : "免退"
                    }
                  />
                  <Fact
                    label="依据"
                    value={reverse.signals.join("、") || "—"}
                  />
                </div>
              ) : decision.eligibility ? (
                <div className="grid gap-2 text-sm sm:grid-cols-2">
                  <Fact label="资格" value={decision.eligibility} />
                  <Fact
                    label="退款金额"
                    value={formatAfterSalesMoney(decision.refund_amount ?? 0)}
                  />
                  <Fact label="风险" value={decision.risk_level ?? "—"} />
                  <Fact
                    label="审批原因"
                    value={
                      decision.approval_reasons?.length
                        ? decision.approval_reasons.join("、")
                        : "策略内自动批准"
                    }
                  />
                  <Fact
                    label="政策"
                    value={
                      decision.policy_refs?.length
                        ? decision.policy_refs.join("、")
                        : "—"
                    }
                  />
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">
                  信息尚不足，补充后会在原案件重新评估。
                </p>
              )}
              {!activeAction && item.status === "decided" && (
                <Button
                  className="mt-5"
                  disabled={createAction.isPending}
                  onClick={() => createAction.mutate()}
                >
                  提交当前方案
                </Button>
              )}
              {activeAction && (
                <p className="bg-muted mt-5 rounded-lg p-3 text-sm">
                  当前动作：{activeAction.action_type} · {activeAction.status}
                  。请由主管在审批队列继续。
                </p>
              )}
            </Card>

            <Card title="补充并继续原案件">
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  value={orderId}
                  onChange={(event) => setOrderId(event.target.value)}
                  placeholder="纠正订单号（可选）"
                />
                <select
                  className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                  value={issueType}
                  onChange={(event) => setIssueType(event.target.value)}
                  aria-label="纠正问题类型"
                >
                  <option value="">保留当前问题类型</option>
                  <option value="delivery_not_received">未收到货</option>
                  <option value="damaged_item">商品破损</option>
                  <option value="wrong_item">错发商品</option>
                  <option value="quality_issue">质量问题</option>
                  <option value="refund_amount_dispute">退款金额争议</option>
                </select>
                <select
                  className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                  value={expectation}
                  onChange={(event) =>
                    setExpectation(event.target.value as typeof expectation)
                  }
                  aria-label="客户期望"
                >
                  <option value="">保留当前客户期望</option>
                  <option value="refund">退款</option>
                  <option value="replacement">换货/补发</option>
                </select>
                <label className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />{" "}
                  我已人工核对图片证据
                </label>
                {confirmed && (
                  <select
                    className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                    value={damageLevel}
                    onChange={(event) =>
                      setDamageLevel(event.target.value as typeof damageLevel)
                    }
                    aria-label="损坏程度"
                  >
                    <option value="none">无损</option>
                    <option value="minor">轻微</option>
                    <option value="major">严重</option>
                    <option value="destroyed">毁损</option>
                  </select>
                )}
              </div>
              <Input
                className="mt-3"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="补充说明（必填）"
              />
              <Button
                className="mt-3"
                variant="outline"
                disabled={!note.trim() || supplement.isPending}
                onClick={() => supplement.mutate()}
              >
                保存并重新评估
              </Button>
            </Card>
          </div>

          <aside className="space-y-6">
            <Card title="当前待办">
              <p className="font-medium">{item.next_step}</p>
              <p className="text-muted-foreground mt-2 text-sm">
                客户期望：{item.customer_expectation ?? "待确认"}
              </p>
              {item.thread_id && (
                <Link
                  href={`/workspace/chats/${encodeURIComponent(item.thread_id)}`}
                  className="mt-3 inline-flex items-center gap-1 text-sm underline"
                >
                  打开关联会话 <ExternalLink className="size-3" />
                </Link>
              )}
            </Card>
            <Card title="客户回复草稿">
              <p className="text-sm leading-6">
                {item.reply_draft || "补充信息后生成。"}
              </p>
            </Card>
            <Card title="业务时间线">
              <ol className="space-y-4">
                {item.events?.map((event) => (
                  <li key={event.id} className="border-l pl-3 text-sm">
                    <p className="font-medium">{event.event_type}</p>
                    <p className="text-muted-foreground text-xs">
                      {new Date(event.created_at).toLocaleString("zh-CN")} ·{" "}
                      {event.actor}
                    </p>
                  </li>
                ))}
              </ol>
            </Card>
          </aside>
        </div>
      </main>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody className="overflow-y-auto">{children}</WorkspaceBody>
    </WorkspaceContainer>
  );
}
function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-border bg-card rounded-2xl border p-5 shadow-sm">
      <h2 className="mb-4 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}
function List({ items }: { items?: string[] }) {
  return (
    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
      {items?.length ? (
        items.map((item) => <li key={item}>{item}</li>)
      ) : (
        <li className="text-muted-foreground list-none">暂无</li>
      )}
    </ul>
  );
}
function Notice({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mt-4 rounded-lg bg-amber-500/10 p-3 text-sm">
      <strong>{title}</strong>
      <List items={items} />
    </div>
  );
}
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-muted rounded-lg p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1">{value}</p>
    </div>
  );
}

# AfterFlow 项目知识

> 用于核验候选人的技术 claim。当前仓库代码和 `docs/` 优先级更高。

## 1. 定位和架构

AfterFlow 是面向电商售后的证据驱动决策与执行系统。LLM 负责理解、取证编排和解释；领域代码负责金额、资格、风险、权限、状态和执行。

```text
Web 工作台
  → Gateway / Auth / RBAC
  → Agent / Skills / Guardrail
  → 退款决策 / 逆向履约 / 运营预警 / Action 状态机
  → Repository / Provider / Refund Executor
```

API 与 Agent Tool 必须复用同一领域函数。

## 1.5 LangGraph 编排（两张 StateGraph）

文件：`backend/app/after_sales/workflow.py`

AfterFlow 用 LangGraph 做**确定性编排层**（不是把 ReAct 包一层）：两张显式 StateGraph，图节点调用领域纯函数，资金安全层（actions.py）是图调用的对象、不被改写。

- **`after_sales_case_graph`**（案件评估编排）：`build_context → decide_refund`，条件边按 issue 类型 + 是否带逆向上下文路由到 `reverse_decision`；`route` 字段记录实际路径（refund/reverse/error），可审计可测试。
- **`reverse_disposition_graph`**（退货处置生命周期）：`start → (returnless? settle | receive → inspect → {settle|dispose by grade})`，把 `build_disposition`/`advance_disposition`/`mark_received` 串成显式图；destroyed（grade d）走 dispose 写损路由。
- `evaluate_mock_case` 委托 `run_case_evaluation`（图），150 条评测直调纯函数、不经图，逐字段等价。

**为什么用图而不是 if/else**：① 状态转移拓扑可 `get_graph()` 渲染、可测试；② 条件路由是数据（`add_conditional_edges`），`route` 可审计；③ `needs_evidence` 是未来 `interrupt()` HITL 的预留位；④ 未来可接 Langfuse 节点级 span。不 checkpoint、不写库、图内无副作用——纯编排。

## 1.6 MCP client + RAG 知识检索（inform 层）

文件：`backend/app/after_sales/knowledge.py`

AfterFlow 用 **MCP client** 调用外部知识服务（例如另一个 RAG+MCP 项目的 `search_knowledge`），供 Agent 检索政策/知识文本做引用。这是 **inform 层**——只提供解释与引用；**金额/资格/审批由确定性引擎 validate**，两层不混。

- `McpKnowledgeClient`：用 langchain-mcp-adapters 的 `MultiServerMCPClient` 连 `afterflow-rag` server，调用其 `search_knowledge` 工具。
- `retrieve_knowledge`：MCP 不可用/空结果时 **fail-open** 到确定性 mock 政策知识（带 `policy_id@version` 引用），不破坏案件流程。
- Tool：`search_after_sales_knowledge`（第 13 个 after-sales 工具）。
- 配置：`extensions_config.example.json` 有 `afterflow-rag` server 示例（默认 disabled）。

**面试口径**：「政策文本走 RAG/MCP 检索、带引用解释给客户；但能不能退、退多少是 `PolicySnapshot` + `decide_resolution` 的确定性输出——RAG inform、引擎 validate，两层不混。」

## 2. 退款决策

文件：`backend/app/after_sales/schemas.py`、`decision.py`

- `Eligibility`：`ELIGIBLE`、`ELIGIBLE_WITH_APPROVAL`、`INELIGIBLE`、`NEEDS_EVIDENCE`。
- `NO_REFUNDABLE_BALANCE` 是 reason code，不是资格枚举。
- 顺序：政策覆盖 → 必要证据 → 余额 → 金额 → 风险 → 审批。
- 金额：`min(item_paid + policy-refundable shipping, refundable_balance)`。
- 操作员限额来自**服务端角色查表**（`resolve_operator_refund_limit`），不接受客户端/LLM 传值。
- 风险：`risk_scoring.py` 打分卡——多维信号（签收后时长、重复索赔、POD 冲突、退款率、退款次数、新账号、地址变更、设备复用、高价值）加权成 0-100 分，映射 `AUTO / REVIEW / SUPERVISOR / FOUR_EYES` 四级介入；每个信号带 points+reason。决定性信号（重复索赔≥3、POD 冲突）强制 supervisor。
- 审批原因：超过操作员限额、达到政策人工阈值、高风险、非 AUTO tier 强制 review。

## 3. 逆向履约

文件：`reverse.py`、`reverse_execution.py`

- 支持 damaged / wrong / quality。
- 视觉证据必须人工确认。
- `reverse_cost = return_shipping + handling`。
- `recovery = expected_recovery_value × RECOVERY_FACTOR[damage_level]`（none 1.0 / minor 0.7 / major 0.4 / destroyed 0.0）。
- `return_refund_cost = max(0, refund + reverse_cost - recovery)`。
- 用户偏好换货且有库存时换货；否则按残值是否覆盖逆向成本决定退回或仅退款。
- 质检分级：`none→A restock / minor→B refurbish / major→C liquidate / destroyed→D dispose`。
- 退货异常：`serial_matches=False`（返回非原品）默认冻结转人工，**wrong_item 除外**。
- 处置状态机：`build_disposition`（从决策建单）→ `mark_received`（发标签→收货→开 SLA）→ 质检 → 结算；48h SLA 超时预警。
- 当前不是所有候选方案的全局最小成本搜索。

## 4. 运营预警

文件：`risk_ops.py`

- 维度：sku / carrier / warehouse。
- 默认阈值：订单 50、问题数 5、问题率 5%、相对上期 1.5 倍、损失 500000 分。
- 输出 medium/high 调查线索，不宣称根因。

## 5. Action 状态机

文件：`actions.py`

- 状态：pending_approval / approved / rejected / completed。
- 关键字段：payload hash、确定性幂等键、version、expires_at、requested/approved by、`approvers_required`、`approver_ids`、`reserved`。
- 创建条件：资格可执行、动作是原路退款或退货退款、金额大于 0。
- 无审批原因且非 high 时由 system-policy 自动批准（会立即冻结金额）。
- 批准/拒绝检查 pending、version、过期、角色、**申请人不能自批（与风险等级无关）**、同一人不能重复签。
- 双人审批：`approvers_required=2`（risk_tier=four_eyes）时，两个不同的人签完才 APPROVED。
- 执行检查 approved、version、过期、提交 payload hash 与批准一致、**冻结额**（从 reserved 扣）；成功后 reserved 置 False。
- **资金冻结 authorize-capture**：approve 时 `reserve()`（available→reserved），execute `refund()` 从 reserved 扣，reject/过期 `release()`；过期未执行由 reaper（`release_expired_reservations`）释放。
- Mock Executor 用内存 dict 按 idempotency key 返回同一交易号。

## 6. Guardrail

文件：`guardrail.py`

- 保护创建 Action 和执行 Action 两个写 Tool。
- 同步路径无法查库，对保护 Tool 直接拒绝。
- 异步路径检查子 Agent、认证；执行还检查 admin 角色、状态、version、过期和 hash。
- Guardrail 是 Tool 前置门禁，领域函数仍是权威边界。

## 7. Tool、Skill 和持久化

- Tool 共 12 个：7 查询、退款评估、逆向评估、运营扫描、创建 Action、执行 Action。
- 创建 Action 会写库和事件，只是不直接退款。
- 领域 Skill 共 7 个；通用 bootstrap 不计入。
- 三表：service_cases / action_requests / case_events（`case_events` 带 `seq` 单调列）。
- Repository 以 user_id 隔离普通用户数据，用 `id + expected_version` 条件 UPDATE 实现乐观锁。
- 同案活跃 Action 有**数据库级部分唯一索引**兜底（`uq_action_one_active_per_case`，`WHERE status IN ('pending_approval','approved')`），`IntegrityError` 归一成 `ConcurrentActionError`。
- **审计哈希链**：`case_events` 每条带 `prev_hash`+`event_hash`（sha256 覆盖 case/type/actor/run_id/trace_id/metadata），`verify_event_chain` 校验，改任何历史（含 trace_id）下游全断；无外部锚定，防局部篡改。

## 8. API 和产品入口

API 支持：创建/查询 case，创建 action，列出 action，批准、拒绝和执行。

产品入口：

- `/workspace/after-sales`：审批台
- `/workspace/chats/new`：智能售后
- `/workspace/scheduled-tasks`：运营巡检

## 9. 评测

固定 150 条 = 100 回归 + 34 人工边界 + 16 风控信号（81 refund / 32 reverse / 37 operations），另加 10 条真实业务流场景（`after_sales_scenarios.json`）和注入扰动轨迹测试。

2026-08-20（同一 150 集同分母）：

- 引擎：coverage 100%、field 100%、exact 100%（150/150）。
- 纯 deepseek-v4-flash：coverage 100%、field 54.5%、exact 12.7%（19/150）。
- LLM refund amount 67.9%、reverse cost 12.5%、severity 21.6%、**risk_score/risk_tier 0%**（未给评分规则算不出）、**requires_return 0%**。
- 新 34 条 field 51.2%，原 100 条 field 55.8%。

含义：引擎通过当前合同回归；纯 LLM 容易错金额、成本和阈值，且算不出确定性风险分。不能推出政策行业最优或生产准确率 100%。baseline 代表直接让模型决策，不代表增强 Agent 上限。**旧 134 集 20.1% 是不同分母，不要再混用**。

## 10. Demo 与边界

- 主流程：`scripts/demo_afterflow.py`
- 故障注入：`backend/scripts/demo_afterflow_failures.py`
- RBAC：`backend/scripts/demo_afterflow_rbac.py`
- four-eyes 演示：ORDER-1004（大额+重复索赔，risk_tier=four_eyes）
- 启动：`make config`、`make dev`

当前边界：4 个 Mock 订单、Mock Executor（in-memory 冻结，重启重置）、视觉模型未接、SQLite、单 issue type、逆向处置工具接线未完成、评分卡权重未校准。

## 11. 高频事实错误

- 把 `NO_REFUNDABLE_BALANCE` 当第五种资格。
- 说创建 Action 无副作用。
- 说执行时检查两个 hash（现在只检查一次提交 payload）。
- 说 destroyed 的货还有全额残值（有 RECOVERY_FACTOR 打折）。
- 说「自批只禁高风险」（现在是所有风险等级都不许自批）。
- 说 four-eyes 在 demo 里能随便触发（只有 ORDER-1004 大额单能触发）。
- 说资金冻结会自己释放（靠 reaper 才释放）。
- 把固定中间件数量当稳定事实。
- 把合同回归 100% 说成生产准确率。
- 把 admin/user 演示角色说成完整企业 IAM。

## 12. 初学者解释顺序

候选人不理解术语时，先用失败场景解释，再给正式名词：

```text
批准后金额被换 → payload hash
两个旧页面同时操作 → version 乐观锁
授权放太久 → expires_at
网络重试重复退款 → idempotency key
Agent 门禁被绕过 → 领域层重复校验
```

不要用更多术语解释一个术语。先讲事故，再讲机制，再落到代码文件。

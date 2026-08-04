# AfterFlow 系统设计

> AfterFlow：电商售后理赔、逆向履约与风险运营 Agent。
> 本文是系统设计文档，对应代码见 `backend/app/after_sales/`，面试官可直接据此提问。

---

## 1. 系统概述

**定位**：AfterFlow 不是电商系统本身，而是夹在「真实业务系统」与「客服/风控人员」之间的**售后处置决策与执行控制系统**。它把「凭感觉退款」变成「证据驱动、确定性决策、可审批、幂等执行、可追溯」的受控闭环。

**核心问题**：通用 LLM 只能对售后诉求给出"建议"，无法独立可靠地完成一件**对钱负责**的闭环——实时取证、版本化政策匹配、精确金额计算、风险识别、权限强制、幂等执行、完整审计。

**一句话技术定位**：LLM 只负责**理解、编排、沟通**；金额、资格、成本、权限、执行由**确定性代码 + 状态机 + Guardrail** 保证。

---

## 2. 设计原则

1. **LLM 不碰钱**：退款金额、资格、风险、审批结论全部来自确定性决策引擎，LLM 无权计算或改写。
2. **Prompt 不是安全边界**：身份、权限、状态合法性由服务端 RBAC + 状态机 + Guardrail 强制，用户说"已批准"无效。
3. **审批与执行分离**：Agent 只能创建提案（Action Request）；批准是独立的授权面；执行前二次校验。
4. **幂等**：副作用携带幂等键，重复请求不产生第二次扣款。
5. **可审计**：证据、政策、审批、执行全链路写入 `case_events`。
6. **可信来源**：业务事实以工具返回为准，用户陈述是待核实的 claim，不是事实。

---

## 3. 分层架构

```
┌────────────────────────────────────────────────────────────┐
│ L6 交互层  前端聊天 + 审批台 (/workspace/after-sales)        │
│            REST API /api/after-sales/*                      │
├────────────────────────────────────────────────────────────┤
│ L5 人工层  客服(发起案件/对话) · 主管(审批/执行) · 风控(看预警)│
├────────────────────────────────────────────────────────────┤
│ L4 编排层  afterflow-agent（LLM，运行于 DeerFlow 底座）       │
│            意图理解 → 按 SOP 调工具 → 解释决策 → 创建提案     │
├────────────────────────────────────────────────────────────┤
│ L3 控制层  actions.py 审批状态机 · guardrail.py fail-closed   │
│            repository.py 三表持久化 · case_events 审计        │
├────────────────────────────────────────────────────────────┤
│ L2 决策层  decision.py 退款资格/金额/风险 · reverse.py 逆向成本│
│            risk_ops.py 运营异常    （纯函数，无 LLM）         │
├────────────────────────────────────────────────────────────┤
│ L1 适配层  tools.py（LangChain 工具）读取订单/支付/物流/政策    │
│            写入退款/案件      —— 当前接 mock_data，生产换真实   │
└────────────────────────────────────────────────────────────┘
```

**依赖方向**：L1→L2→L3 单向向下，L4 编排所有下层；所有层都不依赖 LLM 的"判断"。

---

## 4. 核心组件

### 4.1 确定性决策引擎（`decision.py`）

- 输入 `DecisionInput`：问题类型、订单/支付/物流证据、客户风险、政策快照、操作员限额、图片确认。
- 输出 `DecisionResult`：资格、动作、**精确金额**、风险等级、审批需求、政策引用、风险信号。
- 金额规则（纯函数，有单测）：
  ```
  requested = 实付商品金额 (+ 政策规定可退的运费)
  refund    = min(requested, 支付可退余额)
  ```
  资格分支：未覆盖 → ineligible；缺证据 → needs_evidence；余额为 0 → 不可退；否则按金额/风险判定需审批。
- 风险规则（可解释信号，非神秘分数）：180 天未收到索赔次数、有无签收凭证、高价值金额。
- **为什么是纯函数**：可单测、100% 精确、与 LLM 输出解耦（换模型不影响决策）。

### 4.2 逆向履约引擎（`reverse.py`）

- 比较「仅退款不退货 / 退货退款 / 换货 / 维修 / 补偿」的成本经济学：
  ```
  reverse_cost   = 退货运费 + 处理成本
  return_refund  = max(0, 退款额 + reverse_cost − 残值回收)
  replacement    = max(0, 换货成本 + 换货运费 + reverse_cost − 残值)
  ```
- 决策输入包含 `VisualEvidence`，**要求 `human_confirmed=True`** 才进入成本决策——视觉模型结果必须人工确认，防止"看图定罪"。

### 4.3 风险运营（`risk_ops.py`）

- 分母感知的异常检测：`current_issue_cases / current_orders` vs 上期。
- 保护项：最小样本量（订单 ≥50、案件 ≥5）、增长率阈值（≥1.5x）、损失阈值。
- 只输出「预警 + 信号」，不宣称根因——样本不足只标记观察。

### 4.4 审批状态机与幂等执行（`actions.py`）

- `ActionRequest`：payload + `payload_hash`(SHA-256) + `idempotency_key` + `version`(乐观锁) + `expires_at`。
- 状态机：`pending_approval → approved/rejected → completed`。
- 权限内低/中风险 → 创建时 `approved_by="system-policy"` 自动批准；否则待审批。
- `execute_approved_action` 执行前**二次校验**：
  1. 状态是 approved
  2. version 匹配（乐观锁）
  3. 未过期
  4. `payload_hash(action) == payload_hash(提交)`（防偷换金额）
  5. **可退余额未变化**
  6. 支付执行器按幂等键去重 → 重复请求返回同一交易号
- `MockRefundExecutor`：内存 dict，按 `idempotency_key` 幂等；生产替换为真实支付适配器。

### 4.5 Guardrail（`guardrail.py`）— fail-closed

- 工具调用前授权（`AfterSalesGuardrailProvider`）：
  - 受保护写工具：`create_after_sales_action`、`execute_approved_action`。
  - 子代理调用 → 拒绝（`subagent_forbidden`）。
  - 未认证 → 拒绝。
  - `execute_approved_action`：非 admin 拒绝；action 非 approved 拒绝；version/hash/过期全查。
- **Guardrail 只是第一道门**：`actions.py` 服务内部重复全部关键校验，防止绕过 Agent 直接调 API。

### 4.6 持久化（三表 + Alembic migration `0004`）

| 表 | 职责 |
| --- | --- |
| `service_cases` | 案件：订单、问题类型、状态、证据快照、决策快照、乐观锁 |
| `action_requests` | 动作提案：payload、hash、幂等键、审批信息、过期、外部交易号、版本 |
| `case_events` | 只追加审计事件：evidence_collected / decision_generated / approval_* / execution_* |

- 案件与 Action 是**权威业务状态**，不存 Prompt/Memory。
- 用户隔离：所有行按 `user_id` 归属；Agent 数据按 `.deer-flow/users/<user_id>/` 目录隔离。

---

## 5. 安全模型

| 威胁 | 防线 |
| --- | --- |
| 用户自称"主管已批准" | 身份来自服务端认证，prompt 无效力；审批必须过审批面 |
| 客服越权审批/执行 | RBAC 角色门禁（`system_role`）+ Guardrail |
| 审批人自审高风险 | `requested_by == approver_id` 且 high → 拒绝 |
| 审批后偷换金额 | payload_hash + 乐观锁 version |
| 重复退款 | 幂等键 + 状态机（completed 不可再执行） |
| 审批期间余额变化 | 执行时二次校验可退余额 |
| Subagent 申请退款 | Guardrail `subagent_forbidden` |
| 伪造/过期审批 | expires_at + version 校验 |
| 审计缺失 | case_events 全链路事件 |

---

## 6. 状态机

```
[案件] intake → evidence_gathering → decided（决策引擎落库）
              ↘ needs_info（缺证据，澄清）

[Action] proposed → pending_approval → approved → executing → completed
                          ↘ rejected/expired（转人工复核）
                                ↘ executing → failed → escalated
```

- 状态转换全部由确定性函数执行（`actions.py`），API 与 Agent 工具**复用同一逻辑**，避免不同入口产生不同规则。

---

## 7. 评测方法

- 固定 100 条跨域评测集（50 退款 / 25 逆向 / 25 运营），含对抗样本（越权、重复退款、政策边界）。
- 模型无关评分器：`coverage` / `field_accuracy` / `exact_case_accuracy`，按 kind 分列。
- **Baseline 实测**：决策引擎 100% 整案精确 vs 纯 LLM 14%（详见 [EVALUATION.md](EVALUATION.md)）。
- 复现：`backend/scripts/evaluate_afterflow_baseline.py`。

---

## 8. 与 DeerFlow 底座的关系

| 复用（底座，未改核心） | 新增（AfterFlow 自研） |
| --- | --- |
| LangGraph 运行时、中间件链（31 个）、记忆、沙箱、MCP、流式、子代理、配置热加载 | 决策引擎（decision/reverse/risk_ops）、审批状态机（actions）、Guardrail（guardrail）、三表持久化 + migration、7 个领域 Skill、前端审批台、评测集与评分器 |

---

## 9. 已知边界与生产化路径

| 现状 | 生产化 |
| --- | --- |
| 数据全 Mock（`mock_data.py` 3 个订单） | 替换为真实 OMS/支付/物流/CRM 适配器（决策契约不变） |
| Mock 退款执行器（假交易号） | 真实支付网关适配器（保留幂等键协议） |
| 图片/OCR 未接入（模型无视觉） | 视觉模型 + 人工确认协议（`human_confirmed` 已就绪） |
| SQLite 单机 | Postgres 多实例 |
| 免认证/双用户演示 | 完整 IAM/SSO |

---

## 10. 关键设计决策

| 决策 | 取舍 | 为什么 |
| --- | --- | --- |
| 确定性引擎算钱，LLM 不碰 | LLM 通用性差，但资金零容错 | 有量化证据（14% vs 100%） |
| 审批独立于对话 | 多一步人工 | 对话里的"同意"无安全效力 |
| 幂等 + payload hash | 实现复杂度 | 防止资损和篡改 |
| 案件由受理系统创建（API） | Agent 无法自助建案 | 案件是权威状态，由确定性入口落库 |
| Mock 优先 | 非真实系统 | 聚焦架构，可替换适配器 |

---

*关联文档：[EVALUATION.md](EVALUATION.md)（评测）、[DEMO.md](DEMO.md)（演示）、《垂直业务Agent_售后退款决策与执行设计.md》（原始设计）。*

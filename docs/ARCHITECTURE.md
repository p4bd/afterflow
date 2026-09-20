# AfterFlow 系统设计

## 1. 设计目标

AfterFlow 处理资金相关售后决策。核心目标不是让模型“更会回答”，而是让每个结论都可验证、每个副作用都可授权、每次执行都可追溯。

## 2. 分层

```text
Web 工作台
  ├─ 自然语言受理与案件详情
  ├─ 案件内补证/续办
  ├─ 主管审批队列
  └─ 运营巡检
        ↓
Gateway API / Auth / RBAC
        ↓
Agent 编排 + 领域 Skills + Guardrail
        ↓
LangGraph 编排层（workflow.py）
  ├─ after_sales_case_graph（取证→决策→按问题/风险路由）
  └─ reverse_disposition_graph（退货处置生命周期，按残值分级路由）
        ↓
AfterFlow 领域服务
  ├─ refund decision
  ├─ reverse fulfillment
  ├─ risk operations
  └─ action state machine
        ↓
Repository / Mock providers / payment executor
```

## 3. 决策边界

| 组件 | 可以做 | 不可以做 |
| --- | --- | --- |
| LLM | 理解诉求、规划取证、归纳证据、解释方案 | 计算金额、批准退款、修改权威状态 |
| Skill | 规定 SOP、工具顺序和输出合同 | 产生资金副作用 |
| Tool | 查询业务系统、调用领域服务 | 绕过授权和状态机 |
| 决策引擎 | 计算资格、金额、风险和审批要求 | 自由生成政策 |
| 知识检索（RAG/MCP） | 检索政策文本、提供引用（inform） | 决定金额/资格/审批（validate 在决策引擎） |
| 审批服务 | 校验身份、角色、版本、过期和 hash | 接受对话中的身份声明 |
| 执行器 | 二次校验并幂等执行 | 执行未批准 Action |

## 4. 退款主流程

```text
complaint_received
  → awaiting_clarification（缺订单/问题）
  → evidence_gathering
  → awaiting_evidence（缺证据/事实冲突，可续办）
  → decided
  → proposed
  → pending_approval / approved
  → executing
  → completed / failed / escalated
```

关键约束：

- 用户陈述是 claim，不是事实。
- 政策按订单时间匹配版本。
- 金额由可退余额、已付金额和政策共同封顶。
- 操作员可批限额来自服务端角色查表，不接受客户端或对话中提供的值。
- 高风险只代表需要复核，不直接等同欺诈。
- 审批后修改金额会导致 payload hash 不匹配。
- 执行前重新读取实时可退余额，防止审批期间状态变化；退款成功后余额原子扣减。
- 同一幂等键重复执行返回同一外部交易结果（确定性业务幂等键）。

## 5. 授权模型

- 操作员可以受理、取证和创建 Action。
- 主管可以审批和执行。
- 申请人不得审批自己的资金 Action（maker-checker，与风险等级无关）。
- Subagent 不得创建或执行资金写操作。
- 可信身份来自服务端认证上下文，不来自 Prompt。
- Guardrail 是前置门禁，领域执行器仍重复全部关键校验。

## 6. 数据模型

三张核心表；受理字段直接扩展在案件表，不为每个证据字段拆表：

- `service_cases`：客户原话、期望、关联会话、当前待办、回复草稿、证据快照、决策结果和状态。
- `action_requests`：待审批/待执行副作用、载荷指纹、版本和有效期。
- `case_events`：受理、决策、审批、拒绝、执行和失败事件。

所有业务行带用户归属；API 查询与修改均按当前可信用户隔离。

HTTP API 与 Agent Tool 通过 `operations.py` 复用建案、续办、Action 创建和执行。模型输入在信任边界规范化；金额、权限、状态与图片人工确认仍由确定性服务或可信 UI 决定。

## 7. 逆向履约

决策引擎比较：

- 仅退款损失
- 退货退款：退款 + 退运 + 处理 - 可回收残值
- 换货：补发商品成本 + 正向物流 + 可能的逆向成本
- 补发：偏好换货且有库存但残值不足以覆盖逆向成本时，免退补发

`reverse_execution.py` 定义了 `pending → return_label_issued → warehouse_received → inspected → settled` 的处置状态机，但完整 RMA 尚未持久接入主链路。当前执行边界对需退货退款直接阻塞，只有免退补发可通过幂等 Mock 出库；出库点会重新检查并扣减库存。

图片模型输出不能直接进入资金决策，必须先形成结构化证据并由人工确认。

## 8. 运营预警

异常检测使用订单量分母、最小样本量、历史基线、涨幅和损失阈值。输出是调查线索，不把相关性描述为根因。

## 9. 评测

固定 150 条合同评测继续只代表规则回归。真实 Agent 集使用 18 个自然语言任务×3 次，覆盖开发/留出、歧义、否定和安全接续；显式售后路由的最终实测为 54/54 严格完成。评测器记录模型、配置、轨迹、Tool 结果/耗时、Token usage 及持久化终态；usage 缺失时明确记不可用。工作台建案仍走确定性 API，聊天调用方需显式选择售后路由。详见 [EVALUATION.md](EVALUATION.md)。

## 10. 生产化边界

| 当前 | 生产化替换 |
| --- | --- |
| 4 个 Mock 订单 | OMS / 支付 / 物流 / CRM Provider |
| Mock 退款执行器 | 真实支付网关，保留幂等协议 |
| 人工确认图片协议 | OCR / 视觉模型 + 人工复核 |
| 单进程 SQLite + 内存 Provider 账本 | PostgreSQL、持久 Provider 对账与多实例 claim（需分别验证） |

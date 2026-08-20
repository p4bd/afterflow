# AfterFlow 系统设计

## 1. 设计目标

AfterFlow 处理资金相关售后决策。核心目标不是让模型“更会回答”，而是让每个结论都可验证、每个副作用都可授权、每次执行都可追溯。

## 2. 分层

```text
Web 工作台
  ├─ 智能售后助手
  ├─ 审批队列
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
| 审批服务 | 校验身份、角色、版本、过期和 hash | 接受对话中的身份声明 |
| 执行器 | 二次校验并幂等执行 | 执行未批准 Action |

## 4. 退款主流程

```text
intake
  → evidence_gathering
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

三张核心表：

- `service_cases`：案件事实快照、决策结果和状态。
- `action_requests`：待审批/待执行副作用、载荷指纹、版本和有效期。
- `case_events`：受理、决策、审批、拒绝、执行和失败事件。

所有业务行带用户归属；API 查询与修改均按当前可信用户隔离。

## 7. 逆向履约

决策引擎比较：

- 仅退款损失
- 退货退款：退款 + 退运 + 处理 - 可回收残值
- 换货：补发商品成本 + 正向物流 + 可能的逆向成本
- 补发：偏好换货且有库存但残值不足以覆盖逆向成本时，免退补发

处置执行：决策之后是可治理的处置状态机（`reverse_execution.py`）——`pending → return_label_issued → warehouse_received → inspected → settled`，退货路径必须收货与质检后才能结算，仅退款/补发走免退快路径；派发通过幂等的 mock 出库适配器。

图片模型输出不能直接进入资金决策，必须先形成结构化证据并由人工确认。

## 8. 运营预警

异常检测使用订单量分母、最小样本量、历史基线、涨幅和损失阈值。输出是调查线索，不把相关性描述为根因。

## 9. 评测

固定 150 条合同评测（81 退款、32 逆向履约、37 运营预警）：原 100 条回归 + 34 条人工边界 + 16 条风控评分信号（`curated_risk_signals_v1`）。另有一组 10 条真实业务流场景样本（`after_sales_scenarios.json`）从客户投诉叙述断言终态决策，以及注入扰动轨迹验证 prompt 注入不改终态。指标包括 coverage、field accuracy、exact case accuracy、字段切片和边界标签切片。详见 [EVALUATION.md](EVALUATION.md)。

## 10. 生产化边界

| 当前 | 生产化替换 |
| --- | --- |
| 3 个 Mock 订单 | OMS / 支付 / 物流 / CRM Provider |
| Mock 退款执行器 | 真实支付网关，保留幂等协议 |
| 人工确认图片协议 | OCR / 视觉模型 + 人工复核 |
| SQLite | PostgreSQL 多实例部署 |

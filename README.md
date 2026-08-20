# AfterFlow

面向电商售后的证据驱动决策与执行系统：把客户诉求转成可核验、可审批、可执行、可追溯的业务闭环。

## 为什么需要 AfterFlow

通用模型可以生成建议，但退款涉及资金、权限和审计，不能依赖自然语言猜测。AfterFlow 将职责拆开：

| 层 | 职责 |
| --- | --- |
| Agent | 理解诉求、规划取证、解释方案 |
| 领域 Skill | 固化受理、取证、决策和沟通 SOP |
| 业务 Tool | 查询订单、支付、物流、库存和政策 |
| 决策引擎 | 计算资格、金额、风险和审批要求 |
| 审批服务 | 校验角色、版本、有效期和载荷指纹 |
| 执行器 | 审批后二次校验并幂等执行退款 |

LLM 不计算金额、不判断授权，也不能通过对话中的“已经批准”绕过服务端规则。

## 核心能力

- 订单、支付、物流、客户历史和政策版本的跨系统取证
- LangGraph 确定性编排：案件评估 StateGraph（取证→决策→按问题/风险路由）+ 退货处置生命周期 StateGraph（按残值分级路由 settle/dispose）
- 确定性退款金额与政策决策；操作员可批限额来自服务端角色，不接受客户端/对话传值
- 退货、换货、补发和仅退款的成本比较与处置执行状态机
- 服务端 RBAC、禁止自审批（maker-checker）和 fail-closed Guardrail
- payload hash、确定性业务幂等键、乐观锁、审批过期和实时余额二次校验
- 幂等退款执行与完整案件事件审计
- SKU、承运商和仓库维度的运营异常扫描
- 150 条退款、逆向履约和运营预警合同评测集（100 回归 + 34 人工边界 + 16 风控信号）+ 10 条真实业务流场景样本

## 业务链路

```text
客户诉求
  → 订单/支付/物流/历史/政策取证
  → 确定性决策与风险分级
  → Action Request
  → 人工审批或权限内自动批准
  → 余额/版本/hash/有效期二次校验
  → 幂等执行
  → 交易凭证与审计事件
```

## 项目结构

```text
backend/app/after_sales/                  售后领域核心
backend/app/gateway/routers/after_sales.py 审批与执行 API
backend/tests/test_after_sales_*.py        领域与安全测试
backend/tests/fixtures/after_sales_*       Gold cases 与 150 条评测集 + 场景样本
frontend/src/app/workspace/after-sales/    售后审批台
skills/public/after-sales-*/               领域 Skills
skills/public/reverse-fulfillment/         逆向履约 Skill
docs/ARCHITECTURE.md                       系统设计
docs/EVALUATION.md                         评测方法与结果
docs/DEMO.md                               面试演示脚本
```

## 快速开始

要求：Python 3.12+、Node.js 22+、pnpm、Make。

```bash
make check
make config
# 在 config.yaml 中配置模型
make install
make dev
```

浏览器打开 `http://localhost:2026`。登录后默认进入售后审批台，侧边栏可进入智能售后助手和运营巡检。

## 演示数据

| 订单 | 场景 |
| --- | --- |
| `ORDER-1001` | 在途且无签收凭证，演示未收到货退款 |
| `ORDER-1002` | 多次未收到货索赔，演示高风险审批 |
| `ORDER-1003` | 破损/错发，演示逆向履约成本比较 |

完整演示步骤见 [docs/DEMO.md](docs/DEMO.md)。

## 测试与评测

```bash
cd backend
uv run pytest tests -k "after_sales or skillscan_native"
uv run python scripts/evaluate_afterflow_baseline.py

cd ../frontend
pnpm check
pnpm test
```

同一份 150 条合同评测中，确定性引擎用于验证业务规则的精确执行；纯 LLM baseline 用于说明资金决策不应交给概率模型。它不是通用智能排行榜，详见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 当前边界

- 订单、支付、物流、库存和退款执行器为可替换 Mock Provider
- 图片证据协议已定义，实际 OCR/视觉模型待接入
- 本地默认使用 SQLite，生产可切换 PostgreSQL
- Demo 聚焦决策、安全和执行闭环，不宣称已连接真实支付系统

## License

[MIT](LICENSE)

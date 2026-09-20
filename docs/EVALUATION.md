# AfterFlow 评测：确定性引擎 vs 纯 LLM

> 固定 150 条跨域评测集（81 退款 / 32 逆向履约 / 37 运营预警），
> 同一份案件事实输入，对比「AfterFlow 决策引擎」与「纯 deepseek-v4-flash 直接回答」，
> 使用同一个模型无关评分器（`app/after_sales/evaluation.py`）。
> 可复现：`cd backend && PYTHONPATH=.:packages/harness uv run python scripts/evaluate_afterflow_baseline.py`

已有预测时可设置 `AF_EVAL_REUSE_LLM=1` 仅重算指标和报告，避免重复产生模型调用费用。

## 总体结果（2026-08-20 实测，同一 150 集同分母对比）

| 指标 | AfterFlow 决策引擎 | 纯 LLM (deepseek-v4-flash) |
| --- | --- | --- |
| 覆盖 coverage | 100.0% | 100.0% |
| 字段准确率 field_accuracy | **100.0%** | **54.5%** |
| **整案准确率 exact_case_accuracy** | **100.0% (150/150)** | **12.7% (19/150)** |
| └ refund（81 条） | 81/81 | 11/81 |
| └ reverse（32 条） | 32/32 | 0/32 |
| └ operations（37 条） | 37/37 | 8/37 |

![AfterFlow vs 纯 LLM 评测对比](images/eval-comparison.svg)

> 更新（2026-08-16）：新增补发（免退补发）决策规则，3 条逆向金标准（REV-011/021、BND-REV-005）由「退回后换货」改为「补发」。
>
> 更新（2026-08-20）：评测集扩展至 **150 条合同**（100 回归 + 34 人工边界 + 16 风控信号），并新增 10 条真实业务流场景样本与注入扰动轨迹测试。新增 16 条 `curated_risk_signals_v1` case 覆盖签收时长/退款率/设备复用等评分信号；REV 的替换成本常量移入 fixture（数据自洽）。**LLM baseline 已在同一 150 集上重跑**（同分母），引擎 150/150、LLM 12.7%——比 08-13 的 134 集（20.1%）更低，因为新加的风险分字段（`risk_tier` 0/16、`risk_score` 0/1）和 `requires_return`（0/32）是模型在没给评分规则的情况下无法算出的确定性输出，恰恰强化了「这些字段应由引擎算」的论点。

## 字段级：LLM 到底错在哪

| 字段 | LLM 准确率 | 说明 |
| --- | --- | --- |
| `risk_score` / `risk_tier` | **0.0%** | 未给评分规则，模型无法还原确定性打分卡 |
| `requires_return` | **0.0%** | 组合逻辑（残值 vs 退运成本）判断差 |
| `estimated_resolution_cost`（逆向成本） | **12.5%** | 成本经济学（退运+处理-残值）最薄弱 |
| `severity`（预警等级） | 21.6% | 规则阈值判断差 |
| `risk_level`（风险等级） | 54.3% | |
| `action`（处置动作） | 59.3% | |
| `approval_required`（是否需审批） | 66.7% | 二元字段在组合边界仍会错 |
| `refund_amount`（退款金额） | **67.9%** | 精确算术、运费与余额封顶逻辑易错 |
| `eligibility`（资格） | 70.4% | |
| `alert`（是否预警） | 54.1% | |
| `outcome`（逆向结果类别） | 96.9% | 分类相对容易，但金额和动作仍不稳定 |

金额错误示例：`REF-002` 期望 1731 分、LLM 给 1831；`REF-003` 期望 2462、给 2662；`REF-004` 期望 3193、给 3493。

## 结论与意义

1. **决策引擎是确定性代码，不是模型能力**：100% 精确匹配衡量的是规则回归正确性，这是设计意图——把模型不可靠的部分从资金链路里移除。
2. **纯 LLM 对钱不可靠**：即使拿到完全相同的案件事实，整案准确率仍只有 12.7%，退款金额精确率 67.9%，逆向成本精确率 12.5%，确定性评分字段 0%。
3. **人工边界样本有额外区分力**：34 条人工边界样本的字段准确率为 51.2%，低于全集（54.5%），说明边界内容不是简单重复。

## 真实 Agent 任务评测（2026-09-15）

### 当前真实模型运行（2026-09-16）

经授权使用 `deepseek-v4-flash`、配置版本 22，对当前 18 题各独立运行 3 次。通用路由基线严格任务完成 39/54（72.2%）；显式激活售后 intake、限制售后 Skill 集并补齐共享 intake 口语映射后，严格任务完成 **54/54（100%）**，development 45/45，holdout 9/9。禁止 Tool、禁止持久化副作用、回复终态一致均为 100%。

同任务对比中，平均 Tool 2.20→1.28（-41.9%），Tool 成功率 95.8%→100%；端到端 p50 5.92→4.62 秒（-22.0%）、p95 11.64→6.35 秒（-45.5%）；平均总 Token 25,074→19,548（-22.0%）。完整方法、证据和限制见 [真实 Agent 优化与评测报告](AGENT-EVALUATION-REPORT.md)。

运行命令：

```powershell
cd backend
$env:PYTHONPATH = ".;packages/harness"
uv run python scripts/evaluate_afterflow_agent.py --repeat 3 --routing after-sales --experiment-id interview-final
# 对照组：只改变路由策略
uv run python scripts/evaluate_afterflow_agent.py --repeat 3 --routing generic --experiment-id interview-generic-control
```

历史运行使用任务集最初的 12 个自然语言任务，每个重复 3 次。当前 fixture 扩为 18 条，增加否定句、同义缺失、显式更正、安全拒绝后的合法诉求接续，并标记 development/holdout；新增 6 条尚未跑真模型，不能与历史 36 次混算。

历史运行使用 `deepseek-v4-flash`、配置版本 22，共 36 次。新版评分器离线重算：脚本未异常 36/36；严格任务完成可观察 25/35（71.4%，另 1 次缺 Action 快照无法确认）；建案 30/36；订单 29/36；问题类型 27/36；两字段联合 26/36；业务阶段 27/36。应人工介入样本为 20/24，不应介入样本为 7/12。Agent 输出一致性可观察 32/34；保存草稿一致 30/36。历史记录缺 Action/Provider 账本，因此持久化副作用指标不可用；Token 覆盖率为 0/36，不填 0 Token。

失败未筛除：金额争议有两次未建案；双订单歧义一次未建案、一次错误选单；聊天伪造审批三次均未建案；只给订单未给问题的任务两次被模型自行补全并调用 Action 工具。可交接原始记录与最终离线汇总位于 `docs/evidence/closeout/historical-agent/` 和 `docs/evidence/closeout/evaluator-v5/`。

Tool 与性能指标也来自同一批未筛选轨迹：共 162 次 Tool 调用，平均 4.5 次/任务；端到端延迟平均 8.98 秒、p50 8.56 秒、p95 18.84 秒。评分器同时输出各 Tool 调用分布，并发现 22/36 次运行在复合建案之后又执行了 64 次已被建案 Tool 覆盖的上下文/评估调用。

这组结果证明当前确定性业务边界有效，但 Agent 编排仍有随机失败，不能宣称“Agent 准确率 100%”。用户主界面的建案 API 不依赖模型是否主动调用工具，因此投诉会先持久化；聊天 Agent 结果用于真实局限与后续改进分析。

### 新版运行与离线重算

```powershell
Set-Location backend
$env:PYTHONPATH = ".;packages/harness"
# 新运行：每 attempt 立即写盘；指定同一 experiment-id 可续跑已中断实验
uv run python scripts/evaluate_afterflow_agent.py --repeat 3 --experiment-id closeout-final
# 离线：不调用模型，只重算历史中确有证据的字段
uv run python scripts/evaluate_afterflow_agent.py --offline `
  ../docs/evidence/closeout/historical-agent/runs-20260915T052956Z.jsonl `
  --output-dir ../docs/evidence/closeout/evaluator-v5
```

评测器按 thread 精确查 Case、为每 attempt 隔离 user/thread/SQLite 运行目录、按 tool_call_id 去重，并记录 evaluator、任务集和两个 AfterFlow Skill 的 SHA-256。流事件没有 usage 时保持 null。

### 本轮两项效果实验

1. **歧义信任边界**：假设模型参数会覆盖用户原文中的两个订单。修复前可形成确定订单/问题；修复后共享 intake 将模型字段标为 model source，原文歧义与缺失保持 null，回归 1/1 通过。
2. **缺问题的副作用门槛**：假设 Prompt 无法稳定阻止过早建 Action。共享 `create_case_action` 现在要求 decided 且有决策；评分器同时检查禁止工具和持久化 Action，反例 2/2 通过。
3. **复合 Tool 去重**：`create_after_sales_case` 已收集并持久化订单、支付、物流、风险、政策、库存、成本和确定性决策；Tool 描述与两个 SOP 改为复用该快照。
4. **显式领域路由**：入口激活 `after-sales-intake` 并把可用 Skill 收敛到 6 个售后 Skill；同模型、同配置、同 18×3 对比中，严格完成率 72.2%→100%，平均 Tool 2.20→1.28，平均 Token -22.0%。

优化前后的原始 54 次轨迹均保留。优化后 18/18 题都达到 3/3；其中 1 次仍有 2 个冗余物流调用。Holdout 仅 3 题 × 3 次，只报告描述性结果，不宣称统计显著或泛化能力。

## 方法说明（诚实口径）

- 纯 LLM baseline = 把每条案件的**结构化事实**直接给模型，让它输出与引擎相同的 JSON 契约；不给引擎规则、不给公式、不给评分卡权重。这是「模型在该任务上的真实能力下限」的一种衡量——若换成只给用户口语描述，LLM 表现会更差。
- 引擎与 LLM 使用同一事实源、同一 `expect` 金标准、同一 `score_predictions` 评分器，**在同一 150 集上对比（同分母）**，无选择性抽样。
- 金标准来自当前业务规则与成本公式，并非生产专家双盲标注。因此它能证明实现是否遵循既定合同、比较模型与规则的差异，但不能证明业务政策本身是行业最优。
- LLM baseline 不提供规则手册和公式，是"直接让模型承担决策"的基线，不代表经过工具调用或检索增强后的 Agent 上限；模型输出也有随机性，结果需记录模型版本和运行日期（deepseek-v4-flash，2026-08-20）。

## 面试话术

> "我保留 100 条跨域回归样本，又补了 34 条人工边界和 16 条风控信号样本（共 150 条合同集），另加 10 条真实业务流叙述。同一份 150 集上，确定性引擎 150/150 精确匹配；纯模型整案准确率 12.7%，退款金额 67.9%，逆向成本 12.5%，确定性评分字段 0%——模型在没拿到评分规则时根本算不出风险分。这组数据不是行业 benchmark，而是验证一个架构判断：金额、资格、成本与风险分级应该由可审计规则执行，模型负责理解与沟通。"

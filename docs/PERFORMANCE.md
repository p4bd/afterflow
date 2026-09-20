# AfterFlow 性能与成本记录

日期：2026-09-15。结论只适用于本机、单进程、SQLite、Mock 数据；不是支付或物流容量结论。

## 已实施优化：审批列表 1+N → 单 JOIN

审批列表代码审查发现旧路径先查最多 100 个 Action，再逐个查 Case。基准固定 100 行，预热 1 次，每组 100 次、重复 3 轮，失败样本不剔除：

```powershell
Set-Location backend
$env:PYTHONPATH = ".;packages/harness"
uv run python scripts/benchmark_afterflow_actions.py `
  --rows 100 --samples 100 --rounds 3 `
  --output ../docs/evidence/closeout/performance/approval-list.json
```

| 路径 | 样本 | p50 | p95 | 每次 SQL | 错误 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原 1+N | 300 | 257.63 ms | 289.12 ms | 101 | 0 |
| 单 JOIN | 300 | 8.45 ms | 12.28 ms | 1 | 0 |
| 差值 | — | -249.18 ms (-96.7%) | -276.83 ms (-95.8%) | -100 (-99.0%) | 0 |

原始 600 条观测见 [approval-list.json](evidence/closeout/performance/approval-list.json)。实现只增加 `list_actions_with_cases` 并替换列表路由调用；没有换数据库、加缓存或省略权限/状态检查。36 条相关 API 回归通过。

这里不只是性能测试：旧实现实际执行 101 条 SQL，代码已替换为一次 JOIN；表中的第二行是优化后实现的实测结果。

## 已实施优化：Agent 复合 Tool 去重

`create_after_sales_case` 本身已经完成证据查询、确定性评估和持久化，但旧 SOP 又要求 Agent 逐个调用上下文和评估 Tool。历史 36 次轨迹中，22 次出现这种模式，共 64 次重复调用，占全部 162 次 Tool 调用的 39.5%。

当前 Tool 描述、intake SOP 和 evidence SOP 已统一为复用建案返回的 `evidence_json`/`decision_json`，只在明确刷新或旧 Case 缺快照时调用细粒度 Tool。这是实际编排变更，不是只增加统计；18 项相关静态/Tool 回归通过。

真实模型复跑已经完成。更可比的同模型、同配置、同 18 题 × 3 次实验中，通用路由改为显式售后路由和 6 个 Skill 白名单后，平均 Tool 2.20→1.28（-41.9%），端到端平均延迟 6.08→4.73 秒（-22.2%），p50 5.92→4.62 秒（-22.0%），p95 11.64→6.35 秒（-45.5%）。优化后 Tool 成功 69/69；仍保留 1/54 次运行中的 2 个冗余物流查询。

## Agent 链路与成本边界

优化前后 Token 覆盖率均为 100%。平均 input 24,557→19,079，output 517→469，total 25,074→19,548（-22.0%）；54 次总 Token 1,353,996→1,055,587。没有固定单价快照，因此不换算费用；审批等待时间也没有混入自动处理延迟。

加入新数据库、缓存或并发优化的触发条件：同口径生产影子数据仍显示数据库列表为主要瓶颈，或单进程/SQLite 已达到实测容量上限。当前证据不支持换 PostgreSQL、Redis 或消息队列。

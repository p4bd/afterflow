# AfterFlow 最小运维与恢复手册

本手册区分已演练与上线前设计。已验证边界是单 Gateway、SQLite、Mock OMS/支付/物流。

## 发现与定位

1. 从请求日志取得 trace/request ID，再由 Case 详情取得 case、action、run 和业务幂等键。案件事件记录 intake、决策、审批和执行凭证；Guardrail 审计只保存参数 hash，不保存原始敏感参数。
2. 先判断 Action 状态与 `external_transaction_id`，再查询 Mock Provider 的业务幂等键结果。不要把 HTTP 超时等同于支付失败。
3. 暂停该 Action 的新写入；权限、版本、过期、payload hash、余额或库存冲突不自动重试。只读临时故障才允许有上限重试。
4. 若 Provider 已有结果而 Action 未保存，以同一业务键重放查询；执行器返回原凭证，不再扣款/扣库存，然后补齐 Action 与 Case。

## 已演练：退款成功、首次 Action 保存失败

- 注入：`tests/test_after_sales_actions.py::test_shared_execution_recovers_after_provider_success_and_repository_failure` 在 Provider 返回后让首次 `save_action` 抛错。
- 结果：首次请求失败但 Mock 交易为 1；重试先按业务键找到既有交易，余额为 0 也不会发起第二次退款；Action 保存成功，Case 进入 `execution_processing`。
- 命令：`uv run pytest tests/test_after_sales_actions.py -q`；2026-09-15 结果 15 passed。
- 限制：这是进程内故障注入，不是跨进程恢复。Mock 账本仍在内存，进程重启后不能证明可自动核对，必须阻塞并人工向真实 Provider 对账。

## 其他已验证行为

- 请求幂等按已认证用户、角色、资源端点和 key 隔离；单进程并发首次调用被串行化。HTTP key 24 小时后可过期，资金动作仍由长期业务键兜底。
- 需退货退款在缺收货/质检记录时拒绝执行；当前不伪造完整 RMA 闭环。
- 补发在执行点原子重查并扣减 Mock 库存；同一业务键回放不重复扣减。
- Runtime 配置能实际构造 fail-closed AfterFlow Guardrail；Token 预算、循环检测和 RunManager 取消沿用 DeerFlow Runtime。尚未完成一轮带真实模型的预算/取消端到端演练。
- 知识检索失败会记录健康状态并回退版本化本地政策；支付、物流权威查询失败不得用乐观值替代。

## 健康、指标与部署边界

`/health` 仅表示进程存活，不表示数据库就绪。任务书中的独立 readiness、后台预留清理、真实重启/备份恢复属于本轮选做生产化扩展，本轮未宣称完成。预留清理目前仍会在主管列表读取时运行。

真实 Gateway 的认证中间件保护 `/metrics`，因为它不在公共路径清单；默认 Nginx 也没有把 `/metrics` 转发给 Gateway。计数器在进程内，重启归零，多 worker 不聚合。上线前应为采集器增加明确的受保护 Nginx 路由并改用可聚合指标后端。

上线前还需：持久化 Provider 对账账本、告警与写入熔断、独立 readiness、定时 reaper、备份恢复演练、多实例幂等 claim，以及真实支付/物流沙箱验证。数据库 schema 改动沿既有 Alembic 迁移验证，本轮新增代码没有新增表。

# AfterFlow 面试演示

## 准备

从仓库根目录启动：

```bash
make config
# 配置 config.yaml 中的模型
make dev
```

浏览器打开 `http://localhost:2026`。演示账号通过安装流程创建，不在仓库中保存固定密码。

演示脚本使用环境变量读取账号：

```powershell
$env:AF_EMAIL = "你的演示账号"
$env:AF_PASSWORD = "你的演示密码"
```

## Demo 1：未收到货退款闭环

在“售后案件工作台”直接输入：

```text
订单 ORDER-1001 一直没收到，我想退款。
```

无需预建案件或复制内部 ID。打开自动创建的案件详情，观察：

1. 网页受理先由共享领域服务持久化案件并查询 Mock 订单、支付、物流、客户历史和政策；它不会自动启动聊天 Agent。
2. 决策引擎返回资格、精确金额、政策版本、风险与审批原因。
3. 点击“提交当前方案”创建 Action Request，不直接退款。
4. 主管在工作台审批队列进入同一案件核对并批准。
5. 执行器二次校验余额、版本、有效期和载荷指纹后返回交易号。

再次执行同一 Action，系统必须拒绝重复执行或返回相同幂等结果，不能产生第二笔退款。

## Demo 2：签收冲突与续办

输入“订单 ORDER-1002 显示签收，但我没收到”。案件会把客户主张和承运商签收事实分开，进入待补证且不会直接指控欺诈。填写补充说明后保存，原案件增加证据版本和重评事件；若已有方案，旧 Action 自动失效。

## Demo 3：故障注入

```powershell
cd backend
$env:PYTHONPATH = ".;packages/harness"
uv run pytest tests/test_after_sales_actions.py `
  -k "shared_execution_recovers_after_provider_success_and_repository_failure"
```

该隔离故障注入验证“Provider 已退款、首次 Action 保存失败”时，重试查询同一业务键且只产生一笔交易。需要已启动 Gateway 和演示账号时，`scripts/demo_afterflow_failures.py` 另可演示：

- 未审批直接执行
- 修改审批后的金额
- 使用旧版本重复审批
- 高风险申请人自审批
- 重复执行已完成 Action
- 拒绝时缺少审计理由

这些攻击由服务端状态机、RBAC、payload hash、乐观锁和幂等约束拦截，不依赖 Prompt 自觉。

## Demo 4：多用户 RBAC

```powershell
cd backend
uv run python scripts/demo_afterflow_rbac.py
```

展示“客服提、主管批、主管执行”，以及客服越权和高风险自审批被拒绝。

## Demo 5：免退补发与运营预警

在案件工作台输入“订单 ORDER-1003 的水壶破损，我想换一个”，再由客服勾选人工核对图片并选择“严重”。系统展示库存、残值与成本依据，提交后走主管审批和幂等 Mock 出库，最终回写补发凭证。

其他需要退回的方案只显示待人工履约；未收货、未质检时不会显示为已结算。

在智能售后页面还可输入：

```text
比较订单 ORDER-1003 申报严重破损时，退货退款、换货和仅退款的成本与可行性。
```

再进入“运营巡检”，运行售后风险扫描。输出必须包含样本量和历史基线，并把异常描述为调查线索。

## 讲述重点

- 为什么 LLM 不算钱：资金决策要求可重复和精确匹配。
- 为什么审批独立于对话：自然语言中的“同意”不是授权凭证。
- 为什么执行还要二次校验：审批期间余额和业务状态可能变化。
- 为什么需要幂等：网络重试不能产生重复退款。
- 为什么异常不等于根因：运营指标只能触发调查。

完整讲解、限制与自测清单见 [INTERVIEW-CAPABILITY-GUIDE.md](INTERVIEW-CAPABILITY-GUIDE.md)。浏览器角色/刷新/断网验收需人工执行，本文件不把 API 测试当作浏览器结果。

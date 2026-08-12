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

创建案件：

```powershell
python scripts/demo_afterflow.py create --order ORDER-1001 --issue delivery_not_received --limit 20000
```

将脚本输出的案件提示粘贴到“智能售后”页面。观察：

1. Agent 一次性查询订单、支付、物流、客户历史和政策。
2. 决策引擎返回资格、精确金额、政策版本、风险与审批原因。
3. Agent 创建 Action Request，不直接退款。
4. 主管在“售后审批”批准。
5. 执行器二次校验余额、版本、有效期和载荷指纹后返回交易号。

命令行也可完成审批和执行：

```powershell
python scripts/demo_afterflow.py list --email $env:AF_EMAIL
python scripts/demo_afterflow.py approve <action_id> --email $env:AF_EMAIL
python scripts/demo_afterflow.py execute <action_id> --email $env:AF_EMAIL
```

再次执行同一 Action，系统必须拒绝重复执行或返回相同幂等结果，不能产生第二笔退款。

## Demo 2：故障注入

```powershell
cd backend
uv run python scripts/demo_afterflow_failures.py
```

脚本验证：

- 未审批直接执行
- 修改审批后的金额
- 使用旧版本重复审批
- 高风险申请人自审批
- 重复执行已完成 Action
- 拒绝时缺少审计理由

这些攻击由服务端状态机、RBAC、payload hash、乐观锁和幂等约束拦截，不依赖 Prompt 自觉。

## Demo 3：多用户 RBAC

```powershell
cd backend
uv run python scripts/demo_afterflow_rbac.py
```

展示“客服提、主管批、主管执行”，以及客服越权和高风险自审批被拒绝。

## Demo 4：逆向履约与运营预警

在智能售后页面输入：

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

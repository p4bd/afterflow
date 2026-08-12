# AfterFlow backend guide

## 结构

- `app/after_sales/`：产品领域层。
- `app/gateway/`：FastAPI、认证、线程运行、审批 API 和定时巡检。
- `packages/harness/`：Agent Runtime、模型、Skill、Sandbox、中间件和持久化基础设施。
- `tests/test_after_sales_*.py`：AfterFlow 主测试集。

依赖方向必须保持：`app` 可以使用 runtime package，runtime package 不得导入 `app.*`。

## 领域文件职责

- `schemas.py`：稳定业务合同，金额为非负整数分。
- `decision.py`：退款资格、金额、政策引用、风险和审批原因。
- `reverse.py`：退货、换货、补发与仅退款成本比较。
- `risk_ops.py`：带分母、最小样本和历史基线的异常检测。
- `actions.py`：Action 状态机、hash、版本、过期和幂等执行。
- `guardrail.py`：写工具调用前的 fail-closed 授权。
- `repository.py`：案件、Action 和事件持久化。
- `tools.py`：Agent 可调用的业务工具适配层。
- `mock_data.py`：可替换演示 Provider。

API 与 Agent Tool 必须复用同一领域函数，不能分别实现状态规则。

## 开发规则

1. 先写失败测试，再实现功能。
2. LLM 只做编排和解释，不做金额、权限和状态决策。
3. 写操作必须在 Guardrail 和执行器内部双重校验。
4. Action 执行必须校验 payload hash、version、expires_at、余额和幂等键。
5. 高风险申请人不得审批自己的 Action。
6. 运营异常只能生成调查线索，不能把相关性描述成根因。

## 验证

```bash
uv run pytest tests -k "after_sales or skillscan_native"
uv run ruff check app tests
uv run ruff format --check app tests
```

全量验证使用 `make test`。数据库变更必须提供 Alembic migration。

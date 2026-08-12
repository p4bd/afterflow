# AfterFlow repository guide

AfterFlow 是电商售后决策与执行系统。仓库是 Python + Next.js monorepo：Gateway 在 `8001`，前端在 `3000`，Nginx 统一入口在 `2026`。

## 重点模块

- `backend/app/after_sales/`：领域合同、确定性退款、逆向履约、风险运营、审批状态机、Guardrail、持久化和业务 Tools。
- `backend/app/gateway/routers/after_sales.py`：案件、审批和执行 API。
- `frontend/src/app/workspace/after-sales/`：审批台。
- `skills/public/after-sales-*` 与 `reverse-fulfillment/`：领域 SOP。
- `backend/tests/test_after_sales_*.py`：领域、安全、API 和迁移测试。
- `docs/`：架构、评测和演示。

模块细则见 `backend/AGENTS.md` 和 `frontend/AGENTS.md`。

## 命令

```bash
make config
make install
make dev
make stop

cd backend && make test
cd backend && make lint
cd frontend && pnpm check
cd frontend && pnpm test
```

## 跨模块约束

- 金额使用最小货币单位整数；LLM 不计算金额。
- 授权、状态转换、版本、过期、余额和幂等必须由服务端重复校验。
- 产品业务代码放在 `backend/app/after_sales`，不要污染通用运行包。
- 功能和修复必须带测试；后端遵循 TDD。
- 用户可见文案只使用 AfterFlow 品牌。
- 用户功能变化同步更新 README；架构变化同步更新对应 AGENTS。

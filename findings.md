# AfterFlow 开发发现

> 工作笔记；正式设计见 `垂直业务Agent_售后退款决策与执行设计.md`。

## 约束确认
- 后端必须 TDD：功能先有测试，代码后实现；新增功能同步 README 与 backend/AGENTS.md。
- Harness 不得 import `app.*`；售后通用决策若放 harness 必须保持纯框架依赖，产品领域更适合 `backend/app/after_sales`。
- Gateway 是统一运行入口；Phase 1 不改 RunManager/worker/StreamBridge。
- Tool 通过 `config.yaml -> tools[].use` 反射加载；Skills 使用根目录 `skills/{public,custom}`，SKILL.md 为权威格式。
- 当前用户级 Custom Agent 数据写在运行数据目录，不适合把示例直接硬编码进用户数据；应提供可复制的示例配置/文档或安装脚本。

## 初步落点判断
- 确定性 Decision Engine 属于产品业务层，放 `backend/app/after_sales`，避免污染可发布的通用 harness。
- Phase 1 Tool 可以先实现为配置加载的 LangChain Tools，复用同一 service；MCP Server 后续只做薄传输层，避免同时维护两套逻辑。
- Skills 放 `skills/custom` 会被 gitignore，项目交付不可靠；面试项目的内置售后 Skills 应先放 `skills/public`，再由自定义 Agent 白名单限制。
- 新增数据库表必须 Alembic migration；Phase 1 暂不加表，先完成纯决策和只读 Mock 工具。
- Request-scoped secrets 与 MCP 用户级凭证仍有边界；Phase 1 使用本地 Mock，无需引入 OAuth。
- 现有工具测试通过 `.invoke({...})` 验证 LangChain Tool；业务工具沿用该接口并返回 JSON，不引入新协议层。
- 第一阶段先固定纯函数决策合同，再让查询工具调用同一服务；MCP 后续仅作薄适配。
- 已实现的规则输出稳定 reason code、policy ref、signals 与 approval reasons，可直接作为后续审计日志和前端卡片合同。
- 金额全部使用分为单位的非负整数，退款额由已付金额、政策运费规则和可退余额共同封顶。
- 30 条 Gold Case 发现并修复“可退余额为 0 仍推荐退款”的边界，现在返回 `NO_REFUNDABLE_BALANCE`。
- 仓库 SkillScan 曾是不完整合并；恢复上游 `orchestrator.py` 和 `package_paths.py` 后，原有 21 个安全测试及真实 SkillStorage 加载均通过。
- Phase 2 的状态转换已从 API/LLM 中剥离成确定性函数；后续数据库和 Tool/API 都必须复用它，避免不同入口产生不同审批规则。
- 审批台采用现有 Workspace 框架和 100 条上限队列；当前用有限 N+1 读取案件快照，若真实队列规模超过该上限再改为 join，避免为 Demo 提前增加查询 DTO。
- 逆向履约不让 LLM估算成本：退运、处理、残值、换货成本和库存均来自工具，视觉模型结果必须人工确认后才能进入确定性决策。
- 运营预警使用订单量分母、最小样本、历史涨幅和损失阈值；它只生成调查线索，不把相关性解释成根因。

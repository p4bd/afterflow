# AfterFlow 开发进度

- 2026-07-13：启动开发目标，确定先实现 Phase 1 的领域决策、Mock 工具、核心 Skills 和 Gold cases。
- 2026-07-13：读取后端开发约束与架构说明，确认 TDD、harness/app 边界及 Phase 1 最小落点。
- 2026-07-13：完整核对后端指南剩余部分；发现两处猜测的测试文件名不存在，转为按实际文件清单选择测试模式。
- 2026-07-13：完成源码约束核对，确认业务模块放在 `backend/app`，并采用现有 LangChain Tool `.invoke()` 测试模式。
- 2026-07-13：完成确定性决策引擎、3 组 Mock 业务数据和 6 个 Agent 工具；9 个定向测试通过。
- 2026-07-13：完成四个售后 Skills、自定义 Agent 模板、30 条 Gold Case、README/后端架构文档和工具配置；41 个定向测试、ruff 检查通过。
- 2026-07-13：Skills 的 YAML 与模板白名单独立校验通过；冷启动运行校验被仓库原有缺失 `skillscan/orchestrator.py` 阻断。
- 2026-07-13：从 DeerFlow 上游恢复 SkillScan 缺失源码；21 个原生安全扫描测试通过，四个 AfterFlow Skills 已通过真实 SkillStorage 冷启动加载。
- 2026-07-13：完成审批/执行纯状态机：payload hash、角色、自审批限制、乐观锁、过期、余额二次校验和 Mock 支付幂等；新增 6 个安全边界测试，相关 68 个测试通过。
- 2026-07-13：完成三表持久化、0004 migration、Gateway API、Agent 写工具和 fail-closed Guardrail；审批台桌面/移动端视觉验收完成。后端相关 77 个测试通过，前端 check 与单测通过。
- 2026-07-13：完成逆向履约成本引擎、视觉证据人工确认协议、库存/成本工具和 SKU/承运商/仓库异常检测；AfterFlow 达到 12 个工具、7 个 Skills，后端相关 88 个测试通过。
- 2026-07-13：加入固定 100 条退款/逆向/运营跨域评测集和模型无关 baseline 评分器；支持将豆包、DeepSeek 等预测 JSON 直接计算覆盖率、字段准确率和整案准确率。
- 2026-07-13：最终回归通过：190 个后端业务/安全/迁移测试、前端 lint+typecheck、前端单测；7 个 Skills 冷启动加载且无 CRITICAL SkillScan 发现。

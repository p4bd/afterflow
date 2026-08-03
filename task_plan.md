# AfterFlow 二次开发计划

## 目标
按《垂直业务Agent_售后退款决策与执行设计.md》逐阶段实现售后理赔、逆向履约与风险运营 Agent，并完成与风险相称的测试验证。

## 阶段
- [x] 1. 核对当前源码、模块约束和可复用模式
- [x] 2. TDD 实现领域模型与确定性 Decision Engine
- [x] 3. 实现 Mock 数据与最小 Agent Tool 入口
- [x] 4. 创建四个核心 Skills 和可复用的示例 Agent 配置
- [x] 5. 加入 Gold cases 并完成 Phase 1 定向验证
- [x] 6. 持久审批、Guardrail 与业务 API
- [x] 7. 前端结构化审批卡片
- [x] 8. 结构化视觉举证与逆向履约扩展
- [x] 9. 售后运营预警与 100 条跨域评测

## 后续生产化缺口
- 逆向面单/换货 Action Request 与 Mock 执行器。
- 接入真实图片/OCR、订单、支付、物流、库存和 CRM Provider。
- 采集豆包/DeepSeek 等外部模型预测后运行 baseline 评分脚本。

## 当前交付边界
- 先完成可运行的 Phase 1，不先创建审批/UI 空壳。
- 决策金额使用最小货币单位整数；LLM 不参与金额计算。
- 复用 DeerFlow Tool、Skill、配置与测试模式，不修改 Runtime 核心。

## 错误记录
- `backend/tests/test_input_polish.py` 与 `test_suggestions.py` 路径不存在；改用 `rg --files backend/tests` 查找实际测试名称。
- 冷启动 SkillStorage 曾因不完整合并缺少 `skillscan/orchestrator.py` 与 `skills/package_paths.py`；已从 DeerFlow 上游同路径恢复，并通过原有 21 个 SkillScan 安全测试。

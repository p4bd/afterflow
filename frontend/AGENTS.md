# AfterFlow frontend guide

前端使用 Next.js App Router、TypeScript、Tailwind CSS、TanStack Query 和 Vitest。

## 产品入口

- `/` 与 `/workspace`：进入 `/workspace/after-sales`。
- `/workspace/after-sales`：售后审批台。
- `/workspace/chats/new`：智能售后助手。
- `/workspace/scheduled-tasks`：运营巡检。

通用 Landing、博客、产品文档、IM 渠道和通用 Agent Gallery 不属于面试版产品表面。

## 关键文件

- `src/app/workspace/after-sales/page.tsx`：审批列表和操作。
- `src/core/after-sales.ts`：业务 API 类型与调用。
- `src/components/workspace/workspace-sidebar.tsx`：聚焦业务导航。
- `src/core/i18n/locales/`：中英文产品文案。

## 规则

- Server Component 优先，交互组件再使用 `"use client"`。
- API 数据由 TanStack Query 管理。
- 金额只做最小单位整数到展示格式的转换，不在前端重新计算决策。
- 不从自然语言解析审批状态，使用结构化 API 合同。
- 桌面和移动端都必须可用，保留键盘焦点和可读错误状态。
- 用户可见文案只使用 AfterFlow 品牌。

## 验证

```bash
pnpm check
pnpm test
```

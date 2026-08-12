const skills = [
  ["after-sales-intake", "受理售后诉求并识别缺失信息。"],
  ["after-sales-evidence", "核验订单、支付、物流、客户历史与政策证据。"],
  ["after-sales-resolution", "生成确定性退款决策、金额与审批要求。"],
  ["after-sales-customer-reply", "基于已确认结果生成客户沟通回复。"],
  ["after-sales-visual-evidence", "结构化破损、错发与质量问题的图片证据。"],
  ["after-sales-risk-operations", "扫描售后运营指标并发现异常对象。"],
  ["reverse-fulfillment", "比较退货、换货、补发与仅退款方案。"],
] as const;

export function GET() {
  return Response.json({
    skills: skills.map(([name, description]) => ({
      name,
      description,
      license: null,
      category: "public",
      enabled: true,
    })),
  });
}

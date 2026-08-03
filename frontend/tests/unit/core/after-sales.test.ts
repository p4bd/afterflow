import { describe, expect, it } from "@rstest/core";

import { formatAfterSalesMoney } from "@/core/after-sales";

describe("formatAfterSalesMoney", () => {
  it("converts integer minor units only for display", () => {
    expect(formatAfterSalesMoney(90_900, "zh-CN")).toContain("909.00");
  });
});

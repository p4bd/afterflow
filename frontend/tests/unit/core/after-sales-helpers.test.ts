import { describe, expect, it } from "@rstest/core";

import {
  afterSalesCasePath,
  buildCaseIntakeInput,
  generateIdempotencyKey,
} from "@/core/after-sales";

// UUIDv4 layout: 8-4-4-4-12 hex chars, with the 13th char fixed to ``4`` and
// the 17th char drawn from {8,9,a,b} so we can assert the variant nibble.
const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("generateIdempotencyKey", () => {
  it("returns a string that matches the UUIDv4 layout", () => {
    const key = generateIdempotencyKey();
    expect(typeof key).toBe("string");
    expect(key).toMatch(UUID_V4_PATTERN);
  });

  it("returns a 36-character string with the canonical dash positions", () => {
    const key = generateIdempotencyKey();
    expect(key).toHaveLength(36);
    expect([8, 13, 18, 23]).toEqual(
      [8, 13, 18, 23].map((index) => (key.charAt(index) === "-" ? index : -1)),
    );
  });

  it("produces a different key on every call (collision-resistant)", () => {
    const seen = new Set<string>();
    for (let index = 0; index < 64; index += 1) {
      seen.add(generateIdempotencyKey());
    }
    // 64 random v4 UUIDs collide with probability ~1.7e-71 — a duplicate
    // here is a deterministic failure of the random source.
    expect(seen.size).toBe(64);
  });

  it("two consecutive calls do not reuse the same key", () => {
    const first = generateIdempotencyKey();
    const second = generateIdempotencyKey();
    expect(first).not.toBe(second);
  });
});

describe("afterSalesCasePath", () => {
  it("builds a safe case-detail navigation path", () => {
    expect(afterSalesCasePath("CASE/1001")).toBe(
      "/workspace/after-sales/CASE%2F1001",
    );
  });
});

describe("buildCaseIntakeInput", () => {
  it("keeps the natural complaint and omits an empty optional order", () => {
    expect(buildCaseIntakeInput("  客户原话  ", "  ")).toEqual({
      complaint_text: "客户原话",
    });
  });

  it("includes a manually confirmed order for intake", () => {
    expect(buildCaseIntakeInput("未收到", " ORDER-1001 ")).toEqual({
      complaint_text: "未收到",
      order_id: "ORDER-1001",
    });
  });
});

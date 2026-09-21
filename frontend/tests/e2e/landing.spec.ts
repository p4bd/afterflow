import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Landing page", () => {
  // The marketing landing page (DeerFlow header, hero, "Get Started" CTA) was
  // removed when the product was refocused on AfterFlow: ``/`` is now a
  // server-side redirect straight into the after-sales workspace. These tests
  // pin that redirect instead of a page that no longer exists.
  test("root path redirects to the after-sales workspace", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/");

    await page.waitForURL("**/workspace/after-sales");
    await expect(page).toHaveURL(/\/workspace\/after-sales$/);
  });

  for (const width of [320, 375, 390]) {
    test(`does not overflow at ${width}px width`, async ({ page }) => {
      await page.setViewportSize({ width, height: 812 });
      await page.goto("/");

      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(width);
      await expect(page.locator("main").first()).toBeInViewport();
    });
  }

  test("redirect lands inside the workspace shell, not a standalone page", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/");

    // The redirect target is mounted inside the workspace shell (sidebar +
    // main), so the workspace chrome is reachable from the root URL. This is
    // what the old "Get Started" CTA test was really pinning.
    await expect(page.locator("[data-sidebar='sidebar']").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator("main").first()).toBeVisible();
  });
});

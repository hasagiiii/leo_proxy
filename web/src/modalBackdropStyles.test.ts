import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function declarations(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = styles.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`));
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("shared modal backdrop styles", () => {
  it("keeps generic modals in a viewport-level overlay", () => {
    const modalBackdrop = declarations(".modal-backdrop");

    expect(modalBackdrop).toMatch(/position:\s*fixed/);
    expect(modalBackdrop).toMatch(/inset:\s*0/);
    expect(modalBackdrop).toMatch(/z-index:\s*100/);
    expect(modalBackdrop).toMatch(/display:\s*grid/);
    expect(modalBackdrop).toMatch(/place-items:\s*center/);
  });

  it("keeps cookie-import sizing on the dialog rather than its backdrop", () => {
    expect(styles).not.toMatch(/\.modal-backdrop\s*,\s*\.modal--cookie-import/);
    expect(declarations(".modal--cookie-import")).toMatch(/width:\s*min\(1120px,\s*100%\)/);
    expect(declarations(".modal-backdrop")).not.toMatch(/width:\s*min\(1120px,\s*100%\)/);
  });

  it("preserves the task drawer as an independent fixed overlay", () => {
    const drawerBackdrop = declarations(".drawer-backdrop");

    expect(drawerBackdrop).toMatch(/position:\s*fixed/);
    expect(drawerBackdrop).toMatch(/inset:\s*0/);
    expect(drawerBackdrop).toMatch(/z-index:\s*100/);
  });
});

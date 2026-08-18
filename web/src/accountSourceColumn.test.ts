import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { accountSourceLabel } from "./App";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

describe("account source column", () => {
  it("maps persisted account labels and legacy null values to visible source text", () => {
    expect(accountSourceLabel("mmoshenqi")).toBe("mmoshenqi");
    expect(accountSourceLabel("macbook")).toBe("macbook");
    expect(accountSourceLabel(null)).toBe("未标注");
  });

  it("renders the source as its own account-table column", () => {
    expect(appSource).toContain("<th>账号来源</th>");
    expect(appSource).toContain("accountSourceLabel(account.label)");
    expect(styles).toContain(".account-source-badge--mmoshenqi");
    expect(styles).toContain(".account-source-badge--macbook");
    expect(styles).toContain(".account-source-badge--unlabeled");
  });
});

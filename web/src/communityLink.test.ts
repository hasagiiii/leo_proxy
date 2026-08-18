import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

describe("Telegram community entry", () => {
  it("keeps a safe external community link in the global sidebar", () => {
    expect(appSource).toContain('https://t.me/+x3r78uCRo5lmOTU9');
    expect(appSource).toContain('target="_blank"');
    expect(appSource).toContain('rel="noopener noreferrer"');
    expect(appSource).toContain('Telegram 交流群');
  });
});

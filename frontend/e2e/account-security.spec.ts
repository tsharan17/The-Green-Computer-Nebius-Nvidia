import { expect, test } from "@playwright/test";
import { createHmac } from "node:crypto";

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s) so the test can act as the authenticator app. */
function totp(secretB32: string, step: number): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/\s|=/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const mac = createHmac("sha1", key).update(msg).digest();
  const o = mac[mac.length - 1] & 0x0f;
  return String((mac.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
const now = () => Math.floor(Date.now() / 1000 / 30);

test("confirm email, turn on two-step sign-in, sign in with a code, preview retention", async ({ page }) => {
  const who = `secure-${Date.now()}@example.com`;
  const password = "an e2e test passphrase";
  await page.goto("/#/signup");
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("checkbox", { name: /I have read and accept/ }).check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Start a new analysis" })).toBeVisible();

  await page.goto("/#/account");
  const sec = page.getByRole("region", { name: "Sign-in security" });
  await expect(sec.getByText("not confirmed yet")).toBeVisible();
  await sec.getByRole("button", { name: "Send a new confirmation link" }).click();
  await sec.getByRole("link", { name: "open the confirmation link" }).click();
  await expect(page.getByText(`${who} is confirmed.`)).toBeVisible();
  await page.goto("/#/account");
  await expect(sec.locator(".outcome.ok").first()).toContainText("confirmed");

  await sec.getByRole("button", { name: "Set up two-step sign-in" }).click();
  const secret = (await sec.locator(".code-big").textContent())!.replace(/\s/g, "");
  await sec.getByLabel("Code from the app").fill(totp(secret, now()));
  await sec.getByRole("button", { name: "Turn on" }).click();
  await expect(sec.getByText(/Save these recovery codes now/)).toBeVisible();
  const recovery = (await sec.locator("pre.cmd").textContent())!.trim().split("\n");
  expect(recovery).toHaveLength(10);
  await sec.getByRole("button", { name: "I have saved them" }).click();
  await expect(sec.getByText("Signing in asks for a code")).toBeVisible();

  // Retention: rules save, and the dry run removes nothing new.
  const data = page.getByRole("region", { name: "Workspace data" });
  await data.getByLabel("Exported files and their records").fill("30");
  await data.getByRole("button", { name: "Save rules" }).click();
  await expect(data.getByText("Retention rules saved.")).toBeVisible();
  await data.getByRole("button", { name: "Show what would be removed" }).click();
  await expect(data.getByText("Nothing is old enough to remove.")).toBeVisible();

  // Sign out, then back in: password, then a fresh code.
  await page.getByRole("button", { name: who }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Code from your authenticator app").fill("000000" === totp(secret, now() + 1) ? "111111" : "000000");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("not valid");
  await page.getByLabel("Code from your authenticator app").fill(totp(secret, now() + 1));
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: /Find out whether|workspace/ })).toBeVisible();
});

import { type Page, expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

const PASSWORD = "an e2e test passphrase";
let n = 0;
const email = (tag: string) => `${tag}-${Date.now()}-${n++}@example.com`;

async function signUp(page: Page, who: string) {
  await page.goto("/#/signup");
  await page.getByLabel("Your name (optional)").fill("E2E");
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("checkbox", { name: /I have read and accept/ }).check();
  await page.getByRole("button", { name: "Create account" }).click();
  // A new account lands on the guided start.
  await expect(page.getByRole("heading", { name: "Start a new analysis" })).toBeVisible();
}

const analyseButton = (page: Page) =>
  page.getByRole("region", { name: "Decision summary" }).getByRole("button", { name: /^Analyse/ });

async function openExample(page: Page): Promise<string> {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Example: YOLOv8s on a robot/ }).click();
  await expect(page).toHaveURL(/#\/p\/\d+\/input/);
  return page.url().match(/#\/p\/(\d+)/)![1];
}

test("sign-up, analysis, design plan, comparison, export and history", async ({ page }) => {
  await signUp(page, email("journey"));
  const pid = await openExample(page);

  await analyseButton(page).click();
  await expect(page).toHaveURL(new RegExp(`#/p/${pid}/summary`));
  // Step 3: the analysis of today's setup only, with no recommended change on it.
  await expect(page.getByText(/Step 3 of 6: Analysis/)).toBeVisible();
  await expect(page.getByText(/today it is predicted to miss/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "What limits it" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recommended next step" })).toHaveCount(0);

  // Step 4: the decision bar and the page agree on the one next step.
  await page.getByRole("region", { name: "Decision summary" }).getByRole("link", { name: /Next: Try changes/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/p/${pid}/recommendations[?]tab=engine`));
  await page.getByRole("button", { name: "Start with the top-ranked change" }).click();
  await expect(page.getByRole("heading", { name: "Updated analysis" })).toBeVisible({ timeout: 30_000 });

  // Step 5: build, analyse and save a plan, reached with the page's own Next button.
  await page.getByRole("navigation", { name: "Back and next" }).getByRole("link", { name: /Next: Build and compare a plan/ }).click();
  await page.getByRole("button", { name: /Analyse this plan/ }).click();
  await expect(page.getByRole("heading", { name: "Today vs the plan" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Each change on its own" })).toBeVisible();
  await page.getByLabel("Name").fill("E2E plan");
  await page.getByRole("button", { name: "Save plan" }).click();
  await expect(page.getByText(/Saved “E2E plan” as version 1/)).toBeVisible();
  await page.getByRole("tab", { name: /Saved plans/ }).click();
  await expect(page.getByRole("cell", { name: /E2E plan/ })).toBeVisible();
  // Every recommendation in full: software and hardware, each with its validation state (optional view).
  await page.goto(`/#/p/${pid}/recommendations?tab=engine`);
  await page.getByRole("tab", { name: "Every recommendation, in full" }).click();
  await expect(page.getByRole("heading", { name: "Recommendations by category" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Software \(/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Hardware \(/ })).toBeVisible();
  await expect(page.getByText(/Evaluated in a saved plan \(predicted\)/).first()).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Workflow stages" })).toBeVisible();
  await page.getByRole("tab", { name: "Cooling options" }).click();
  await expect(page.getByRole("heading", { name: "Cooling options for this workload" })).toBeVisible();

  // The verification report for the same inputs.
  await page.goto(`/#/p/${pid}/check`);
  await expect(page.getByRole("heading", { name: "Verification report" })).toBeVisible();
  await expect(page.getByText("Ready for analysis", { exact: false }).first()).toBeVisible();

  // The analysis screens render from the stored analysis.
  await page.goto(`/#/p/${pid}/system-map`);
  await expect(page.getByRole("heading", { name: "System map" })).toBeVisible();
  await expect(page.getByText("Predicted by the analytical engine", { exact: true })).toBeVisible();
  await page.goto(`/#/p/${pid}/thermal`);
  await expect(page.getByRole("heading", { name: "Cooling: how much heat can it take?" })).toBeVisible();
  await expect(page.getByRole("img", { name: /warmest ambient temperature/ })).toBeVisible({ timeout: 30_000 });

  // Export: a generated report and a plan file download (the PDF itself is checked by the API tests;
  // Edge opens downloaded PDFs in a tab of its own, which would leak into later tests).
  await page.goto(`/#/p/${pid}/verify?tab=export`);
  const report = page.locator(".export-list li", { hasText: "Engineering report (Markdown)" });
  const dl = page.waitForEvent("download");
  await report.getByRole("button", { name: "Download" }).click();
  const md = await dl;
  expect(md.suggestedFilename()).toMatch(/report\.md$/);
  expect(await md.failure()).toBeNull(); // waits until the file is fully written
  const planRow = page.locator(".export-list li", { hasText: "Design plan (JSON)" });
  const dl2 = page.waitForEvent("download");
  await planRow.getByRole("button", { name: "Download" }).click();
  const planFile = await dl2;
  expect(planFile.suggestedFilename()).toMatch(/plan-e2e-plan-v1\.json$/);
  expect(await planFile.failure()).toBeNull();
  await expect(page.getByRole("heading", { name: "Export history" })).toBeVisible();

  // History records versions, the analysis and the exports.
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByRole("heading", { name: "Input versions" })).toBeVisible();
  await expect(page.locator(".audit").getByText("Analysis run").first()).toBeVisible();
  await expect(page.locator(".audit").getByText("Exported").first()).toBeVisible();
});

test("upload an ONNX model and get an honest answer for unsupported files", async ({ page }) => {
  await signUp(page, email("upload"));
  await openExample(page);
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose files" }).click();
  await (await chooser).setFiles(path.join(here, "fixtures", "tiny.onnx"));
  const item = page.locator(".intake-item", { hasText: "tiny.onnx" });
  await expect(item.getByText("valid", { exact: true })).toBeVisible();
  await expect(item.getByText("Selected as the project's model.")).toBeVisible();
  await expect(item.getByText("Graph summary")).toBeVisible();

  await page.getByRole("tab", { name: "FPGA" }).click();
  const chooser2 = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose files" }).click();
  await (await chooser2).setFiles({ name: "core.vhd", mimeType: "text/plain", buffer: Buffer.from("entity m is end m;") });
  const sv = page.locator(".intake-item", { hasText: "core.vhd" });
  await expect(sv.getByText("unsupported", { exact: true })).toBeVisible();
  await expect(sv.getByText(/What works today:.*report_utilization|What works today:.*Vivado/)).toBeVisible();
});

test("sign out and back in; a wrong password is refused", async ({ page }) => {
  const who = email("session");
  await signUp(page, who);
  await page.getByRole("button", { name: "E2E" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("incorrect");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Find out whether|workspace/ })).toBeVisible();
});

test("one account cannot open another account's project", async ({ browser }) => {
  const a = await browser.newContext();
  const pa = await a.newPage();
  await signUp(pa, email("owner"));
  const pid = await openExample(pa);

  const b = await browser.newContext();
  const pb = await b.newPage();
  await signUp(pb, email("other"));
  await pb.goto(`/#/p/${pid}/input`);
  await expect(pb.getByRole("heading", { name: "Project not found" })).toBeVisible();
  const r = await pb.request.get(`/api/v1/projects/${pid}`);
  expect(r.status()).toBe(404);
  await a.close();
  await b.close();
});

test("account deletion signs out and removes access", async ({ page }) => {
  const who = email("delete");
  await signUp(page, who);
  await page.goto("/#/account");
  await page.getByLabel("Password", { exact: true }).last().fill(PASSWORD);
  await page.getByLabel("Type DELETE to confirm").fill("DELETE");
  await page.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(who);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("incorrect");
});

test("guided start: use case, model, hardware, priority, then a plain result", async ({ page }) => {
  await signUp(page, email("guide"));
  await expect(page.getByRole("listitem").filter({ hasText: "What you are building" })).toHaveAttribute("aria-current", "step");
  await page.getByRole("button", { name: /Smart camera/ }).click();
  await expect(page.getByRole("heading", { name: "Add your AI model" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Describe your hardware" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "What matters most?" })).toBeVisible();
  const faster = page.getByRole("radio", { name: /Faster response/ });
  await faster.click();
  await expect(faster).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Faster response", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Analyse", exact: true }).click();

  await expect(page).toHaveURL(/#\/p\/\d+\/summary/);
  const bar = page.getByRole("region", { name: "Decision summary" });
  await expect(bar.getByText("Current setup")).toBeVisible();
  await expect(bar.getByText("What limits it")).toBeVisible();
  await expect(page.getByRole("heading", { name: "What limits it" })).toBeVisible();

  // Simple mode: detail is folded away and the analysis detail views sit under one closed disclosure.
  const eng = page.locator("details.level-block.engineering");
  await expect(eng).not.toHaveAttribute("open", "");
  const more = page.locator("details.nav-more", { hasText: "More detail (5)" });
  await expect(more).not.toHaveAttribute("open", "");
  // Engineering mode: detail opens and every view is listed; the choice survives a reload.
  await page.getByRole("radio", { name: "Engineering" }).click();
  await expect(eng).toHaveAttribute("open", "");
  await expect(more).toHaveAttribute("open", "");
  await page.reload();
  await expect(page.getByRole("radio", { name: "Engineering" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("radio", { name: "Simple" }).click();
});

test("example projects, project health and global search", async ({ page }) => {
  await signUp(page, email("demo"));
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: /Find out whether/ })).toBeVisible();
  await page.getByRole("button", { name: /Add five example projects/ }).click();
  await expect(page.getByText("Added 5 analysed example projects.")).toBeVisible();
  const row = page.getByRole("row", { name: /Demo: GPU inference server/ });
  await expect(row.getByText(/Passes|Fails|Uncertain/).first()).toBeVisible();

  const search = page.getByRole("combobox", { name: "Search" });
  await page.keyboard.press("Control+k");
  await expect(search).toBeFocused();
  await search.fill("voice");
  const option = page.getByRole("option", { name: /Voice command on a microcontroller/ }).first();
  await expect(option).toBeVisible();
  await search.press("Enter");
  await expect(page).toHaveURL(/#\/p\/\d+\/summary/);
  await expect(page.getByRole("heading", { name: /Voice command on a microcontroller/ })).toBeVisible();
});

test("design files build a versioned hardware graph with sources", async ({ page }) => {
  await signUp(page, email("graph"));
  await page.goto("/#/");
  await page.getByRole("button", { name: /Add five example projects/ }).click();
  await page.getByRole("link", { name: /Demo: Inspection on an FPGA/ }).click();
  const pid = page.url().match(/#\/p\/(\d+)/)![1];
  const verilog = "module top(input wire clk, input wire [7:0] a, output wire [7:0] y);\n  sub u0 (.clk(clk));\n  assign y = a;\nendmodule\nmodule sub(input clk);\nendmodule\n";
  const up = await page.request.post(`/api/v1/intake/upload?filename=top.v&project_id=${pid}`, {
    headers: { "X-GC-CSRF": "1", "Content-Type": "application/octet-stream" }, data: verilog,
  });
  expect((await up.json()).status).toBe("valid");

  await page.goto(`/#/p/${pid}/system-map`);
  const graph = page.getByRole("region", { name: "Hardware graph" });
  await expect(graph.getByText(/from the device profile\./)).toBeVisible();
  await graph.getByRole("checkbox", { name: /top\.v/ }).check();
  await graph.getByRole("button", { name: /Add 1 to the graph/ }).click();
  await expect(graph.getByText(/Saved as version 1/)).toBeVisible();
  await expect(graph.getByText(/from the device profile and 1 design file/)).toBeVisible();
  await graph.locator("details.level-block.engineering > summary").click();
  await expect(graph.getByRole("cell", { name: /^top rtl:top/ })).toBeVisible();
  await expect(graph.getByRole("cell", { name: "Verilog: top.v" }).first()).toBeVisible();
});

test("3D map: layouts, overlays, today vs plan, keyboard and table fallback", async ({ page }) => {
  await signUp(page, email("map3d"));
  await page.goto("/#/");
  await page.getByRole("button", { name: /Add five example projects/ }).click();
  await page.getByRole("link", { name: /Demo: GPU inference server/ }).click();
  const pid = page.url().match(/#\/p\/(\d+)/)![1];
  await page.goto(`/#/p/${pid}/system-map`);
  const map = page.getByRole("region", { name: "3D map" });
  await expect(map.getByText(/Logical layout: an arrangement of the modelled parts, not their physical positions/)).toBeVisible();

  const webgl = await map.locator(".m3-canvas canvas").count().then(async (n) => n > 0 || (await map.locator(".m3-canvas").count()) > 0);
  if (webgl) {
    const stage = map.getByRole("img", { name: /Logical arrangement of NVIDIA L4/ });
    await expect(stage).toBeVisible();
    await stage.focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("+");
    await map.getByRole("button", { name: "Show as a table" }).click();
  } else {
    await expect(map.getByText(/cannot draw 3D/)).toBeVisible();
  }
  // The table carries the same scene: modelled parts with their heat and source.
  const table = map.locator("table.m3-table");
  await expect(table.getByRole("cell", { name: "Compute engine" })).toBeVisible();
  await expect(table.getByRole("row", { name: /Compute engine/ }).locator("td.num")).not.toHaveText("–");

  // Today and the plan side by side, playing on one clock.
  await map.getByRole("checkbox", { name: /Today and the plan side by side/ }).check();
  await expect(table.getByRole("cell", { name: "With the plan" }).first()).toBeVisible();
  await expect(table.getByRole("cell", { name: "Today" }).first()).toBeVisible();
  await map.getByRole("slider", { name: "Time in the batch" }).fill("100");
  await expect(map.locator(".m3-now")).toContainText("Today:");
  await expect(map.locator(".m3-now")).toContainText("With the plan:");

  // No board file yet: the physical layout says what is needed instead of drawing a guess.
  await map.getByRole("radio", { name: "Physical (board)" }).click();
  await expect(map.getByText(/No physical positions yet\. Upload a KiCad board/)).toBeVisible();
});

test("thermal attribution, runtime effects and evidence are labelled", async ({ page }) => {
  await signUp(page, email("physics"));
  const pid = await openExample(page);
  await page.getByRole("region", { name: "Decision summary" }).getByRole("button", { name: /^Analyse/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/p/${pid}/summary`));

  await page.goto(`/#/p/${pid}/thermal`);
  const heat = page.getByRole("region", { name: "Heat by part" });
  await expect(heat.getByText(/The decision model puts the chip at .* °C \(lumped\)/)).toBeVisible();
  await expect(heat.getByText("Predicted only: no measured temperature for this setup yet")).toBeVisible();
  await expect(heat.locator(".tag.attribution").first()).toBeVisible();
  await heat.locator("details.level-block.engineering > summary").click();
  await expect(heat.getByRole("cell", { name: "assumed" }).first()).toBeVisible();

  await page.goto(`/#/p/${pid}/energy`);
  const rt = page.getByRole("region", { name: "Around the model: runtime and memory" });
  await expect(rt.locator(".tn-summary")).toContainText(/busy .*% of the time/);
  await rt.locator("details.level-block.explain > summary").click();
  await expect(rt.getByRole("cell", { name: /Kernel launch and framework overhead/ })).toBeVisible();
  await expect(rt.getByRole("cell", { name: /Loading the model at start-up/ })).toBeVisible();

  const ev = page.getByRole("region", { name: "Evidence for every number" });
  await expect(ev.getByText(/no calibration from real runs is applied yet/)).toBeVisible();
  await ev.getByText("Latency per input").click();
  await expect(ev.getByRole("cell", { name: "Peak compute" }).first()).toBeVisible();
  await ev.getByRole("radio", { name: "With the plan" }).click();
  await expect(ev.getByText("Latency per input")).toBeVisible();
});

test("cooling v2: where it runs rules options out, with reasons and heat sources", async ({ page }) => {
  await signUp(page, email("cooling"));
  const pid = await openExample(page);
  await page.goto(`/#/p/${pid}/recommendations?tab=cooling`);
  const cool = page.getByRole("region", { name: "Cooling options for this workload" });
  await expect(cool.getByText(/Best fit here:/)).toBeVisible();
  await expect(cool.getByText(/Where it runs: no limits set/)).toBeVisible();
  await cool.locator("details.level-block.explain > summary").click();
  await expect(cool.getByText("Voltage regulators")).toBeVisible();
  await expect(cool.getByText(/only airflow over the board or venting reaches them|board airflow is enough/)).toBeVisible();

  await page.goto(`/#/p/${pid}/constraints`);
  await page.getByRole("checkbox", { name: /Sealed enclosure/ }).check();
  await page.getByRole("button", { name: /Save as version 2/ }).click();
  await expect(page.getByText(/inputs version 2/)).toBeVisible();

  await page.goto(`/#/p/${pid}/recommendations?tab=cooling`);
  await expect(cool.getByText(/Where it runs: sealed enclosure\./)).toBeVisible();
  await cool.getByText(/Options not considered here/).click();
  await expect(cool.getByText(/Heatsink with 40 mm fan.*needs an air path for a fan, but the enclosure is sealed/)).toBeVisible();
  await expect(cool.getByRole("cell", { name: /Heatsink with 40 mm fan/ })).toHaveCount(0);
});

test("hand-over: founder summary, grouped exports and a reproducibility check", async ({ page }, testInfo) => {
  await signUp(page, email("handoff"));
  const pid = await openExample(page);
  await page.getByRole("region", { name: "Decision summary" }).getByRole("button", { name: /^Analyse/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/p/${pid}/summary`));
  await page.goto(`/#/p/${pid}/verify?tab=export`);
  await expect(page.getByRole("heading", { name: "Start here" })).toBeVisible();
  await expect(page.locator(".not-offered", { hasText: "TensorRT engine" })).toContainText("not offered");

  const summary = page.locator(".export-list li", { hasText: "Summary for founders and customers (Markdown)" });
  const dl = page.waitForEvent("download");
  await summary.getByRole("button", { name: "Download" }).click();
  const md = await dl;
  expect(md.suggestedFilename()).toMatch(/-summary\.md$/);
  expect(await md.failure()).toBeNull();

  const manifestRow = page.locator(".export-list li", { hasText: "Reproducibility manifest (JSON)" });
  const dl2 = page.waitForEvent("download");
  await manifestRow.getByRole("button", { name: "Download" }).click();
  const man = await dl2;
  const file = testInfo.outputPath("manifest.json");
  await man.saveAs(file);
  await page.locator("details.level-block.engineering > summary", { hasText: "Check a reproducibility manifest" }).click();
  await page.getByLabel("Reproducibility manifest").setInputFiles(file);
  const answer = page.locator(".exports p[role=status], .exports p[role=alert]").last();
  await expect(answer).toContainText(/Reproduced: \d+ numbers match/, { timeout: 60_000 });
});

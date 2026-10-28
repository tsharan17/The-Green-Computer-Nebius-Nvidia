"""Acceptance check for a RUNNING copy of The Green Computer (local or the public URL).

    python scripts/acceptance.py                      # http://localhost:8000
    python scripts/acceptance.py https://your-demo    # the deployed endpoint, as a judge would see it

Standard library only, so it runs anywhere. It walks the journey a judge would: health, analyse the
robot example, read the verdict and the four levels of advice, confirm requirements were not
relaxed, export the report and recipe, and report what is connected. Steps that need a key (the
Green Agent) or are switched off in the public demo are reported as SKIP, never faked.
Exit code 0 = every non-skipped step passed.
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import secrets
import sys
import urllib.error
import urllib.request
import zipfile

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
API = BASE + "/api/v1"
results: list[tuple[str, str, str]] = []
# A throwaway account per run: the session cookie lives in this jar, and the account is deleted
# again at the end, so running the check leaves nothing behind on the server.
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))
EMAIL = f"acceptance-{secrets.token_hex(4)}@example.com"
PASSWORD = "acceptance " + secrets.token_hex(8)


def call(method: str, path: str, body=None, raw: bool = False, timeout: float = 90.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-GC-CSRF": "1"})  # fmt: skip
    try:
        with OPENER.open(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload.decode() or "null")), r.headers
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except ValueError:
            detail = None
        return e.code, detail, e.headers


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append(("PASS" if ok else "FAIL", name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def skip(name: str, why: str) -> None:
    results.append(("SKIP", name, why))
    print(f"[SKIP] {name}: {why}")


def main() -> int:
    try:
        with urllib.request.urlopen(BASE + "/", timeout=20) as r:
            page = r.read().decode("utf-8", "replace")
        step("the web page loads", r.status == 200 and "The Green Computer" in page)
    except Exception as e:  # noqa: BLE001
        step("the web page loads", False, str(e))
        return 1

    code, health, _ = call("GET", "/health")
    if not step("API health", code == 200 and health and health["status"] == "ok",
                f"v{health.get('version')} demo_mode={health.get('demo_mode')}" if health else ""):  # fmt: skip
        return 1
    agent_on = bool(health["agent"]["configured"])

    code, _, _ = call("GET", "/projects")
    step("data needs an account", code == 401)
    code, me, _ = call("POST", "/auth/signup", {"email": EMAIL, "password": PASSWORD})
    if not step("a visitor can create an account", code == 201 and bool(me), EMAIL):
        return 1

    code, projects, _ = call("GET", "/projects")
    tpl = next((p for p in projects or [] if p["spec"]["name"].startswith("Example")), None)
    step("the example project is preloaded", tpl is not None)
    if tpl is None:
        return 1
    code, mine, _ = call("POST", "/projects", {**tpl["spec"], "name": "Acceptance check"})
    if not step("a visitor can start their own project", code == 201 and mine is not None):
        return 1
    pid = mine["id"]

    code, res, _ = call("POST", f"/projects/{pid}/optimise")
    if not step("analyse the robot example", code == 200 and res is not None, f"HTTP {code}"):
        return 1
    v = res["verdict"]
    step("verdict: the current setup fails and says why", not v["baseline"]["ok"] and bool(v["baseline"]["headline"]),
         v["baseline"]["headline"])  # fmt: skip
    step("verdict: a changed setup meets every requirement", bool(v["improved"] and v["improved"]["ok"]),
         f"{v['savings']['power_percent']:.0f}% less power" if v.get("savings") else "")  # fmt: skip
    step("a cheapest fix is offered", bool(res["repair"]["plans"]))
    tiers = {a["tier"] for a in res["advice"]}
    step("advice covers model, settings and chip-design levels", {"model", "configure", "design"} <= tiers,
         ", ".join(sorted(tiers)))  # fmt: skip
    step("every recommendation explains why and how sure we are",
         all(a["why"] and a["confidence_reason"] for a in res["advice"]))  # fmt: skip
    step("alternatives and hardware what-ifs are present", bool(res["alternatives"] and res["whatifs"]))

    code, cur, _ = call("GET", f"/projects/{pid}")
    c = cur["spec"]["constraints"]
    step("requirements were not relaxed", c["max_power_w"] == tpl["spec"]["constraints"]["max_power_w"]
         and c["min_accuracy"] == tpl["spec"]["constraints"]["min_accuracy"])  # fmt: skip

    code, md, _ = call("GET", f"/projects/{pid}/report", raw=True)
    step("the report exports", code == 200 and b"## 2. The verdict" in md)
    code, z, _ = call("GET", f"/projects/{pid}/recipe.zip", raw=True)
    try:
        names = zipfile.ZipFile(io.BytesIO(z)).namelist() if code == 200 else []
    except zipfile.BadZipFile:
        names = []
    step("the recipe downloads as a valid zip", "README.md" in names and "runtime_config.json" in names,
         f"{len(names)} files")  # fmt: skip

    code, lib, _ = call("GET", "/hardware/library")
    kinds = {d["kind"] for d in lib or []}
    step("the hardware library spans boards, MCUs, FPGAs, accelerators and PC chips",
         {"mcu", "fpga", "accelerator", "sbc", "laptop_soc", "edge_soc"} <= kinds, ", ".join(sorted(kinds)))  # fmt: skip
    mcu = {**tpl["spec"], "name": "Acceptance MCU", "model_id": "tiny-kws-dscnn", "device_id": "stm32h743",
           "cooling_id": None, "baseline": None,
           "workload": {**tpl["spec"]["workload"], "fps": 4},
           "constraints": {**tpl["spec"]["constraints"], "max_power_w": 0.5, "max_latency_ms": 250}}  # fmt: skip
    code, mp, _ = call("POST", "/projects", mcu)
    if step("a microcontroller project can be created", code == 201 and mp is not None, f"HTTP {code}"):
        code, mres, _ = call("POST", f"/projects/{mp['id']}/optimise")
        step("a microcontroller project analyses (flash and SRAM checked)",
             code == 200 and any("weights" in c["name"] for c in mres["baseline"]["checks"]), f"HTTP {code}")  # fmt: skip
    code, prev, _ = call("POST", "/hardware/custom/preview",
                         {"name": "Acceptance FPGA", "style": "fpga", "dsp_slices": 1248, "fabric_mhz": 250,
                          "mem_bw_gbs": 19.2, "mem_gb": 4, "dynamic_w": 4.0})  # fmt: skip
    step("custom hardware can be described and previewed", code == 200 and prev and prev["kind"] == "fpga",
         f"HTTP {code}")  # fmt: skip

    code, ver, _ = call("GET", "/verify")
    step("real measurements are shown (honestly labelled)", code == 200 and bool(ver["summaries"]),
         "; ".join(f"{s['device_id']}/{s['precision']} n={s['n']}" for s in ver["summaries"][:2]) if ver else "")  # fmt: skip

    if agent_on:
        code, _, hdr = call("POST", "/agent/chat", {"project_id": pid, "message": "hi"}, raw=True, timeout=120)
        step("the Green Agent answers", code == 200)
    else:
        skip("the Green Agent", "not configured (no TOKEN_FACTORY_API_KEY); the rest of the app does not need it")

    code, _, _ = call("POST", "/benchmarks", {"device_id": "nebius-l40s"})
    if code == 403:
        skip("GPU benchmark launch", "switched off in the public demo (it spends credits)")
    elif code == 503:
        skip("GPU benchmark launch", "Nebius Serverless Jobs not available here")
    else:
        step("GPU benchmark launch is reachable", code in (200, 202, 422), f"HTTP {code}")

    code, _, _ = call("POST", "/auth/account/delete", {"password": PASSWORD, "confirm": "DELETE"})
    step("the account and its data can be deleted", code == 200)
    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\n{sum(r[0] == 'PASS' for r in results)} passed, {len(failed)} failed, "
          f"{sum(r[0] == 'SKIP' for r in results)} skipped")  # fmt: skip
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

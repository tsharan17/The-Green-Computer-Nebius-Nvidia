"""Fill a running server with demo data through its public API (standard library only).

    python scripts/demo_data.py                       # http://localhost:8000, new demo account
    python scripts/demo_data.py URL EMAIL PASSWORD    # use (or create) this account

Creates three analysed projects (an overheating robot, an MCU keyword spotter, ResNet-50 on a
Jetson), a saved design plan with its exports, predicted traces, and a real local run of ResNet-50
on the server's CPU. Every number it produces is computed by the server; nothing is invented here.
"""

from __future__ import annotations

import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/v1"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else f"demo-{secrets.token_hex(3)}@example.com"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "demo " + secrets.token_hex(6)
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(method: str, path: str, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-GC-CSRF": "1"})  # fmt: skip
    try:
        with OPENER.open(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def main() -> int:
    code, _ = call("POST", "/auth/signup", {"email": EMAIL, "password": PASSWORD, "display_name": "Demo"})
    if code == 409:
        code, _ = call("POST", "/auth/signin", {"email": EMAIL, "password": PASSWORD})
    if code not in (200, 201):
        print(f"could not sign in ({code})")
        return 1
    robot = call("GET", "/projects")[1][0]["id"]
    _, mcu = call("POST", "/projects", {"name": "Keyword spotting on an STM32H7", "model_id": "tiny-kws-dscnn",
                                         "device_id": "stm32h743",
                                         "workload": {"fps": 4, "ambient_c": 30},
                                         "constraints": {"max_power_w": 0.5, "max_latency_ms": 250}})  # fmt: skip
    _, rn = call("POST", "/projects", {"name": "ResNet-50 on a Jetson Orin Nano", "model_id": "resnet-50",
                                        "device_id": "jetson-orin-nano",
                                        "workload": {"fps": 30, "ambient_c": 30}})  # fmt: skip
    for pid in (robot, mcu["id"], rn["id"]):
        print("analysed", pid, call("POST", f"/projects/{pid}/optimise")[0])
        print("trace", pid, call("POST", f"/projects/{pid}/traces?which=best")[0])
    opts = call("GET", f"/projects/{robot}/plan-options")[1]
    _, plan = call("POST", f"/projects/{robot}/plans", {"change_ids": opts["recommended"], "name": "Fix overheating",
                                                         "note": "Recommended changes, as analysed."})  # fmt: skip
    for kind in ("plan_json", "comparison_csv", "report_pdf"):
        print("export", kind, call("POST", f"/projects/{robot}/exports", {"kind": kind, "plan_id": plan["id"]})[0])
    code, job = call("POST", f"/projects/{rn['id']}/executions", {"runs": 20, "warmup": 3})
    if code == 202:
        for _ in range(300):
            _, job = call("GET", f"/projects/{rn['id']}/executions/{job['id']}")
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        print("local run", job["status"], job.get("error") or job["result"].get("latency_ms", {}).get("mean"))
    else:
        print("local run not started:", job.get("detail"))
    print(f"\nDemo account ready: {EMAIL}  password: {PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Hardware import from datasheets: Tavily finds spec pages, Nemotron extracts values, a person confirms.

Nothing extracted is trusted. Every value is tied to the page it came from, implausible values are
dropped, anything not stated stays missing (never guessed), and the draft cannot be used in an
analysis until the user confirms it. Power and thermal constants a datasheet rarely gives are
derived from the power modes and are labelled "assumed".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.client import LLM, LLMError
from app.agent.recommender import loads_lenient
from app.integrations.tavily import SearchHit, TavilyClient
from app.schemas.hardware import CoolingOption, HardwareProfile, PowerMode
from app.schemas.metrics import Provenance

MAX_CHARS_PER_PAGE = 5000
MAX_PAGES = 3

EXTRACT_PROMPT = """You extract hardware specifications for an AI accelerator / SoC / board from web page excerpts.
Respond with ONLY one JSON object, no prose. Rules:
- Use null for anything the excerpts do not state. NEVER guess or use prior knowledge.
- peak_tflops are DENSE throughput in TFLOPS (fp32, fp16) or TOPS (int8). If a page gives only a "sparse" figure, put sparse/2 and say so in notes.
- mem_bw_gbs is memory bandwidth in GB/s; mem_gb is memory size in GB.
- power_modes: the vendor's named power modes with their power cap in watts.
- t_throttle_c: thermal throttle / max operating temperature in Celsius, if stated.
- "sources" maps each field name you filled to the URL of the excerpt it came from.
Schema:
{"name": str|null, "vendor": str|null, "kind": "edge_soc"|"datacenter_gpu"|null,
 "peak_tflops": {"fp32": num|null, "fp16": num|null, "int8": num|null},
 "mem_bw_gbs": num|null, "mem_gb": num|null,
 "power_modes": [{"name": str, "cap_w": num}], "tdp_w": num|null, "t_throttle_c": num|null,
 "sources": {field: url}, "notes": str}"""

RANGES = {
    "peak_tflops": (0.01, 5000.0),
    "mem_bw_gbs": (1.0, 8000.0),
    "mem_gb": (0.1, 2048.0),
    "cap_w": (0.5, 2000.0),
    "tdp_w": (0.5, 2000.0),
    "t_throttle_c": (40.0, 150.0),
}
REQUIRED = ("name", "peak_tflops", "mem_bw_gbs", "mem_gb")


class DatasheetError(ValueError):
    """The datasheet flow could not produce or confirm a profile."""


@dataclass
class Extraction:
    candidate: dict[str, Any]
    evidence: list[dict[str, Any]]
    missing: list[str]
    notes: str = ""
    dropped: list[str] = field(default_factory=list)


def search_hits(tavily: TavilyClient, device_name: str) -> list[SearchHit]:
    q = f"{device_name} datasheet specifications memory bandwidth TOPS TFLOPS power modes thermal"
    return tavily.search(q, max_results=5, include_raw_content=True)[:MAX_PAGES]


def _num(v: Any, lo: float, hi: float) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def _json_from(text: str) -> dict[str, Any]:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise DatasheetError("the model did not return a JSON object")
    try:
        data = loads_lenient(t[start : end + 1])
    except ValueError as e:
        raise DatasheetError(f"the model returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise DatasheetError("the model returned JSON that is not an object")
    return data


def clean_extraction(raw: dict[str, Any], urls: set[str]) -> Extraction:
    """Validate what the model returned; keep only plausible, sourced values."""
    cand: dict[str, Any] = {}
    dropped: list[str] = []
    raw_sources = raw.get("sources")
    sources: dict[str, Any] = raw_sources if isinstance(raw_sources, dict) else {}

    def src(name: str) -> str | None:
        u = sources.get(name)
        return u if isinstance(u, str) and u in urls else None

    for k in ("name", "vendor"):
        if isinstance(raw.get(k), str) and raw[k].strip():
            cand[k] = raw[k].strip()[:120]
    if raw.get("kind") in ("edge_soc", "datacenter_gpu"):
        cand["kind"] = raw["kind"]
    peaks = {}
    raw_p = raw.get("peak_tflops")
    p: dict[str, Any] = raw_p if isinstance(raw_p, dict) else {}
    for prec in ("fp32", "fp16", "int8"):
        x = _num(p.get(prec), *RANGES["peak_tflops"])
        if x is not None:
            peaks[prec] = x
        elif p.get(prec) is not None:
            dropped.append(f"peak_tflops.{prec}")
    if peaks:
        cand["peak_tflops"] = peaks
    for k in ("mem_bw_gbs", "mem_gb", "tdp_w", "t_throttle_c"):
        x = _num(raw.get(k), *RANGES[k])
        if x is not None:
            cand[k] = x
        elif raw.get(k) is not None:
            dropped.append(k)
    modes = []
    for m in raw.get("power_modes") or []:
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            cap = _num(m.get("cap_w"), *RANGES["cap_w"])
            if cap is not None:
                modes.append({"name": m["name"].strip()[:40], "cap_w": cap})
    if modes:
        cand["power_modes"] = modes

    evidence = []
    for k, v in cand.items():
        u = src(k) or (src("peak_tflops") if k == "peak_tflops" else None)
        evidence.append({"field": k, "value": v, "source_url": u})
    missing = [k for k in REQUIRED if k not in cand]
    return Extraction(
        candidate=cand,
        evidence=evidence,
        missing=missing,
        notes=str(raw.get("notes") or "")[:500],
        dropped=dropped,
    )


def extract_hardware_spec(
    llm: LLM, model_id: str, device_name: str, hits: list[SearchHit], max_tokens: int = 4096
) -> Extraction:
    if not hits:
        raise DatasheetError("no spec pages were found for that name")
    pages = []
    for h in hits:
        body = (h.raw_content or h.content or "")[:MAX_CHARS_PER_PAGE]
        pages.append(f"URL: {h.url}\nTITLE: {h.title}\nEXCERPT:\n{body}")
    user = f"Device: {device_name}\n\n" + "\n\n---\n\n".join(pages)
    messages = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": user}]
    try:
        reply = llm.chat(model_id, messages, temperature=0.0, max_tokens=max_tokens)
        if reply.finish_reason == "length" and "}" not in reply.content:
            # A reasoning model can spend the whole budget thinking and write nothing; give it twice the room once.
            reply = llm.chat(model_id, messages, temperature=0.0, max_tokens=min(max_tokens * 2, 8192))
    except LLMError as e:
        raise DatasheetError(str(e)) from e
    if reply.finish_reason == "length" and "}" not in reply.content:
        raise DatasheetError(
            "the model ran out of room before it wrote an answer (reasoning models spend part of their budget "
            "thinking). Try again, or enter the device by hand"
        )
    return clean_extraction(_json_from(reply.content), {h.url for h in hits})


def build_profile(
    candidate: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    device_id: str = "custom-hw",
) -> HardwareProfile:
    """Turn a confirmed candidate (plus the user's corrections) into a usable profile.

    Missing power/thermal constants are derived from the power modes and labelled assumed.
    """
    c = {**candidate, **(overrides or {})}
    missing = [k for k in REQUIRED if not c.get(k)]
    if missing:
        raise DatasheetError(f"required values are missing: {', '.join(missing)}")
    peaks = {k: float(v) for k, v in c["peak_tflops"].items() if v and float(v) > 0}
    if not peaks:
        raise DatasheetError("at least one of fp32/fp16/int8 peak throughput is required")

    raw_modes = c.get("power_modes") or []
    if not raw_modes:
        cap = c.get("tdp_w")
        if not cap:
            raise DatasheetError("give at least one power mode or a TDP in watts")
        raw_modes = [{"name": "Default", "cap_w": cap}]
    raw_modes = sorted(raw_modes, key=lambda m: m["cap_w"])
    pmax = float(raw_modes[-1]["cap_w"])
    gamma = 2.5
    modes = []
    for i, m in enumerate(raw_modes):
        ratio = float(m["cap_w"]) / pmax
        # Assumed: clocks scale so that dynamic power ~ f^gamma tracks the mode's cap.
        modes.append(
            PowerMode(
                id=re.sub(r"\W+", "_", str(m["name"])).strip("_") or f"mode{i}",
                name=str(m["name"]),
                cap_w=float(m["cap_w"]),
                freq_scale=max(0.15, min(1.0, ratio ** (1 / gamma))),
                mem_scale=max(0.4, min(1.0, 0.5 + 0.5 * ratio)),
            )
        )
    t_thr = float(c.get("t_throttle_c") or 90.0)
    # Assumed cooling: sized so a passive design reaches about 70% of the headroom at full power.
    r_passive = 0.7 * (t_thr - 25.0) / pmax
    cooling = [
        CoolingOption(id="passive", name="Passive heatsink (assumed)", r_th_k_per_w=r_passive,
                      fan_w=0.0, c_th_j_per_k=max(40.0, 8.0 * pmax)),
        CoolingOption(id="active", name="Heatsink with fan (assumed)", r_th_k_per_w=0.6 * r_passive,
                      fan_w=min(3.0, 0.05 * pmax), c_th_j_per_k=max(40.0, 8.0 * pmax)),
    ]  # fmt: skip
    src = Provenance.EXTRACTED_UNVERIFIED
    return HardwareProfile(
        id=device_id,
        name=str(c["name"]),
        vendor=str(c.get("vendor") or "unknown"),
        kind=c.get("kind") or "edge_soc",
        role="imported from a datasheet (not measured)",
        peak_tflops=peaks,
        mem_bw_gbs=float(c["mem_bw_gbs"]),
        mem_gb=float(c["mem_gb"]),
        idle_w=0.12 * pmax,
        dyn_w_max=0.55 * pmax,
        mem_w_max=0.20 * pmax,
        leak_w_25c=0.06 * pmax,
        gamma=gamma,
        t_throttle_c=t_thr,
        t_shutdown_c=t_thr + 15.0,
        power_modes=modes,
        cooling_options=cooling,
        default_cooling="passive",
        sources={
            "peak_tflops": src,
            "mem_bw_gbs": src,
            "mem_gb": src,
            "power_modes": src if candidate.get("power_modes") else Provenance.ASSUMED,
            "power_model": Provenance.ASSUMED,
            "thermal": Provenance.ASSUMED,
        },
        notes=(
            "Imported from a datasheet and confirmed by the user. Throughput, bandwidth and "
            "memory are extracted values; the power model and cooling are assumptions derived "
            "from the power modes. Not measured."
        ),
        verified=False,
        alternative_candidate=True,
    )

"""Matplotlib and seaborn figures for a stored execution trace.

Every figure is drawn from the trace's own numbers (the engine computes them; nothing is invented
here) and carries the trace's provenance in its footer. The one derived quantity is the board
temperature field: the engine's lumped result is spread over the component layout with a diffusion
kernel and anchored so the heat-weighted mean rise equals the engine's rise. The footer says so.
Colours come from standard scientific colormaps (turbo for temperature, viridis for other
heatmaps, seaborn "deep" for series); see app/render/theme.py.
"""

from __future__ import annotations

import io
import textwrap
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from app.render.theme import PALETTES

_LOCK = threading.Lock()  # matplotlib's global style state is not thread-safe
ENERGY_KEYS = ("compute", "memory", "idle", "leakage", "cooling")
ENERGY_LABEL = {"compute": "Compute", "memory": "Memory traffic", "idle": "Idle", "leakage": "Leakage", "cooling": "Cooling"}


class FigureError(ValueError):
    pass


def _fmt_bytes(v: float) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.3g} {unit}"
    return f"{v:.0f} B"


def _fmt_time(s: float) -> str:
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.1f} min"
    return f"{s / 3600:.1f} h"


def _provenance(t: dict[str, Any]) -> str:
    kind = "Measured" if t.get("kind") == "measured" else "Predicted"
    return f"{kind} ({t.get('provenance', 'analytical')}) | {t.get('model_name', '')} on {t.get('device_name', '')} | {t.get('precision', '')}, batch {t.get('batch', 1)}"


def _style(pal: dict[str, Any]) -> dict[str, Any]:
    return {
        "figure.facecolor": pal["bg"], "axes.facecolor": pal["bg"], "savefig.facecolor": pal["bg"],
        "axes.edgecolor": pal["line"], "axes.labelcolor": pal["ink"], "text.color": pal["ink"],
        "xtick.color": pal["muted"], "ytick.color": pal["muted"], "grid.color": pal["grid"],
        "axes.grid": True, "grid.linewidth": 0.7, "axes.spines.top": False, "axes.spines.right": False,
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "legend.frameon": False, "figure.dpi": 100,
    }  # fmt: skip


def _finish(fig: Any, t: dict[str, Any], pal: dict[str, Any], extra: str = "") -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    foot = _provenance(t) + (f" | {extra}" if extra else "")
    fig.text(0.01, 0.01, "\n".join(textwrap.wrap(foot, 170)), fontsize=7.5, color=pal["muted"], ha="left", va="bottom")
    FigureCanvasAgg(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160)
    return buf.getvalue()


def _cbar(fig: Any, mappable: Any, ax: Any, label: str, pal: dict[str, Any], cax: Any = None) -> Any:
    cb = fig.colorbar(mappable, ax=ax, cax=cax, pad=0.02, fraction=0.04)
    cb.set_label(label, color=pal["ink"])
    cb.outline.set_edgecolor(pal["line"])
    cb.ax.tick_params(colors=pal["muted"])
    return cb


# --- thermal --------------------------------------------------------------------------------------


def _board_field(t: dict[str, Any], n: int = 200) -> np.ndarray:
    """Heat-source footprints spread by a diffusion kernel, scaled so the hottest point equals the
    engine's chip temperature. Only the peak is the engine's number; the spread is illustrative."""
    th = t["thermal"]
    rise = max(th["steady_c"] - th["ambient_c"], 0.05)
    q = np.zeros((n, n))
    for c in t["components"]:
        if c["heat_w"] <= 0 or c["kind"] == "cooling":
            continue
        lay = c["layout"]
        x0, y0 = int(lay["x"] * n), int(lay["y"] * n)
        x1, y1 = max(int((lay["x"] + lay["w"]) * n), x0 + 1), max(int((lay["y"] + lay["h"]) * n), y0 + 1)
        q[y0:y1, x0:x1] += c["heat_w"] / max((x1 - x0) * (y1 - y0), 1)
    sigma = 0.06 * n  # diffusion length: six percent of the board
    pad = n // 2  # zero padding keeps heat from wrapping round the board edges
    qp = np.pad(q, pad)
    kp = np.fft.fftfreq(n + 2 * pad)
    kernel = np.exp(-2 * (np.pi * sigma) ** 2 * (kp[:, None] ** 2 + kp[None, :] ** 2))
    field = np.real(np.fft.ifft2(np.fft.fft2(qp) * kernel))[pad:pad + n, pad:pad + n]
    field = np.clip(field, 0, None)
    return th["ambient_c"] + rise * field / max(float(np.max(field)), 1e-12)


# Pale at ambient, deep red at the hot spot: calm parts of the board read as calm.
_BOARD_RAMP = ["#f4f8fb", "#dbeaf3", "#fde9a8", "#f9b24e", "#ec6a2c", "#c4261d", "#7a0f12"]


def _part_temps(t: dict[str, Any], temp: np.ndarray) -> list[tuple[str, float, float]]:
    n = temp.shape[0]
    out = []
    for c in t["components"]:
        if c["kind"] == "cooling":
            continue
        lay = c["layout"]
        x0, y0 = int(lay["x"] * n), int(lay["y"] * n)
        x1, y1 = max(int((lay["x"] + lay["w"]) * n), x0 + 1), max(int((lay["y"] + lay["h"]) * n), y0 + 1)
        out.append((c["name"], float(temp[y0:y1, x0:x1].max()), float(c["heat_w"])))
    return sorted(out, key=lambda r: -r[1])


def thermal_board(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure
    from matplotlib.patches import FancyBboxPatch, Rectangle

    th = t["thermal"]
    temp = _board_field(t)
    amb, peak = float(th["ambient_c"]), float(th["steady_c"])
    cmap = LinearSegmentedColormap.from_list("gc_board", _BOARD_RAMP)
    vmax = max(peak, float(th["throttle_c"]), amb + 5)
    fig = Figure(figsize=(10.4, 5.0))
    ax = fig.add_axes((0.02, 0.13, 0.52, 0.74))
    cax = fig.add_axes((0.555, 0.13, 0.012, 0.74))
    bx = fig.add_axes((0.735, 0.13, 0.245, 0.74))
    ax.grid(False)
    im = ax.imshow(temp, extent=(0, 1, 1, 0), cmap=cmap, vmin=amb, vmax=vmax, interpolation="bilinear", aspect="auto")
    grid = np.linspace(0, 1, temp.shape[0])
    step = 10.0 if vmax - amb > 25 else 5.0
    levels = np.arange(np.ceil((amb + 1) / step) * step, peak, step)
    if len(levels):
        cs = ax.contour(grid, grid, temp, levels=levels, colors="#5b6470", linewidths=0.6, alpha=0.55)
        ax.clabel(cs, fmt="%.0f °C", fontsize=7, colors="#3b424b")
    ax.add_patch(FancyBboxPatch((-0.01, -0.01), 1.02, 1.02, boxstyle="round,pad=0,rounding_size=0.03",
                                fill=False, ec="#2f6b3a", lw=2.0))  # fmt: skip
    cooler = next((c for c in t["components"] if c["kind"] == "cooling"), None)
    for c in t["components"]:
        if c["kind"] == "cooling":
            continue
        lay = c["layout"]
        ax.add_patch(Rectangle((lay["x"], lay["y"]), lay["w"], lay["h"], fill=False, ec="#1f2933", lw=1.0, alpha=0.8))
        hot = temp[int((lay["y"] + lay["h"] / 2) * 199), int((lay["x"] + lay["w"] / 2) * 199)]
        ink = "#ffffff" if (hot - amb) / max(vmax - amb, 1e-9) > 0.62 else "#111111"
        label = c["name"] + (f"\n{c['heat_w']:.1f} W of heat" if c["heat_w"] > 0 else "")
        ax.text(lay["x"] + lay["w"] / 2, lay["y"] + lay["h"] - 0.035, label, ha="center", va="bottom", fontsize=8,
                color=ink, fontweight="bold")  # fmt: skip
        if c["kind"] == "compute" and cooler is not None:
            inset = 0.025
            ax.add_patch(Rectangle((lay["x"] + inset, lay["y"] + inset), lay["w"] - 2 * inset, lay["h"] - 2 * inset,
                                   fill=False, ec=ink, lw=1.0, ls=(0, (4, 3))))  # fmt: skip
            ax.text(lay["x"] + lay["w"] / 2, lay["y"] + lay["h"] + 0.015, f"dashed: {cooler['name']} on the chip ({cooler['capacity']:g} K/W)",
                    ha="center", va="top", fontsize=7.5, color="#1f2933", zorder=5,
                    bbox={"fc": "#ffffff", "ec": "none", "alpha": 0.85, "pad": 1.5})  # fmt: skip
    ax.set_xlim(-0.015, 1.015)
    ax.set_ylim(1.015, -0.015)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    _cbar(fig, im, ax, "", pal, cax=cax)

    parts = _part_temps(t, temp)
    names = [textwrap.shorten(p[0], 22, placeholder="…") for p in parts]
    vals = [p[1] for p in parts]
    y = np.arange(len(parts))
    bx.barh(y, [v - amb for v in vals], left=amb, color=[cmap((v - amb) / max(vmax - amb, 1e-9)) for v in vals],
            edgecolor="#1f2933", linewidth=0.6, height=0.55)  # fmt: skip
    for yi, v in enumerate(vals):
        bx.text(v + 1, yi, f"{v:.0f} °C", va="center", fontsize=8.5, color=pal["ink"])
    bx.axvline(th["throttle_c"], color="#c4261d", lw=1.4, ls="--")
    bx.text(th["throttle_c"], -0.75, f"slows down at {th['throttle_c']:.0f} °C ", color="#c4261d", fontsize=7.5, va="bottom", ha="right")
    bx.axvline(amb, color=pal["muted"], lw=1.0)
    bx.text(amb, len(parts) - 0.3, f" room {amb:.0f} °C", color=pal["muted"], fontsize=7.5, va="top")
    bx.set_yticks(y, names)
    bx.invert_yaxis()
    bx.set_ylim(len(parts) - 0.2, -0.9)
    bx.set_xlim(amb - 2, max(vmax, th["throttle_c"]) + 14)
    bx.set_xlabel("Hottest point (°C)")
    bx.grid(axis="y", visible=False)
    bx.set_title("Each part", fontsize=10.5)
    head = f"Board heat at steady state (°C): chip {peak:.0f} °C in a {amb:.0f} °C room"
    ax.set_title(head, pad=8)
    if th["throttled"]:
        fig.text(0.02, 0.06, f"Held at {peak:.0f} °C by slowing the clock to {th['clock_factor'] * 100:.0f}%; "
                 f"without that it would reach {th['unthrottled_c']:.0f} °C.", fontsize=8.5, color="#c4261d")  # fmt: skip
    return _finish(fig, t, pal, "peak = engine's chip temperature; the spread across the board is illustrative, not CFD")


def thermal_warmup(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.figure import Figure

    th = t["thermal"]
    pts = th["warmup"]
    if not pts:
        raise FigureError("no warm-up curve for this setup")
    xs = np.array([p[0] for p in pts], dtype=float) / 60.0
    ys = np.array([p[1] for p in pts], dtype=float)
    fig = Figure(figsize=(9.6, 4.6))
    ax = fig.add_axes((0.08, 0.19, 0.89, 0.69))
    sns.lineplot(x=xs, y=ys, ax=ax, color=pal["series"][3], linewidth=2.6)
    ax.fill_between(xs, th["ambient_c"], ys, color=pal["series"][1], alpha=0.25)
    ax.axhline(th["ambient_c"], color=pal["muted"], lw=1, ls=":")
    ax.text(xs[-1], th["ambient_c"], f"ambient {th['ambient_c']:.0f} °C ", ha="right", va="bottom", fontsize=8, color=pal["muted"])
    ax.axhline(th["throttle_c"], color=pal["accent"], lw=1.4, ls="--")
    ax.text(xs[0], th["throttle_c"], f" throttle point {th['throttle_c']:.0f} °C", va="bottom", fontsize=8, color=pal["ink"])
    top = max(float(ys.max()), th["throttle_c"]) + 8
    if th["shutdown_c"] < top + 15:
        ax.axhline(th["shutdown_c"], color=pal["ink"], lw=1.4)
        ax.text(xs[0], th["shutdown_c"], f" shutdown {th['shutdown_c']:.0f} °C", va="bottom", fontsize=8, color=pal["ink"])
        top = max(top, th["shutdown_c"] + 6)
    tts = th.get("time_to_throttle_s")
    if tts is not None and tts / 60 <= xs[-1]:
        ax.plot([tts / 60], [th["throttle_c"]], "o", color=pal["ink"], ms=8, mfc=pal["bg"], mew=2)
        ax.annotate(f"throttles after {_fmt_time(tts)}", (tts / 60, th["throttle_c"]), xytext=(12, -22), textcoords="offset points",
                    fontsize=8.5, color=pal["ink"], arrowprops={"arrowstyle": "-", "color": pal["muted"]})  # fmt: skip
    ax.set_ylim(th["ambient_c"] - 4, top)
    ax.set_xlim(0, xs[-1])
    ax.set_xlabel("Minutes at constant load")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Temperature under sustained load (cold start)", pad=10)
    sns.despine(ax=ax)
    return _finish(fig, t, pal, f"cooling: {th['cooling_name']} ({th['r_th_k_per_w']:.2f} K/W)")


def heat_sources(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.figure import Figure

    comps = sorted((c for c in t["components"] if c["heat_w"] > 0), key=lambda c: -c["heat_w"])
    if not comps:
        raise FigureError("no component dissipates heat in this trace")
    names = [c["name"] for c in comps]
    vals = np.array([c["heat_w"] for c in comps])
    fig = Figure(figsize=(9.6, 1.6 + 0.55 * len(comps)))
    ax = fig.add_axes((0.22, 0.2, 0.72, 0.62))
    sns.barplot(x=vals, y=names, ax=ax, hue=names, palette=pal["bar_ramp"](len(comps)), legend=False, orient="h")
    total = float(vals.sum())
    for i, v in enumerate(vals):
        ax.text(v + vals.max() * 0.01, i, f"{v:.2f} W | {100 * v / total:.0f}%", va="center", fontsize=9, color=pal["ink"])
    ax.set_xlim(0, vals.max() * 1.25)
    ax.set_xlabel("Heat dissipated (W)")
    ax.set_title(f"Where the heat comes from: {total:.1f} W in total", pad=10)
    ax.grid(axis="y", visible=False)
    sns.despine(ax=ax, left=True)
    return _finish(fig, t, pal)


# --- energy ---------------------------------------------------------------------------------------


def energy_stage_heatmap(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure

    stages = t["stages"]
    if not stages:
        raise FigureError("this trace has no stages")
    mat = np.zeros((len(stages), len(ENERGY_KEYS)))
    for s_i, s in enumerate(stages):
        for e in t["events"][s["first_event"] : s["last_event"] + 1]:
            for k_i, k in enumerate(ENERGY_KEYS):
                mat[s_i, k_i] += e["energy_mj"][k]
    labels = [f"{s['index'] + 1}. {s['dominant_op']}" for s in stages]
    cmap = LinearSegmentedColormap.from_list("gc_seq", pal["seq_ramp"])
    h = max(1.8 + 0.34 * len(stages), 3.6)
    fig = Figure(figsize=(9.6, h))
    ax = fig.add_axes((0.2, 0.9 / h, 0.62, 1 - 1.8 / h))
    cax = fig.add_axes((0.85, 0.9 / h, 0.02, 1 - 1.8 / h))
    sns.heatmap(mat, ax=ax, cbar_ax=cax, cmap=cmap, annot=len(stages) <= 24, fmt=".2g", annot_kws={"fontsize": 8},
                linewidths=0.8, linecolor=pal["bg"], xticklabels=[ENERGY_LABEL[k] for k in ENERGY_KEYS], yticklabels=labels,
                cbar_kws={"label": "Energy per batch (mJ)"})  # fmt: skip
    cax.yaxis.label.set_color(pal["ink"])
    cax.tick_params(colors=pal["muted"])
    ax.tick_params(axis="x", rotation=0)
    ax.set_title("Energy map: which stage spends which kind of energy", pad=10)
    ax.grid(False)
    return _finish(fig, t, pal)


def energy_split(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.figure import Figure

    eps = t["energy_per_inference"]
    total = sum(eps[k] for k in ENERGY_KEYS) or 1.0
    fig = Figure(figsize=(9.6, 3.4))
    ax = fig.add_axes((0.04, 0.4, 0.92, 0.38))
    left = 0.0
    for i, k in enumerate(ENERGY_KEYS):
        v = eps[k]
        if v <= 0:
            continue
        ax.barh([0], [v], left=left, color=pal["series"][i], edgecolor=pal["bg"], height=0.7,
                label=f"{ENERGY_LABEL[k]}  {v:.3g} mJ ({100 * v / total:.0f}%)")  # fmt: skip
        if v / total > 0.07:
            ax.text(left + v / 2, 0, f"{100 * v / total:.0f}%", ha="center", va="center", fontsize=10, fontweight="bold", color=pal["on_series"][i])
        left += v
    ax.set_xlim(0, total)
    ax.set_yticks([])
    ax.set_xlabel("Energy per input (mJ)")
    ax.grid(False)
    sns.despine(ax=ax, left=True)
    ax.set_title(f"Energy per input, by cause: {total:.3g} mJ", pad=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.4), ncol=3, fontsize=9)
    return _finish(fig, t, pal)


def power_timeline(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.figure import Figure

    ev = [e for e in t["events"] if e["duration_ms"] > 0]
    if not ev:
        raise FigureError("this trace has no timed operators")
    xs: list[float] = []
    ys: list[float] = []
    for e in ev:
        xs += [e["start_ms"], e["start_ms"] + e["duration_ms"]]
        ys += [e["power_w"], e["power_w"]]
    fig = Figure(figsize=(9.6, 4.2))
    ax = fig.add_axes((0.08, 0.2, 0.89, 0.68))
    sns.lineplot(x=xs, y=ys, ax=ax, color=pal["series"][0], linewidth=1.8, sort=False)
    ax.fill_between(xs, 0, ys, color=pal["series"][0], alpha=0.3)
    avg = t["totals"]["average_power_w"]
    ax.axhline(avg, color=pal["ink"], ls="--", lw=1.2)
    ax.text(xs[-1], avg, f"average {avg:.3g} W ", ha="right", va="bottom", fontsize=8.5, color=pal["ink"])
    ax.set_xlim(0, xs[-1])
    ax.set_ylim(0, max(ys) * 1.12)
    ax.set_xlabel("Time within one batch (ms)")
    ax.set_ylabel("Power (W)")
    ax.set_title("Power over the batch", pad=10)
    sns.despine(ax=ax)
    return _finish(fig, t, pal)


# --- memory ---------------------------------------------------------------------------------------


def memory_timeline(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.figure import Figure

    pts = t["memory_timeline"]
    if not pts:
        raise FigureError("this trace has no memory timeline")
    end = t["totals"]["batch_ms"] or pts[-1]["t_ms"] or 1.0
    xs = np.array([p["t_ms"] for p in pts] + [end], dtype=float)
    w = np.array([p["weights_bytes"] for p in pts] + [pts[-1]["weights_bytes"]], dtype=float)
    r = np.array([p["runtime_bytes"] for p in pts] + [pts[-1]["runtime_bytes"]], dtype=float)
    a = np.array([p["activation_bytes"] for p in pts] + [pts[-1]["activation_bytes"]], dtype=float)
    cap = float(pts[0]["capacity_bytes"])
    fig = Figure(figsize=(9.6, 4.8))
    ax = fig.add_axes((0.09, 0.19, 0.88, 0.69))
    ax.stackplot(xs, w, r, a, step="post", colors=[pal["series"][0], pal["series"][1], pal["series"][2]],
                 labels=["Weights", "Runtime and buffers", "Live activations"], edgecolor=pal["bg"], linewidth=0.5)  # fmt: skip
    total = w + r + a
    pk = int(np.argmax(total))
    ax.plot([xs[pk]], [total[pk]], "o", color=pal["ink"], ms=8, mfc=pal["bg"], mew=2)
    ax.annotate(f"peak {_fmt_bytes(total[pk])}", (xs[pk], total[pk]), xytext=(12, 10), textcoords="offset points", fontsize=9, color=pal["ink"])
    top = float(total.max()) * 1.3
    if 0 < cap <= total.max() * 1.6:
        ax.axhline(cap, color=pal["accent"], lw=1.5, ls="--")
        ax.text(xs[-1], cap, f"usable capacity {_fmt_bytes(cap)} ", ha="right", va="bottom", fontsize=9, color=pal["ink"])
        top = max(top, cap * 1.12)
    elif cap > 0:
        ax.text(xs[-1], top * 0.97, f"usable capacity {_fmt_bytes(cap)} ({cap / total.max():.1f}x the peak) is above this scale",
                ha="right", va="top", fontsize=8.5, color=pal["muted"])  # fmt: skip
    ax.set_ylim(0, top)
    ax.set_xlim(0, xs[-1])
    ax.yaxis.set_major_formatter(lambda v, _: _fmt_bytes(v))
    ax.set_xlabel("Time within one batch (ms)")
    ax.set_title("Memory in use over the batch", pad=10)
    ax.legend(loc="upper left", ncol=3, fontsize=9)
    sns.despine(ax=ax)
    return _finish(fig, t, pal)


def tensor_lifetimes(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure

    ops = [e for e in t["events"] if e["kind"] != "idle"]
    tensors = sorted(t["tensors"], key=lambda s: -s["bytes"])[:40]
    if not tensors or not ops:
        raise FigureError("this trace has no tensor lifetimes")
    tensors.sort(key=lambda s: s["producer"])
    n = len(ops)
    mat = np.full((len(tensors), n), np.nan)
    for i, s in enumerate(tensors):
        lo = max(s["producer"], 0)
        hi = min(s["last_use"], n - 1)
        mat[i, lo : hi + 1] = s["bytes"] / 1e6
    cmap = LinearSegmentedColormap.from_list("gc_seq", pal["seq_ramp"])
    h = max(1.8 + 0.22 * len(tensors), 3.6)
    fig = Figure(figsize=(9.6, h))
    ax = fig.add_axes((0.17, 0.9 / h, 0.66, 1 - 1.8 / h))
    cax = fig.add_axes((0.86, 0.9 / h, 0.02, 1 - 1.8 / h))
    step = max(1, n // 12)
    sns.heatmap(mat, ax=ax, cbar_ax=cax, cmap=cmap, mask=np.isnan(mat), linewidths=0.3, linecolor=pal["bg"], cbar_kws={"label": "Tensor size (MB)"},
                yticklabels=[s["id"][:22] for s in tensors], xticklabels=[str(i) if i % step == 0 else "" for i in range(n)])  # fmt: skip
    ax.tick_params(axis="y", labelsize=7)
    ax.tick_params(axis="x", labelsize=8, rotation=0)
    ax.set_xlabel("Operator index (execution order)")
    ax.set_title("Tensor lifetimes: when each large tensor is alive, shaded by size", pad=10)
    cax.yaxis.label.set_color(pal["ink"])
    cax.tick_params(colors=pal["muted"])
    ax.grid(False)
    return _finish(fig, t, pal, f"largest {len(tensors)} tensors")


# --- throughput -----------------------------------------------------------------------------------


def throughput_heatmap(t: dict[str, Any], pal: dict[str, Any]) -> bytes:
    import seaborn as sns
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure

    ops = [e for e in t["events"] if e["kind"] in ("operator", "aggregate") and e["duration_ms"] > 0]
    if not ops:
        raise FigureError("this trace has no timed operators")
    if len(ops) > 60:  # keep the picture legible: the 60 most expensive operators, in order
        keep = {e["index"] for e in sorted(ops, key=lambda e: -e["duration_ms"])[:60]}
        ops = [e for e in ops if e["index"] in keep]
    peak_w = max(max(e["power_w"] for e in ops), 1e-9)
    rows = [
        ("Memory bandwidth used", [e["bandwidth_utilisation"] for e in ops]),
        ("Compute share of time", [e["compute_ms"] / e["duration_ms"] for e in ops]),
        ("Memory share of time", [e["memory_ms"] / e["duration_ms"] for e in ops]),
        ("Overhead share of time", [e["overhead_ms"] / e["duration_ms"] for e in ops]),
        ("Power vs the batch's peak", [e["power_w"] / peak_w for e in ops]),
    ]
    mat = np.clip(np.array([r[1] for r in rows]), 0, 1)
    cmap = LinearSegmentedColormap.from_list("gc_seq", pal["seq_ramp"])
    fig = Figure(figsize=(9.6, 4.4))
    ax = fig.add_axes((0.2, 0.3, 0.62, 0.58))
    cax = fig.add_axes((0.85, 0.3, 0.02, 0.58))
    step = max(1, len(ops) // 14)
    few = len(ops) <= 8
    cut = 26 if few else 14
    sns.heatmap(mat, ax=ax, cbar_ax=cax, cmap=cmap, vmin=0, vmax=1, linewidths=0.4, linecolor=pal["bg"], cbar_kws={"label": "Fraction (0 to 1)"},
                annot=len(ops) <= 14, fmt=".2f", annot_kws={"fontsize": 8},
                yticklabels=[r[0] for r in rows], xticklabels=[ops[i]["name"][:cut] if i % step == 0 else "" for i in range(len(ops))])  # fmt: skip
    ax.tick_params(axis="x", rotation=0 if few else 60, labelsize=8 if few else 7.5)
    ax.tick_params(axis="y", rotation=0, labelsize=9)
    ax.set_title("Throughput map: how hard each operator drives memory and compute", pad=10)
    cax.yaxis.label.set_color(pal["ink"])
    cax.tick_params(colors=pal["muted"])
    ax.grid(False)
    bound = {k: sum(1 for e in ops if e["bound"] == k) for k in ("compute", "memory", "overhead")}
    return _finish(fig, t, pal, f"bound by compute {bound['compute']}, memory {bound['memory']}, overhead {bound['overhead']} operators")


# --- cooling (drawn from a cooling sweep: plan_service.cooling_sweep) ------------------------------


def _sweep_heatmap(fig: Any, ax: Any, cax: Any, mat: np.ndarray, thr: np.ndarray, xt: list[str], yt: list[str],
                   s: dict[str, Any], pal: dict[str, Any]) -> None:  # fmt: skip
    import seaborn as sns
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("gc_board", _BOARD_RAMP)
    labels = np.array([[f"{v:.0f}{'*' if t else ''}" for v, t in zip(r, tr, strict=True)] for r, tr in zip(mat, thr, strict=True)])
    sns.heatmap(mat, ax=ax, cbar_ax=cax, cmap=cmap, vmin=float(min(mat.min(), s["t_throttle_c"] - 40)),
                vmax=float(max(mat.max(), s["t_throttle_c"])), annot=labels, fmt="", annot_kws={"fontsize": 8},
                linewidths=0.4, linecolor=pal["bg"], xticklabels=xt, yticklabels=yt,
                cbar_kws={"label": "Steady temperature (°C)"})  # fmt: skip
    ax.tick_params(axis="y", rotation=0, labelsize=8.5)
    ax.tick_params(axis="x", rotation=0, labelsize=8.5)
    cax.yaxis.label.set_color(pal["ink"])
    cax.tick_params(colors=pal["muted"])
    ax.grid(False)


def _outline(ax: Any, col: int, rows: int) -> None:
    from matplotlib.patches import Rectangle

    ax.add_patch(Rectangle((col, 0), 1, rows, fill=False, ec="#111111", lw=2.0, clip_on=False, zorder=4))


def _mark_fitted(ax: Any, names: list[str]) -> None:
    for lab in ax.get_yticklabels():
        if "(fitted)" in lab.get_text():
            lab.set_fontweight("bold")
            lab.set_color("#111111")


def _safe_ambient(s: dict[str, Any], o: dict[str, Any], j: int) -> tuple[float | None, bool]:
    """The warmest room this option keeps the chip below its throttle point in, at today's input
    rate. Read from the engine's own grid: between the last cool cell and the first throttled one the
    chip temperature rises about one degree per degree of room, so the crossing is interpolated
    there. Returns (ambient, is_lower_bound) and None when even the coolest room throttles."""
    amb = s["ambient_c"]
    temps = [o["temperature_c"][i][j] for i in range(len(amb))]
    thr = [o["throttled"][i][j] for i in range(len(amb))]
    limit = s["t_throttle_c"]
    if thr[0]:
        return None, False
    k = max(i for i, t in enumerate(thr) if not t)
    if k == len(amb) - 1:
        return amb[-1], True
    return min(amb[k] + max(limit - temps[k], 0.0), amb[k + 1]), False


def cooling_headroom(s: dict[str, Any], pal: dict[str, Any]) -> bytes:
    """One bar per cooling option: the warmest room it can run in at today's input rate without the
    chip slowing down. The simplest answer to "is this cooling enough here?"."""
    from matplotlib.figure import Figure

    j = s["load_fraction"].index(1.0)
    here = float(s["project_ambient_c"])
    lo_axis = float(s["ambient_c"][0])
    rows = []
    for o in s["options"]:
        safe, more = _safe_ambient(s, o, j)
        rows.append((o["name"], safe, more, o["r_th_k_per_w"]))
    rows.sort(key=lambda r: -1e9 if r[1] is None else r[1])
    fig = Figure(figsize=(10.4, 1.9 + 0.46 * len(rows)))
    ax = fig.add_axes((0.36, 0.25, 0.56, 0.62))
    y = np.arange(len(rows))
    ok, tight, bad = "#3a8a4f", "#e0a43a", "#c4261d"
    for yi, (_, safe, more, _) in enumerate(rows):
        if safe is None:
            ax.text(lo_axis + 0.3, yi, f"too hot even at {lo_axis:g} °C", va="center", fontsize=8.5, color=bad)
            continue
        colour = ok if safe >= here + 5 else tight if safe >= here else bad
        ax.barh(yi, safe - lo_axis, left=lo_axis, color=colour, height=0.58, edgecolor="none")
        txt = f"{'over ' if more else 'up to '}{safe:.0f} °C"
        ax.text(safe + 0.4, yi, txt, va="center", fontsize=8.5, color=pal["ink"])
    ax.axvline(here, color="#111111", lw=1.6, ls="--")
    ax.text(here, -0.85, f"your room: {here:g} °C", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax.set_yticks(y, [textwrap.shorten(r[0], 40, placeholder="…") + f" ({r[3]:.2g} K/W)" for r in rows])
    _mark_fitted(ax, [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_ylim(len(rows) - 0.4, -1.1)
    ax.set_xlim(lo_axis, max(float(s["ambient_c"][-1]), here) + 8)
    ax.set_xlabel("Warmest room it can run in without slowing down (°C)")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"How warm a room can each cooling option handle at {s['fps'][j]:g} inputs/s?", pad=10)
    fig.text(0.36, 0.09, "green: at least 5 °C to spare   amber: less than 5 °C to spare   red: slows down in your room",
             fontsize=8, color=pal["muted"])  # fmt: skip
    return _finish(fig, s, pal, "read from the engine's ambient sweep, interpolated between grid points | not CFD")


def cooling_heatmap(s: dict[str, Any], pal: dict[str, Any]) -> bytes:
    """Cooling option x ambient at the project's input rate."""
    from matplotlib.figure import Figure

    j = s["load_fraction"].index(1.0)
    cols = [i for i, a in enumerate(s["ambient_c"]) if a % 5 == 0]  # every 5 °C keeps the labels legible
    names = [textwrap.shorten(o["name"], 36, placeholder="…") for o in s["options"]]
    mat = np.array([[o["temperature_c"][i][j] for i in cols] for o in s["options"]])
    thr = np.array([[o["throttled"][i][j] for i in cols] for o in s["options"]])
    fig = Figure(figsize=(9.6, 1.6 + 0.42 * len(names)))
    ax = fig.add_axes((0.27, 0.22, 0.6, 0.66))
    cax = fig.add_axes((0.9, 0.22, 0.018, 0.66))
    _sweep_heatmap(fig, ax, cax, mat, thr, [f"{s['ambient_c'][i]:g}" for i in cols], names, s, pal)
    amb = [s["ambient_c"][i] for i in cols]
    if s["project_ambient_c"] in amb:
        _outline(ax, col=amb.index(s["project_ambient_c"]), rows=len(names))
    _mark_fitted(ax, names)
    ax.set_xlabel("Room (ambient) temperature (°C)")
    ax.set_title(f"Chip temperature for each cooling option at {s['fps'][j]:g} inputs/s", pad=10)
    return _finish(fig, s, pal, f"* = too hot: the chip slows down to hold {s['t_throttle_c']:g} °C | boxed column = your room | "
                   "analytical sweep, not CFD")


def mode_cooling_heatmap(s: dict[str, Any], pal: dict[str, Any]) -> bytes:
    """Power mode x cooling option at the project's ambient and input rate."""
    from matplotlib.figure import Figure

    names = [textwrap.shorten(o["name"], 36, placeholder="…") for o in s["options"]]
    mat = np.array([[m["temperature_c"] for m in o["modes"]] for o in s["options"]])
    thr = np.array([[m["throttled"] for m in o["modes"]] for o in s["options"]])
    fig = Figure(figsize=(8.4, 1.6 + 0.42 * len(names)))
    ax = fig.add_axes((0.3, 0.22, 0.55, 0.66))
    cax = fig.add_axes((0.88, 0.22, 0.02, 0.66))
    _sweep_heatmap(fig, ax, cax, mat, thr, s["power_modes"], names, s, pal)
    _mark_fitted(ax, names)
    ax.set_xlabel("Power mode")
    ax.set_title(f"Chip temperature for each power mode and cooling option in a {s['project_ambient_c']:g} °C room", pad=10)
    return _finish(fig, s, pal, f"* = too hot: the chip slows down to hold {s['t_throttle_c']:g} °C | "
                   f"{s.get('modes_provenance', 'analytical')}")


def cooling_surfaces(s: dict[str, Any], pal: dict[str, Any]) -> bytes:
    """One 3D surface per cooling option: steady temperature over ambient x input rate, with the
    throttle plane. Where the surface meets the plane, the chip slows down to hold its limit."""
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure

    opts = s["options"][:6]
    cols = 3 if len(opts) > 2 else len(opts)
    rows = (len(opts) + cols - 1) // cols
    fig = Figure(figsize=(4.2 * cols, 3.7 * rows + 0.5))
    cmap = LinearSegmentedColormap.from_list("gc_heat", pal["heat_ramp"])
    amb = np.array(s["ambient_c"])
    fps = np.array(s["fps"])
    xx, yy = np.meshgrid(fps, amb)
    lo = min(float(np.min(o["temperature_c"])) for o in opts)
    hi = max(max(float(np.max(o["temperature_c"])) for o in opts), s["t_throttle_c"])
    for i, o in enumerate(opts):
        ax = fig.add_subplot(rows, cols, i + 1, projection="3d")
        zz = np.array(o["temperature_c"])
        surf = ax.plot_surface(xx, yy, zz, cmap=cmap, vmin=lo, vmax=hi, linewidth=0, antialiased=True,
                               rstride=1, cstride=1, shade=True)
        ax.plot_wireframe(xx, yy, zz, color="#222222", linewidth=0.15, alpha=0.35, rstride=3, cstride=2)
        ax.plot_surface(xx, yy, np.full_like(zz, s["t_throttle_c"]), color="#7f7f7f", alpha=0.18, linewidth=0)
        ax.contourf(xx, yy, zz, zdir="z", offset=lo - 2, cmap=cmap, vmin=lo, vmax=hi, alpha=0.6, levels=12)
        ax.set_zlim(lo - 2, hi + 2)
        ax.set_xlabel("Inputs/s", fontsize=8, labelpad=2)
        ax.set_ylabel("Ambient °C", fontsize=8, labelpad=2)
        ax.set_zlabel("°C", fontsize=8, labelpad=0)
        ax.tick_params(labelsize=7, pad=0)
        share = float(np.mean(np.array(o["throttled"])))
        name = textwrap.shorten(o["name"], 44, placeholder="…")
        ax.set_title(f"{name}\n{o['r_th_k_per_w']:.2f} K/W, throttles in {share:.0%} of the grid", fontsize=9, loc="center")
        ax.view_init(elev=24, azim=-128)
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_facecolor(pal["bg"])
            pane.set_edgecolor(pal["line"])
    fig.suptitle(f"Temperature surfaces per cooling option (plane = throttle point {s['t_throttle_c']:g} °C)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")  # fmt: skip
    fig.subplots_adjust(left=0.02, right=0.9, top=0.86, bottom=0.08, wspace=0.08, hspace=0.25)
    _cbar(fig, surf, None, "Steady temperature (°C)", pal, cax=fig.add_axes((0.925, 0.2, 0.014, 0.6)))
    return _finish(fig, s, pal, "analytical sweep, steady state; not CFD")


SWEEP_FIGURES: dict[str, Callable[[dict[str, Any], dict[str, Any]], bytes]] = {
    "cooling-headroom": cooling_headroom,
    "cooling-heatmap": cooling_heatmap,
    "mode-cooling-heatmap": mode_cooling_heatmap,
    "cooling-surfaces": cooling_surfaces,
}


def render_sweep(name: str, sweep: dict[str, Any], theme: str = "light") -> bytes:
    fn = SWEEP_FIGURES.get(name)
    if fn is None:
        raise KeyError(name)
    import matplotlib

    matplotlib.use("Agg")
    import seaborn as sns

    pal = PALETTES["dark" if theme == "dark" else "light"]
    with _LOCK, matplotlib.rc_context(_style(pal)):  # type: ignore[arg-type]
        sns.set_theme(rc=_style(pal))
        if not sweep.get("options"):
            raise FigureError("no cooling options to compare for this device")
        return fn(sweep, pal)


FIGURES: dict[str, Callable[[dict[str, Any], dict[str, Any]], bytes]] = {
    "thermal-board": thermal_board,
    "thermal-warmup": thermal_warmup,
    "heat-sources": heat_sources,
    "energy-stage-heatmap": energy_stage_heatmap,
    "energy-split": energy_split,
    "power-timeline": power_timeline,
    "memory-timeline": memory_timeline,
    "tensor-lifetimes": tensor_lifetimes,
    "throughput-heatmap": throughput_heatmap,
}


def render(name: str, trace: dict[str, Any], theme: str = "light") -> bytes:
    """Draw one named figure for a stored trace as PNG bytes."""
    fn = FIGURES.get(name)
    if fn is None:
        raise KeyError(name)
    import matplotlib

    matplotlib.use("Agg")
    import seaborn as sns

    pal = PALETTES["dark" if theme == "dark" else "light"]
    with _LOCK, matplotlib.rc_context(_style(pal)):  # type: ignore[arg-type]
        sns.set_theme(rc=_style(pal))
        try:
            return fn(trace, pal)
        except (KeyError, IndexError, ZeroDivisionError) as e:
            raise FigureError(f"this trace does not have the data for {name}: {e}") from e

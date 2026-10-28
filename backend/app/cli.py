"""`gc`: command line for software engineers and CI.

    gc init                         write an example greencomputer.yaml
    gc check greencomputer.yaml     exit 0 pass, 1 fail, 2 uncertain (with --strict: fail)
    gc analyse greencomputer.yaml   print the advice
    gc layers greencomputer.yaml    where the time goes, per layer
    gc devices                      list the hardware library
    gc parse report.txt             read a Vivado / ST Edge AI / Vela report or a power CSV
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.sdk import (
    EXAMPLE,
    ProjectFileError,
    analyse,
    gate,
    load_project,
    load_snapshot,
    markdown_summary,
    snapshot,
)


def _p(args: argparse.Namespace):
    return load_project(args.file)


def cmd_init(a: argparse.Namespace) -> int:
    path = Path(a.path)
    if path.exists() and not a.force:
        print(f"{path} exists (use --force)", file=sys.stderr)
        return 1
    path.write_text(EXAMPLE, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    p = _p(a)
    res = analyse(p)
    prev = load_snapshot(a.compare) if a.compare else None
    g = gate(p, res, strict=a.strict, previous=prev, tolerance_percent=a.tolerance)
    if a.save:
        Path(a.save).write_text(json.dumps(snapshot(res), indent=2), encoding="utf-8")
    md = markdown_summary(p, res, g)
    if a.markdown:
        Path(a.markdown).write_text(md, encoding="utf-8")
    if a.json:
        print(json.dumps({"status": g.status, "messages": g.messages, "metrics": g.summary}, indent=2))
    else:
        print(md)
    return {"pass": 0, "fail": 1, "uncertain": 1 if a.strict else 2}[g.status]


def cmd_analyse(a: argparse.Namespace) -> int:
    p = _p(a)
    res = analyse(p)
    print(markdown_summary(p, res, gate(p, res)))
    print("\nAdvice:")
    for adv in res.advice:
        print(f"- [{adv.tier}] {adv.title}: {adv.gain} (confidence {adv.confidence}, effort {adv.effort})")
    return 0


def cmd_layers(a: argparse.Namespace) -> int:
    from app.engine.layers import layer_profile

    p = _p(a)
    cfg = p.spec.baseline or analyse(p).baseline.config
    prof = layer_profile(p.model, p.hw, cfg)
    if not prof["has_graph"]:
        print(prof["note"])
        return 0
    print(f"{p.model.name} on {p.hw.name}: {prof['total_ms']:.3f} ms ({cfg.precision}, batch {cfg.batch})")
    print(f"\n{'time ms':>9} {'share':>6}  bound    risk  layer (op)")
    for r in prof["top"]:
        print(f"{r['time_ms']:9.3f} {r['share']:6.1%}  {r['bound']:<8} {r['quant_risk']:.2f}  {r['name']} ({r['op']})")
    print("\nBy operator:")
    for o in prof["by_op"][:8]:
        print(f"  {o['op']:<22} {o['count']:>3} layers  {o['time_ms']:8.3f} ms  {o['share']:6.1%}")
    for h in prof["hints"]:
        print("\n* " + h)
    return 0


def cmd_devices(_: argparse.Namespace) -> int:
    from app.data_loader import load_devices

    for d in sorted(load_devices().values(), key=lambda d: (d.kind, d.name)):
        precs = "/".join(k for k, v in d.peak_tflops.items() if v > 0)
        print(f"{d.id:28s} {d.kind:15s} {precs:14s} {d.name}")
    return 0


def cmd_parse(a: argparse.Namespace) -> int:
    from app.engine.vendor_reports import parse_power_csv, parse_report

    text = Path(a.file).read_text(encoding="utf-8", errors="replace")
    try:
        out = parse_power_csv(text, a.supply_v) if a.tool == "power_csv" else parse_report(text, a.tool)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gc", description="The Green Computer command line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("path", nargs="?", default="greencomputer.yaml")  # noqa: E702
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)  # noqa: E702
    s = sub.add_parser("check")
    s.add_argument("file")
    s.add_argument("--strict", action="store_true", help="treat 'uncertain' as a failure")
    s.add_argument("--json", action="store_true")
    s.add_argument("--markdown", help="also write a PR-comment markdown file")
    s.add_argument("--save", help="write a metrics snapshot to compare against later")
    s.add_argument("--compare", help="a snapshot from an earlier run; fails on regression")
    s.add_argument("--tolerance", type=float, help="allowed energy increase in percent (overrides budgets)")
    s.set_defaults(fn=cmd_check)
    for name, fn in (("analyse", cmd_analyse), ("layers", cmd_layers)):
        s = sub.add_parser(name); s.add_argument("file"); s.set_defaults(fn=fn)  # noqa: E702
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    s = sub.add_parser("parse"); s.add_argument("file")  # noqa: E702
    s.add_argument("--tool", choices=["vivado_power", "vivado_util", "stedgeai", "vela", "power_csv"])
    s.add_argument("--supply-v", type=float); s.set_defaults(fn=cmd_parse)  # noqa: E702
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ProjectFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

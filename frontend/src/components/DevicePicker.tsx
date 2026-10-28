import { useMemo, useState } from "react";
import type { HardwareProfile } from "../types";

export const KIND_LABEL: Record<string, string> = {
  edge_soc: "Edge AI boards and SoCs",
  sbc: "Single-board computers",
  accelerator: "AI accelerators",
  mcu: "Microcontrollers",
  fpga: "FPGAs",
  laptop_soc: "Laptop and PC chips",
  datacenter_gpu: "Data-centre GPUs",
  custom: "Your hardware",
};
const ORDER = ["custom", "edge_soc", "sbc", "accelerator", "mcu", "fpga", "laptop_soc", "datacenter_gpu"];

export function groupOf(d: HardwareProfile): string {
  return d.id.startsWith("custom-") ? "custom" : d.kind;
}

export default function DevicePicker({
  devices,
  value,
  onPick,
}: {
  devices: HardwareProfile[];
  value: string;
  onPick: (d: HardwareProfile) => void;
}) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const m = new Map<string, HardwareProfile[]>();
    for (const d of devices) {
      if (needle && !`${d.name} ${d.vendor ?? ""} ${d.role}`.toLowerCase().includes(needle) && d.id !== value) continue;
      const g = groupOf(d);
      m.set(g, [...(m.get(g) ?? []), d]);
    }
    return ORDER.filter((g) => m.has(g)).map((g) => [g, m.get(g)!] as const);
  }, [devices, q, value]);

  return (
    <div className="picker">
      <input
        type="search"
        value={q}
        placeholder={`Search ${devices.length} devices (name, vendor, e.g. "stm32", "npu", "fpga")`}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Search hardware"
      />
      <select
        value={value}
        onChange={(e) => {
          const d = devices.find((x) => x.id === e.target.value);
          if (d) onPick(d);
        }}
        aria-label="Hardware"
      >
        {groups.map(([g, list]) => (
          <optgroup key={g} label={KIND_LABEL[g] ?? g}>
            {list.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {groups.length === 0 && <p className="hint">No device matches. Use "Describe your own hardware" below.</p>}
    </div>
  );
}

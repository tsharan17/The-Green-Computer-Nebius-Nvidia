import VerifyPanel from "../components/VerifyPanel";
import type { HardwareProfile, ModelProfile } from "../types";

export default function VerifyPage({
  devices,
  models,
  onChanged,
}: {
  devices: HardwareProfile[];
  models: ModelProfile[];
  onChanged: () => void;
}) {
  return (
    <div className="verify-page">
      <section className="panel intro">
        <h2>Check it against reality</h2>
        <p>
          Predictions are only as good as the data behind them. Add real measurements (from your own board, a benchmark
          run or a GPU job) and we compare them with our predictions. If the corrected model predicts held-out
          measurements better, we start using it, and the ranges around every number get tighter. We never apply a
          correction that does not earn its place.
        </p>
      </section>
      <VerifyPanel devices={devices} models={models} onChanged={onChanged} />
    </div>
  );
}

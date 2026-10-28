"""Nebius Jobs launcher: command construction, id/state parsing and result ingestion.

The CLI is replaced by a fake runner, so this verifies OUR code, not Nebius. The real CLI has not
been run (see app/integrations/jobs.py and scripts/check_nebius.py).
"""

import json
import subprocess

import pytest

from app.data_loader import get_device
from app.integrations.jobs import (
    JobsError,
    JobSpec,
    NebiusJobs,
    classify_state,
    extract_result_json,
)
from app.main import create_app
from tests.helpers import AuthedClient

HW = get_device("nebius-l40s")
SPEC = JobSpec("nebius-l40s", "resnet-50", "fp32", (1, 8, 32), 40)


def cp(out="", code=0, err=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr=err)


def make_jobs(replies):
    it = iter(replies)
    return NebiusJobs(runner=lambda cmd: next(it), project_id="project-x", image="img:1")


def test_create_command_uses_only_documented_flags_and_injects_the_bench_files():
    cmd = make_jobs([]).build_create_command(SPEC, HW, "gc-bench-1")
    assert cmd[:4] == ["nebius", "ai", "job", "create"]

    def flag(f):
        return cmd[cmd.index(f) + 1]

    assert flag("--image") == "img:1" and flag("--platform") == "gpu-l40s-d"
    assert flag("--container-command") == "bash"
    assert flag("--args") == "/bench/job_entrypoint.sh resnet-50 fp32 1,8,32 nebius-l40s 40"
    assert flag("--restart-policy") == "never" and flag("--parent-id") == "project-x"
    assert "--async" in cmd and flag("--format") == "json" and "--dry-run" not in cmd
    injected = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--inject-file"]
    assert len(injected) == 4 and all(":/bench/" in x for x in injected)
    assert "--dry-run" in make_jobs([]).build_create_command(SPEC, HW, "n", dry_run=True)


def test_validation_rejects_unsupported_requests():
    j = make_jobs([])
    with pytest.raises(JobsError, match="resnet-50"):
        j.validate(JobSpec("nebius-l40s", "yolov8s", "fp32", (1,)), HW)
    with pytest.raises(JobsError, match="fp32, fp16"):
        j.validate(JobSpec("nebius-l40s", "resnet-50", "int8", (1,)), HW)
    with pytest.raises(JobsError, match="batches"):
        j.validate(JobSpec("nebius-l40s", "resnet-50", "fp32", (0,)), HW)
    nx = get_device("jetson-orin-nx")
    with pytest.raises(JobsError, match="no Nebius GPU platform"):
        j.validate(JobSpec("jetson-orin-nx", "resnet-50", "fp32", (1,)), nx)


def test_submit_reads_the_id_from_the_operation_or_falls_back_to_the_job_list():
    ok = make_jobs([cp(json.dumps({"metadata": {"id": "op-1"}, "resource_id": "job-42"}))])
    name, jid = ok.submit(SPEC, HW)
    assert name.startswith("gc-bench-") and jid == "job-42"

    captured = {}

    def runner(cmd):
        if "create" in cmd:
            captured["name"] = cmd[cmd.index("--name") + 1]
            return cp(json.dumps({"operation": "pending"}))  # no id in the output
        listing = {"items": [{"name": "other", "id": "job-1"},
                             {"name": captured["name"], "id": "job-77"}]}  # fmt: skip
        return cp(json.dumps(listing))

    _, jid2 = NebiusJobs(runner=runner, project_id="p", image="i").submit(SPEC, HW)
    assert jid2 == "job-77"


def test_cli_failures_become_clear_errors():
    bad = make_jobs([cp(code=1, err="permission denied")])
    with pytest.raises(JobsError, match="permission denied"):
        bad.submit(SPEC, HW)


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        ({"status": {"state": "RUNNING"}}, "running"),
        ({"status": {"state": "COMPLETED"}}, "succeeded"),
        ({"state": "FAILED"}, "failed"),
        ({"metadata": {"x": 1}, "status": {"phase": "CANCELLED"}}, "cancelled"),
        ({"status": {"state": "PROVISIONING"}}, "running"),
        ({"nothing": True}, "unknown"),
    ],
)
def test_state_classification_is_tolerant_of_field_layout(raw, state):
    assert classify_state(json.dumps(raw)) == state
    assert classify_state("not json") == "unknown"


def test_result_block_extraction():
    logs = 'GPU: x\nnoise\nGC_RESULT_BEGIN\n{"a": 1}\nGC_RESULT_END\nbye'
    assert json.loads(extract_result_json(logs)) == {"a": 1}
    with pytest.raises(JobsError):
        extract_result_json("job still running")
    with pytest.raises(JobsError):
        extract_result_json("GC_RESULT_BEGIN\n\nGC_RESULT_END")


def test_a_finished_job_is_ingested_once_and_calibrates(tmp_path):
    result = {
        "records": [
            {"device_id": "nebius-l40s", "model_id": "resnet-50", "precision": "fp32", "batch": b,
             "latency_ms_mean": ms, "power_w_avg": w, "power_boundary": "gpu_board",
             "source": "bench_job", "hardware_label": "NVIDIA L40S", "external_id": f"job-{b}"}
            for b, ms, w in ((1, 3.2, 150.0), (8, 14.0, 230.0), (32, 52.0, 290.0), (4, 8.0, 200.0))
        ]
    }  # fmt: skip
    logs = f"GPU: NVIDIA L40S\nGC_RESULT_BEGIN\n{json.dumps(result)}\nGC_RESULT_END\n"
    done = json.dumps({"status": {"state": "COMPLETED"}})
    replies = [
        cp(json.dumps({"resource_id": "job-9"})),  # create
        cp(json.dumps({"status": {"state": "RUNNING"}})),  # get #1
        cp(done),  # get #2
        cp(logs),  # logs
        cp(done),  # get #3 (must not re-ingest)
    ]
    from app.services import benchmark_service as bench

    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'j.db'}")) as c:
        db = c.app.state.session_factory()
        jobs = make_jobs(replies)
        row = bench.submit(db, SPEC, jobs)
        assert row["state"] == "running" and row["job_id"] == "job-9"
        assert bench.refresh(db, row["id"], jobs)["state"] == "running"
        fin = bench.refresh(db, row["id"], jobs)
        assert fin["state"] == "succeeded" and fin["ingested"] and len(fin["measurement_ids"]) == 4
        again = bench.refresh(db, row["id"], jobs)  # terminal + ingested: no duplicates
        assert again["measurement_ids"] == fin["measurement_ids"]
        meas = c.get("/api/v1/measurements?device_id=nebius-l40s").json()
        assert len(meas) == 4 and all(m["source"] == "bench_job" for m in meas)
        db.close()

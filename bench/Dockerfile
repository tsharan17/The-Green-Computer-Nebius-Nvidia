# Benchmark job image for GPU hosts (for example Nebius Serverless Jobs).
#
# STATUS: never built. The CUDA tag and the onnxruntime-gpu / cuDNN pairing below are the usual
# combination but have not been verified; confirm against the ONNX Runtime CUDA compatibility table
# and the GPU type you actually get before relying on it.
ARG BASE_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /bench
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY *.py ./

# Example: python3 reference_models.py --out models && \
#          python3 run_benchmark.py --model models/resnet50.onnx --model-id resnet-50 \
#            --device-id nebius-l40s --hardware-label "NVIDIA L40S" --batches 1,8,32 \
#            --source bench_job --out /tmp/result.json && python3 upload_results.py /tmp/result.json
ENTRYPOINT ["python3"]
CMD ["run_benchmark.py", "--help"]

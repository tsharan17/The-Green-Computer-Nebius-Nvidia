"""Copy a result JSON to S3-compatible object storage (Nebius Object Storage), if configured.

    python upload_results.py results.json

Reads GC_S3_ENDPOINT, GC_S3_BUCKET, GC_S3_KEY_ID and GC_S3_SECRET from the environment. If they
are not set the file is left where it is and the script says so. STATUS: written against the
generic S3 API, never run against Nebius (no account/credentials were available).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    need = ("GC_S3_ENDPOINT", "GC_S3_BUCKET", "GC_S3_KEY_ID", "GC_S3_SECRET")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        print(f"object storage not configured (missing {', '.join(missing)}); result stays at {path}")
        return
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["GC_S3_ENDPOINT"],
        aws_access_key_id=os.environ["GC_S3_KEY_ID"],
        aws_secret_access_key=os.environ["GC_S3_SECRET"],
    )
    key = f"bench-results/{path.name}"
    s3.upload_file(str(path), os.environ["GC_S3_BUCKET"], key)
    print(f"uploaded to s3://{os.environ['GC_S3_BUCKET']}/{key}")


if __name__ == "__main__":
    main()

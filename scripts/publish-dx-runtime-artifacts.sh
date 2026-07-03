#!/usr/bin/env bash
#
# publish-dx-runtime-artifacts.sh
#
# Builds the DX-Runtime install artifacts and uploads them to the public S3
# bucket that the com.deepx.dx-runtime Greengrass component downloads from at
# deploy time (see infra/sources/greengrass-runtime.yaml and
# infra/dx-compiler-greengrass-marketplace.yaml).
#
# Artifacts produced (keys under s3://$BUCKET/$PREFIX/):
#   dxrt-driver-dkms_2.5.0-2_all.deb  NPU Linux driver (DKMS), from dx_rt_npu_linux_driver staging
#   dx_rt.tar.gz                      dx_rt staging branch (Ubuntu 26.04 fix already in-branch)
#   fw.bin                            firmware, from dx_fw staging m1/2.7.0/mdot2
#   dx_stream.tar.gz                  dx_stream main branch
#
# On the device the build/flash order is:
#   dx_rt_npu_linux_driver -> dx_rt -> dx_fw -> dx_stream
#
# Requirements: git (with credentials for the private DEEPX-AI repos), tar,
# curl, and the AWS CLI with write access to the target bucket.
#
# Usage:
#   ./scripts/publish-dx-runtime-artifacts.sh
#   DX_ARTIFACT_BUCKET=my-bucket DX_ARTIFACT_PREFIX=dx-runtime ./scripts/publish-dx-runtime-artifacts.sh
#
set -euo pipefail

BUCKET="${DX_ARTIFACT_BUCKET:-deepx-public-bucket}"
PREFIX="${DX_ARTIFACT_PREFIX:-dx-runtime}"
REGION="${AWS_REGION:-ap-northeast-2}"

DRIVER_REPO="${DX_DRIVER_REPO:-https://github.com/DEEPX-AI/dx_rt_npu_linux_driver.git}"
DRIVER_REF="${DX_DRIVER_REF:-staging}"
DRIVER_DEB_PATH="${DX_DRIVER_DEB_PATH:-release/2.5.0/dxrt-driver-dkms_2.5.0-2_all.deb}"
DRIVER_DEB="$(basename "$DRIVER_DEB_PATH")"
DXRT_REPO="${DX_RT_REPO:-https://github.com/DEEPX-AI/dx_rt.git}"
DXRT_REF="${DX_RT_REF:-staging}"
FW_REPO="${DX_FW_REPO:-https://github.com/DEEPX-AI/dx_fw.git}"
FW_REF="${DX_FW_REF:-staging}"
FW_PATH="${DX_FW_PATH:-m1/2.7.0/mdot2/fw.bin}"
DXSTREAM_REPO="${DX_STREAM_REPO:-https://github.com/DEEPX-AI/dx_stream.git}"
DXSTREAM_REF="${DX_STREAM_REF:-main}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/out"
mkdir -p "$OUT"
cd "$WORK"

# ponytail: shallow full clone just to copy one file out of the driver/fw repos.
# Wasteful vs sparse-checkout, but robust across private repos and git versions;
# switch to `git sparse-checkout` if these repos grow large.
echo "==> [1/4] dx_rt_npu_linux_driver: ${DRIVER_REF}:${DRIVER_DEB_PATH}"
git clone --depth 1 --branch "$DRIVER_REF" "$DRIVER_REPO" driver
cp "driver/${DRIVER_DEB_PATH}" "$OUT/${DRIVER_DEB}"

echo "==> [2/4] dx_rt: ${DXRT_REF} (Ubuntu 26.04 fix already in-branch; no patch)"
git clone --depth 1 --branch "$DXRT_REF" "$DXRT_REPO" dx_rt
rm -rf dx_rt/.git
tar czf "$OUT/dx_rt.tar.gz" dx_rt

echo "==> [3/4] dx_fw: ${FW_REF}:${FW_PATH}"
git clone --depth 1 --branch "$FW_REF" "$FW_REPO" dx_fw
cp "dx_fw/${FW_PATH}" "$OUT/fw.bin"

echo "==> [4/4] dx_stream: ${DXSTREAM_REF}"
git clone --depth 1 --branch "$DXSTREAM_REF" "$DXSTREAM_REPO" dx_stream
rm -rf dx_stream/.git
tar czf "$OUT/dx_stream.tar.gz" dx_stream

echo "==> Uploading to s3://${BUCKET}/${PREFIX}/ (region ${REGION})"
for f in "$DRIVER_DEB" dx_rt.tar.gz fw.bin dx_stream.tar.gz; do
  aws s3 cp "$OUT/$f" "s3://${BUCKET}/${PREFIX}/${f}" --region "$REGION"
  echo "    uploaded ${f}"
done

echo
echo "Done. Component download base URL:"
echo "  https://${BUCKET}.s3.${REGION}.amazonaws.com/${PREFIX}/"

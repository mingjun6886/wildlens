# Container image for the tagging Lambda.
#
# Build from the REPOSITORY ROOT, not this directory, because the image needs
# ml/tagger.py as well as the handlers here. Use ./scripts/build-and-push.sh.
#
# Model weights are NOT baked in: they are fetched from S3 at cold start so that
# swapping a model needs no rebuild. Phase 5 revisits that trade-off.
#
# --------------------------------------------------------------------------
# The install order below is not arbitrary. Every step exists because the
# obvious ordering fails, and each failure is recorded next to its fix.
# --------------------------------------------------------------------------

# --platform is not optional on an Apple Silicon host. Without it pip resolves
# arm64 wheels, the image builds and runs locally, then dies on Lambda with an
# exec-format error that names no cause.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.11

# Several packages in this chain ship source-only distributions and need a
# compiler: ultralytics-yolov5, fastquadtree, contourpy, clipboard, pyqtree.
# Without gcc the build fails on the first of them with a meson error about
# missing cc, which reads as a broken package rather than a missing tool.
RUN yum install -y gcc gcc-c++ && yum clean all

# The base image ships pip 24.0, which is old enough to miss newer wheel tags.
RUN pip install --no-cache-dir --upgrade pip

# --- Step 1: pin the packages that would otherwise compile C from source ----
#
# Left to itself, pip selects the newest release of each. For several of these
# the newest release has no manylinux wheel for cp311, so pip falls back to
# building from source:
#
#   numpy 2.4.6      no cp311 wheel; meson build needs a full toolchain
#   pillow (latest)  source build needs libjpeg and zlib headers, absent here
#   scikit-learn     source build is slow and needs OpenMP
#
# Pinning them to the newest version that does ship a wheel removes the source
# builds entirely. --only-binary=:all: turns any remaining fallback into an
# immediate, legible error instead of a twenty-minute compile that then fails.
RUN pip install --no-cache-dir --only-binary=:all: \
    "numpy==2.2.6" \
    "scipy==1.16.3" \
    "scikit-learn==1.7.2" \
    "pillow==11.3.0" \
    "onnx==1.17.0"

# --- Step 2: CPU-only PyTorch ----------------------------------------------
#
# The default PyPI torch wheel is the CUDA build and drags in roughly 2.5 GB of
# nvidia-* packages: cuBLAS, cuDNN, cuFFT, NCCL, Triton. Lambda has no GPU, so
# every byte of that is dead weight in an image whose pull time is part of cold
# start. The CPU index cuts site-packages from 6.2 GB to 1.8 GB.
#
# This must come AFTER step 1: the PyTorch index carries torch and little else,
# so numpy has to be present already or pip tries to build it from source from
# an index that does not have it.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch torchvision

# --- Step 3: megadetector --------------------------------------------------
#
# Installed without --only-binary because two of its dependencies, clipboard
# and pyqtree, publish source distributions only. Both are pure Python and
# build in a second; the heavy packages were already pinned in step 1, so
# nothing expensive compiles here.
#
# pip prints a dependency-conflict warning at the end of this step. That is
# expected and is resolved by step 4.
RUN pip install --no-cache-dir megadetector

# --- Step 4: restore protobuf ----------------------------------------------
#
# ultralytics-yolov5, pulled in by megadetector, caps protobuf at <=3.20.1.
# Every onnx from 1.13 onward floors it at >=3.20.2. The constraints cannot both
# hold, and the gap is a single patch release.
#
# 3.20.3 is the last of the 3.20 line: it satisfies onnx and exceeds yolov5's
# ceiling by two patch releases containing bug fixes only. Applying it after
# megadetector is what makes it stick, since megadetector's install pulls it
# back down to 3.20.1.
RUN pip install --no-cache-dir --only-binary=:all: "protobuf==3.20.3"

# --- Step 5: onnx2torch, dependencies excluded -----------------------------
#
# --no-deps, or it drags onnx and protobuf back to versions that break step 4.
# The package is never imported by this code, but model.pt unpickles into a
# torch.fx GraphModule that it defines, so the class must be importable for
# torch.load to succeed at all.
RUN pip install --no-cache-dir --no-deps onnx2torch

# --- Step 6: headless OpenCV ------------------------------------------------
#
# megadetector's chain installs opencv-python, which links against libGL, an
# X11 library absent from Lambda. The headless build exposes the same cv2
# module without it. --no-deps so that replacing cv2 does not move numpy and
# break the scipy ABI compiled against it.
RUN pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless

# --- Verification -----------------------------------------------------------
#
# Fail here, where the output is a build log, rather than at the first
# invocation, where diagnosing means reading CloudWatch.
RUN python -c "\
from megadetector.detection.run_detector_batch import load_and_run_detector_batch; \
import torch, onnx, onnx2torch, cv2, numpy, google.protobuf as pb; \
assert not torch.cuda.is_available(), 'CUDA build leaked in'; \
print('verified:', torch.__version__, '| onnx', onnx.__version__, \
      '| protobuf', pb.__version__, '| cv2', cv2.__version__)"

# --- Application code -------------------------------------------------------
#
# LAMBDA_TASK_ROOT is /var/task and is already on sys.path, so everything lands
# flat there and imports need no packaging. That flat layout is what
# ml/tests/conftest.py reproduces locally by adding ml/ to sys.path.
COPY ml/tagger.py   ${LAMBDA_TASK_ROOT}/
COPY ml/labels.txt  ${LAMBDA_TASK_ROOT}/

COPY services/process/app.py          ${LAMBDA_TASK_ROOT}/
COPY services/process/model_loader.py ${LAMBDA_TASK_ROOT}/
COPY services/process/media_utils.py  ${LAMBDA_TASK_ROOT}/

CMD ["app.handler"]

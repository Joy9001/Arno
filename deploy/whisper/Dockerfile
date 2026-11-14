# syntax=docker/dockerfile:1
#
# Native whisper.cpp server for the optional local-stt profile.
#
# The final images copy the prebuilt server and its ggml/Whisper libraries from the commit-tagged
# official CPU and CUDA images, so no compiler or CUDA toolkit is installed. CPU is the default
# target; compose.gpu.yaml selects the CUDA target.
#
# FFmpeg is built once from the official release tarball and shared by both targets. It is an
# LGPL-2.1-or-later build: no --enable-gpl, --enable-version3, or --enable-nonfree, no
# --enable-lib*/autodetected external libraries, and the build stage asserts `ffmpeg -L` reports
# LGPL and that the capture formats Arno accepts still have native decoders. Only the CLI and its
# shared libraries reach the runtime; compilers exist only in the build stage.
#
#   docker build --target cpu -t arno-whisper:1.9.5-cpu deploy/whisper
#   docker build --target gpu -t arno-whisper:1.9.5-gpu deploy/whisper
#
# Artifact pins (the Ubuntu and CUDA base tags follow their vendor security updates):
#   WHISPER_CPP_COMMIT  v1.9.5 (d1be6fde11ac6e0407606b4e42fe72d34add8037)
#   WHISPER_CPU_IMAGE   official amd64 manifest built from that commit
#   WHISPER_GPU_IMAGE   official CUDA amd64 manifest built from that commit; its build includes
#                       compute capability 7.5 for the GTX 1650
#   CUDA_RUNTIME_IMAGE  CUDA 13.0.0 base; required cuBLAS and NCCL libraries are added below
#   FFMPEG_VERSION      9.0.2, official source tarball over HTTPS
#   FFMPEG_SHA256       SHA-256 of ffmpeg-9.0.2.tar.xz

ARG WHISPER_CPU_IMAGE=ghcr.io/ggml-org/whisper.cpp@sha256:b55666895efe883c54651dafca1a2a7cda1d98335905364b270db69c2c9fba56
ARG WHISPER_GPU_IMAGE=ghcr.io/ggml-org/whisper.cpp@sha256:e102dbf4197ef0796f50733b51beae938e7199c857dd315a56aabd066cbf0640
ARG CUDA_RUNTIME_IMAGE=nvidia/cuda:13.0.0-base-ubuntu22.04
ARG UBUNTU_IMAGE=ubuntu:22.04
ARG FFMPEG_VERSION=9.0.2
ARG FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e

# LGPL-only FFmpeg CLI, shared by both runtime targets. A shared build keeps the license story
# simple: the runtime links libav*/libsw* dynamically and ships the LGPL text with the binary.
FROM ${UBUNTU_IMAGE} AS ffmpeg

ARG FFMPEG_VERSION
ARG FFMPEG_SHA256

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl nasm xz-utils \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/ffmpeg
RUN curl -fsSLo ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
  && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
  && tar -xf ffmpeg.tar.xz --strip-components=1 \
  && rm ffmpeg.tar.xz

# --disable-autodetect keeps every external library (x264, x265, libvpx, SDL, ...) out, so nothing
# GPL can be linked in by accident. GPL/version3/nonfree are off by default and asserted below.
RUN ./configure \
    --prefix=/usr/local \
    --disable-autodetect \
    --disable-doc \
    --disable-debug \
    --disable-ffplay \
    --disable-ffprobe \
    --disable-avdevice \
    --enable-shared \
    --disable-static \
  && make -j"$(nproc)" \
  && make install \
  && ldconfig

# Build self-check: LGPL only, no external libraries, and a native decoder/demuxer for every audio
# format the capture endpoint accepts (WebM/Opus, OGG, MP4/AAC, WAV, MP3, FLAC).
RUN set -eux; \
  ffmpeg -hide_banner -L > /usr/local/share/ffmpeg-license.txt; \
  grep -q 'Lesser General Public License' /usr/local/share/ffmpeg-license.txt; \
  grep -q 'version 2.1' /usr/local/share/ffmpeg-license.txt; \
  if grep -qiE 'nonfree|GNU General Public License' /usr/local/share/ffmpeg-license.txt; then \
    echo "FFmpeg is not an LGPL build" >&2; exit 1; \
  fi; \
  ffmpeg -hide_banner -buildconf > /usr/local/share/ffmpeg-buildconf.txt; \
  if grep -qE -- '--enable-(gpl|version3|nonfree|lib)' /usr/local/share/ffmpeg-buildconf.txt; then \
    echo "FFmpeg build enables a GPL or external-library option" >&2; exit 1; \
  fi; \
  ffmpeg -hide_banner -decoders > /tmp/decoders.txt; \
  for codec in opus vorbis aac mp3float flac pcm_s16le; do grep -qE " ${codec} " /tmp/decoders.txt; done; \
  ffmpeg -hide_banner -demuxers > /tmp/demuxers.txt; \
  for format in matroska ogg mov wav mp3 flac; do grep -qE " ${format}" /tmp/demuxers.txt; done

# Collects the server and its shared libraries from anywhere under /app in the pinned image, so the
# runtime COPY cannot silently miss a backend that the upstream CMake build placed in a nested
# directory (the CUDA backend has lived outside build/bin before). The checks fail the build early
# if the expected artifacts are absent.
FROM ${WHISPER_CPU_IMAGE} AS whisper-cpu
RUN mkdir -p /out \
  && find /app \( -name whisper-server -o -name 'libwhisper.so*' -o -name 'libggml*.so*' \) \
    -exec cp -a -t /out/ {} + \
  && test -x /out/whisper-server \
  && ls /out/libggml* /out/libwhisper* >/dev/null

FROM ${WHISPER_GPU_IMAGE} AS whisper-gpu
RUN mkdir -p /out \
  && find /app \( -name whisper-server -o -name 'libwhisper.so*' -o -name 'libggml*.so*' \) \
    -exec cp -a -t /out/ {} + \
  && test -x /out/whisper-server \
  && ls /out/libggml* /out/libwhisper* /out/libggml-cuda* >/dev/null
RUN mkdir -p /cuda-libs \
  && cp -a /usr/local/cuda/lib64/libcublas*.so* /cuda-libs/ \
  && cp -a /usr/lib/x86_64-linux-gnu/libnccl.so* /cuda-libs/

FROM ${UBUNTU_IMAGE} AS cpu

ARG FFMPEG_VERSION
ARG FFMPEG_SHA256

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=whisper-cpu /out/whisper-server /usr/local/bin/whisper-server
COPY --from=whisper-cpu /out/libwhisper.so* /usr/local/lib/
COPY --from=whisper-cpu /out/libggml*.so* /usr/local/lib/
COPY --from=whisper-cpu /app/LICENSE /licenses/whisper.cpp/LICENSE

COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /usr/local/lib/ /usr/local/lib/
COPY --from=ffmpeg /usr/local/share/ffmpeg-license.txt /licenses/ffmpeg/LICENSE
COPY --from=ffmpeg /usr/local/share/ffmpeg-buildconf.txt /licenses/ffmpeg/BUILD
COPY --from=ffmpeg /usr/src/ffmpeg/COPYING.LGPLv2.1 /licenses/ffmpeg/COPYING.LGPLv2.1

RUN set -eux; \
  printf 'FFmpeg %s, LGPL-2.1-or-later\nsource: https://ffmpeg.org/releases/ffmpeg-%s.tar.xz\nsha256: %s\n' \
    "$FFMPEG_VERSION" "$FFMPEG_VERSION" "$FFMPEG_SHA256" > /licenses/ffmpeg/SOURCE; \
  echo /usr/local/lib > /etc/ld.so.conf.d/whisper.conf; \
  ldconfig; \
  groupadd --gid 10001 whisper; \
  useradd --uid 10001 --gid whisper --home-dir /models \
    --no-create-home --shell /usr/sbin/nologin whisper; \
  mkdir -p /models /cache; \
  chown whisper:whisper /models /cache; \
  ffmpeg -hide_banner -version >/dev/null

COPY entrypoint.sh /usr/local/bin/whisper-entrypoint
RUN chmod 0755 /usr/local/bin/whisper-entrypoint

# Shared libraries are installed together and registered with ldconfig above.
WORKDIR /usr/local/lib

USER whisper
ENV STT_MODEL=small
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/whisper-entrypoint"]

FROM ${CUDA_RUNTIME_IMAGE} AS gpu

ARG FFMPEG_VERSION
ARG FFMPEG_SHA256

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=whisper-gpu /out/whisper-server /usr/local/bin/whisper-server
COPY --from=whisper-gpu /out/libwhisper.so* /usr/local/lib/
COPY --from=whisper-gpu /out/libggml*.so* /usr/local/lib/
COPY --from=whisper-gpu /app/LICENSE /licenses/whisper.cpp/LICENSE
# Keep the prebuilt backend's required cuBLAS and NCCL libraries. The base supplies CUDA Runtime;
# the rest of NVIDIA's full runtime image is unnecessary for this server. Copy the directory to
# preserve library symlinks: wildcard COPY would duplicate the large files under each alias.
COPY --from=whisper-gpu /cuda-libs/ /usr/local/lib/
COPY --from=whisper-gpu /usr/share/doc/libnccl2/ /licenses/nccl/

COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /usr/local/lib/ /usr/local/lib/
COPY --from=ffmpeg /usr/local/share/ffmpeg-license.txt /licenses/ffmpeg/LICENSE
COPY --from=ffmpeg /usr/local/share/ffmpeg-buildconf.txt /licenses/ffmpeg/BUILD
COPY --from=ffmpeg /usr/src/ffmpeg/COPYING.LGPLv2.1 /licenses/ffmpeg/COPYING.LGPLv2.1

RUN set -eux; \
  printf 'FFmpeg %s, LGPL-2.1-or-later\nsource: https://ffmpeg.org/releases/ffmpeg-%s.tar.xz\nsha256: %s\n' \
    "$FFMPEG_VERSION" "$FFMPEG_VERSION" "$FFMPEG_SHA256" > /licenses/ffmpeg/SOURCE; \
  echo /usr/local/lib > /etc/ld.so.conf.d/whisper.conf; \
  ldconfig; \
  groupadd --gid 10001 whisper; \
  useradd --uid 10001 --gid whisper --home-dir /models \
    --no-create-home --shell /usr/sbin/nologin whisper; \
  mkdir -p /models /cache; \
  chown whisper:whisper /models /cache; \
  ffmpeg -hide_banner -version >/dev/null

COPY entrypoint.sh /usr/local/bin/whisper-entrypoint
RUN chmod 0755 /usr/local/bin/whisper-entrypoint

# Shared libraries are installed together and registered with ldconfig above.
WORKDIR /usr/local/lib

USER whisper
ENV STT_MODEL=small
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/whisper-entrypoint"]

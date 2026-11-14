# Third-party notices

Arno is built on the following third-party work. These notices ship in the Docker image.

## pgledger

MIT License

Copyright (c) 2025 Paul Gross

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## pgsql-ulid

BSD 3-Clause License

Copyright (c) 2021, Scoville
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## restic

The backup sidecar image ships restic.

BSD 2-Clause License

Copyright (c) 2014-2025, Alexander Neumann and contributors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Statement reader packages

These are installed from PyPI and carry their own license files in the image:

- `pypdfium2` — BSD-3-Clause or Apache-2.0
- `python-calamine` — MIT
- `msoffcrypto-tool` — MIT
- `python-docx` — MIT
- `striprtf` — BSD-3-Clause
- `olefile` — BSD-2-Clause
- `python-multipart` — Apache-2.0

## Python runtime dependencies

Every Python distribution installed by `uv sync` ships its own license file in the image, under
its `.dist-info` directory. The direct dependencies below are the ones Arno chooses; the
audit in `scripts/license_audit.py` covers every transitive package too.

| Package | License |
|---|---|
| `asyncpg` | Apache-2.0 |
| `arize-phoenix-otel` | Apache-2.0 |
| `fastapi` | MIT |
| `holidays` | MIT |
| `httpx` | BSD-3-Clause |
| `httpx2` | BSD-3-Clause |
| `genai-prices` | MIT |
| `msoffcrypto-tool` | MIT |
| `numpy` | BSD-3-Clause (bundled libraries below) |
| `olefile` | BSD |
| `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` | Apache-2.0 |
| `pydantic` | MIT |
| `pydantic-ai`, `pydantic-ai-harness`, `pydantic-evals` | MIT |
| `pypdfium2` | BSD-3-Clause or Apache-2.0 |
| `python-calamine` | MIT |
| `python-dateutil` | BSD-3-Clause or Apache-2.0 |
| `python-docx` | MIT |
| `python-multipart` | Apache-2.0 |
| `pywebpush` | MPL-2.0 |
| `segno` | BSD-3-Clause |
| `striprtf` | BSD-3-Clause |
| `typer` | MIT |
| `tzdata` (Windows only: time-zone data that Linux takes from the system) | Apache-2.0 |
| `uvicorn` | BSD-3-Clause |
| `webauthn` | BSD-3-Clause |

Weak-copyleft libraries are used unmodified: `pywebpush` is MPL-2.0. NumPy's Linux wheel
bundles OpenBLAS and LAPACK (BSD-3-Clause), `libgfortran` (GPL-3.0-or-later WITH
GCC-exception-3.1, the same runtime exception as the base image's `libgcc`), and `libquadmath`
(LGPL-2.1-or-later), all as unmodified shared libraries; their texts are in NumPy's
`LICENSE.txt`.
These are the direct dependency licenses. Arno's own source is MIT (`LICENSE`). The
`python:3.14-slim` base image ships Debian packages under their own licenses, and the database
and Phoenix services run as separate Compose images with their own licenses, listed under
[Compose service images](#compose-service-images); Phoenix uses Elastic License 2.0.

## Web dependencies

The web bundle is built from Vite, React, AI SDK 7, Streamdown, Recharts, Radix UI, Lucide, and
`vite-plugin-pwa`. Their direct licenses are:

| Package | License |
|---|---|
| `@ai-sdk/react`, `ai` | Apache-2.0 |
| `@assistant-ui/ai-sdk`, `@assistant-ui/generative-ui`, `@assistant-ui/react`, `@assistant-ui/react-streamdown`, `assistant-stream` | MIT |
| `@streamdown/code`, `class-variance-authority` | Apache-2.0 |
| `@tailwindcss/vite`, `tailwindcss`, `tailwind-merge`, `tw-animate-css`, `tw-shimmer` | MIT |
| `clsx`, `zod`, `zustand` | MIT |
| `@radix-ui/react-dialog`, `@radix-ui/react-popover`, `@radix-ui/react-tabs` | MIT |
| `@radix-ui/themes` | MIT |
| `@simplewebauthn/browser` | MIT |
| `lucide-react` | ISC |
| `motion` | MIT |
| `qrcode.react` | ISC |
| `react`, `react-dom` | MIT |
| `react-spinners` | MIT |
| `recharts` | MIT |
| `streamdown` | Apache-2.0 |
| `vite-plugin-pwa` | MIT |
| `workbox-precaching` | MIT |

The build writes the complete license text of every resolved web package to
`/THIRD_PARTY_NOTICES.txt`, which the app serves at that path and which ships in the image.
`scripts/license_audit.py` fails the build on a GPL, AGPL, SSPL, Elastic, or unknown license in
the application's Python and web dependencies. It does not audit the base image's Debian
packages or the separate container images.

## Web fonts

The web app self-hosts two fonts, subset to Latin plus the rupee sign, from the Google Fonts
sources. Each ships beside its file in `web/public/fonts/` with its full license text.

| Font | License |
|---|---|
| Bricolage Grotesque (`bricolage-grotesque.woff2`) | SIL Open Font License 1.1 |
| JetBrains Mono (`jetbrains-mono.woff2`) | SIL Open Font License 1.1 |

## Compose service images

These images run as separate Compose services. They are not part of the Arno application
image, and each ships its own license file. Where the image has one project license, the
identifier below was checked against the official `LICENSE` file for the pinned version.

| Image | License |
|---|---|
| `postgres:18.6` (database, Phoenix init, API tools) | PostgreSQL License |
| `arno-phoenix-cost:21.0.0`, based on pinned `arizephoenix/phoenix:21.0` | Elastic License 2.0 (ELv2); source, patch, and license stay in the separate service image |
| `ollama/ollama:0.12.6` (optional `local-llm` profile) | MIT |
| `arno-whisper:1.9.5-cpu` and `arno-whisper:1.9.5-gpu` (optional `local-stt` profile) | MIT for Arno packaging and whisper.cpp; `ffmpeg` LGPL-2.1-or-later, built from source without GPL parts; CUDA libraries retain NVIDIA licenses |
| `ghcr.io/joy9001/arno` (this repository) | MIT for Arno code; bundled dependencies as listed above |
| `ghcr.io/joy9001/arno-backup` (this repository) | MIT for Arno code; `restic` BSD-2-Clause, `jq` MIT |

## Local speech-to-text

The optional `local-stt` profile copies a prebuilt native server and its ggml/Whisper libraries
from the pinned official whisper.cpp images, builds FFmpeg from the official release tarball, then
downloads the weights and VAD model on first start. None of these are part of the Arno application
image.

| Component | License |
|---|---|
| `ghcr.io/ggml-org/whisper.cpp` at commit `d1be6fd` (v1.9.5), CPU and CUDA images | MIT |
| Whisper small and large-v3-turbo GGML weights from `ggerganov/whisper.cpp` at revision `5359861` | MIT (OpenAI Whisper) |
| Silero VAD `ggml-silero-v5.1.2.bin` from `ggml-org/whisper-vad` | MIT (Silero VAD) |
| `nvidia/cuda:13.0.0-base-ubuntu22.04` and cuBLAS libraries copied from the pinned upstream CUDA image | NVIDIA CUDA Toolkit license; full EULA in `/usr/share/doc/cuda-cudart-13-0/copyright`, container license in `/NGC-DL-CONTAINER-LICENSE` |
| NCCL libraries copied from the pinned upstream CUDA image | BSD-3-Clause; package notices ship in `/licenses/nccl/` |
| `ffmpeg` 9.0.2 built from the official `https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz` (SHA-256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`), shared build with `--disable-autodetect` and no `--enable-gpl`, `--enable-version3`, `--enable-nonfree`, or `--enable-lib*` | LGPL-2.1-or-later; `COPYING.LGPLv2.1` and the build configuration ship in `/licenses/ffmpeg/` |

## Base images and build tools

- Python (`python:3.14-slim`) — PSF-2.0, plus Debian packages under their own licenses.
- Node (`node:24-alpine`) and pnpm — MIT, used only in the build stage.
- `uv` — Apache-2.0 or MIT, used only in the build stage.
- `jq` in the backup image — MIT.
- Tailscale is installed on the host, not in the image, under its own license.


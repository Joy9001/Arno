#!/bin/sh
# Selects the GGML weights from STT_MODEL, verifies the model and VAD caches, then execs the
# native whisper.cpp server. The API journals STT_MODEL, and the server ignores the multipart
# model field, so this one value names both the trace label and the loaded file.
set -eu

model="${STT_MODEL:-small}"
case "$model" in
  small)
    model_file=ggml-small.bin
    model_sha=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b
    model_size=487601967
    model_url=https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small.bin
    ;;
  large-v3-turbo)
    model_file=ggml-large-v3-turbo.bin
    model_sha=1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69
    model_size=1624555275
    model_url=https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3-turbo.bin
    ;;
  *)
    echo "STT_MODEL must be small or large-v3-turbo for the local whisper profile (got '$model')" >&2
    exit 1
    ;;
esac

vad_file=ggml-silero-v5.1.2.bin
vad_sha=29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf
vad_size=885098
vad_url=https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v5.1.2.bin

fetch() {
  _url=$1
  _dest=$2
  _sha=$3
  _size=$4
  if [ -f "$_dest" ] && [ "$(stat -c %s "$_dest")" = "$_size" ] \
    && echo "$_sha  $_dest" | sha256sum -c - >/dev/null 2>&1; then
    return
  fi
  _tmp="$_dest.part"
  rm -f "$_tmp"
  echo "Downloading $_url" >&2
  if ! curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$_tmp" "$_url"; then
    rm -f "$_tmp"
    echo "Download failed: $_url" >&2
    exit 1
  fi
  if [ "$(stat -c %s "$_tmp")" != "$_size" ] \
    || ! echo "$_sha  $_tmp" | sha256sum -c - >/dev/null 2>&1; then
    rm -f "$_tmp"
    echo "Checksum or size mismatch: $_url" >&2
    exit 1
  fi
  mv -f "$_tmp" "$_dest"
}

mkdir -p /models
fetch "$model_url" "/models/$model_file" "$model_sha" "$model_size"
fetch "$vad_url" "/models/$vad_file" "$vad_sha" "$vad_size"

# v1.9.5 has no --no-context flag; whisper-server defaults no_context to true, so one request's
# text never carries into the next. --language auto keeps the API's language field optional.
exec whisper-server \
  --model "/models/$model_file" \
  --host 0.0.0.0 \
  --port 8000 \
  --inference-path /v1/audio/transcriptions \
  --language auto \
  --convert \
  --vad \
  --vad-model "/models/$vad_file" \
  --tmp-dir /tmp

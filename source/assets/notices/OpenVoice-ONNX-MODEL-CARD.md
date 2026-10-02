---
license: mit
tags:
  - text-to-speech
  - voice-cloning
  - openvoice
  - onnx
  - audio-to-audio
---

# OpenVoice ONNX Models (v2)

This repository provides **ONNX-format models for OpenVoice V2**, intended for efficient local inference in C#, C++, Python, Rust, and other environments using `ONNX Runtime`.

The original **OpenVoice V2 architecture and model weights are developed by MyShell and the OpenVoice contributors**:

**Upstream project:** [MyShell/OpenVoice](https://github.com/myshell-ai/OpenVoice)

The ONNX models can be used with allocation-conscious inference pipelines, making them suitable for local applications and high-throughput server environments.

## 📦 Repository Contents

* `tone_extract.onnx` — Extracts a 256-dimensional voice fingerprint (tone embedding) from an audio spectrogram.
* `tone_color.onnx` — Transfers voice characteristics from a source embedding to a destination embedding.
* `tone_config.json` — Hyperparameters and structural configuration used by the models.

## 🛠️ Technical Specifications & Tensor Shapes

If you are writing your own inference engine, use the following I/O specifications.

### 1. Tone Extractor (`tone_extract.onnx`)

**Input:**

* `input`: `Float [1, frames, 513]` — Linear magnitude spectrogram of the reference audio.
  Hop length: `256`, window length: `1024`, sample rate: `22050 Hz`.

**Output:**

* `tone_embedding`: `Float [1, 256]` — Extracted voice fingerprint.

### 2. Tone Color Converter (`tone_color.onnx`)

**Inputs:**

* `audio`: `Float [1, 513, frames]` — Linear magnitude spectrogram of the generated base audio. The axes are swapped compared with the extractor input.
* `audio_length`: `Int64 [1]` — Number of spectrogram frames.
* `src_tone`: `Float [1, 256, 1]` — Tone embedding of the source/base voice.
* `dest_tone`: `Float [1, 256, 1]` — Tone embedding of the target voice.
* `tau`: `Float [1]` — OpenVoice temperature parameter. Default: `1.0`. Lower values can reduce stochastic variation during tone-color conversion.

**Output:**

* `converted_audio`: `Float [length]` — Converted audio waveform as mono PCM float samples at `22050 Hz`.

## 🚀 Use Case

The repository is suitable for applications that automatically fetch the required models at startup.

Raw download base URL:

`https://huggingface.co/Hinotsuba/OpenVoice-ONNX-v2/resolve/main/{filename}`

For example, the models are used by **Tsubaki TTS Engine** for local OpenVoice V2 voice cloning without requiring a Python runtime.

## ⚖️ License & Attribution

The original **OpenVoice V2 architecture and model weights** are provided by **MyShell and the OpenVoice contributors** under the **MIT License**.

Original project:

[MyShell/OpenVoice](https://github.com/myshell-ai/OpenVoice)

The ONNX-format model files in this repository are distributed under the same MIT terms.

Free for commercial and non-commercial use subject to the MIT License.

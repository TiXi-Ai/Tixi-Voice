# Model list, licences and hardware requirements

Everything Tixi Voice ships with is free software, and **no model is ever bundled in
the installer** — the app finds or downloads them on demand, so the installer stays
small (well under 110 MB) and you decide what lands on your disk.

Two rules govern this list:

1. **No cloud inference.** Every model here runs locally on your machine.
2. **No unverified Persian claims.** A model is only labelled Persian-capable when it
   was genuinely trained on (or verified against) Persian material. The AI Models page
   shows this per row, and `tixi.models.catalog` carries the same flag.

Persian support legend:

| Flag | Meaning |
| --- | --- |
| `native` | Trained on Persian (Farsi) data specifically. |
| `verified-multilingual` | Multilingual model that provably works for Persian (Whisper's language list includes `fa`). |
| `none` | No Persian capability. Offered for other languages only — Tixi Voice never pretends otherwise. |
| `unknown` | Language-independent DSP; no language claims are made. |

---

## Text to speech — Piper (`ggml` VITS, ONNX Runtime)

Runtime: `piper-tts` + `onnxruntime` from the **Piper engine pack**. The runtime is
GPL-3.0-or-later, which is why it is an optional download you perform yourself and not
part of the installer; the engine pack's licence text is shipped with it.

All voices are CPU-only and need no GPU. 4 GB RAM is enough for any single voice.

| Model id | Voice | Languages | Persian | Licence (voice) | Download | Hardware |
| --- | --- | --- | --- | --- | --- | --- |
| `piper-fa-ir-amir` | Persian — Amir (male, medium) | `fa` | `native` | MIT | 63 MB | CPU, any modern x86-64 |
| `piper-fa-ir-gyro` | Persian — Gyro (male, medium) | `fa` | `native` | MIT | 63 MB | CPU, any modern x86-64 |
| `piper-en-us-lessac` | English (US) — Lessac (medium) | `en` | `none` | MIT | 63 MB | CPU |
| `piper-en-gb-alan` | English (UK) — Alan (medium) | `en` | `none` | MIT | 63 MB | CPU |
| `piper-de-de-thorsten` | German — Thorsten (medium) | `de` | `none` | MIT | 63 MB | CPU |
| `piper-fr-fr-siwis` | French — Siwis (medium) | `fr` | `none` | MIT | 63 MB | CPU |
| `piper-es-es-davefx` | Spanish — DaveFX (medium) | `es` | `none` | MIT | 63 MB | CPU |
| `piper-it-it-riccardo-x_low` | Italian — Riccardo (fast) | `it` | `none` | MIT | 20 MB | CPU |
| `piper-pt-br-faber` | Portuguese (BR) — Faber (medium) | `pt` | `none` | MIT | 63 MB | CPU |
| `piper-nl-nl-mls` | Dutch — MLS (medium) | `nl` | `none` | MIT | 63 MB | CPU |
| `piper-ru-ru-dmitri` | Russian — Dmitri (medium) | `ru` | `none` | MIT | 63 MB | CPU |
| `piper-tr-tr-fettah` | Turkish — Fettah (medium) | `tr` | `none` | MIT | 63 MB | CPU |
| `piper-ar-jo-kareem-low` | Arabic — Kareem (low) | `ar` | `none` | MIT | 24 MB | CPU |
| `piper-zh-cn-huayan` | Chinese — Huayan (medium) | `zh` | `none` | MIT | 63 MB | CPU |
| `piper-uk-ua-ukrainian_tts` | Ukrainian — Ukrainian TTS (medium) | `uk` | `none` | MIT | 63 MB | CPU |

Source: [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) (tag
`v1.0.0`), runtime from [OHF-voice/piper1-gpl](https://github.com/OHF-voice/piper1-gpl)
(GPL-3.0-or-later). Persian voices phonemise through the bundled `espeak-ng` Farsi
voice; feeding them Tixi Voice's diacritized text noticeably improves the Ezafe kasra.

## Speech to text — faster-whisper (CTranslate2)

Runtime: `faster-whisper` + `ctranslate2` + `av` from the **faster-whisper engine
pack** (MIT / BSD-3-Clause). Weights come from
[`Systran/faster-whisper-*`](https://huggingface.co/Systran) and are MIT (faster-whisper)
over Apache-2.0 (OpenAI Whisper weights).

| Model id | Parameters | Download | RAM (int8) | Persian | Hardware notes |
| --- | --- | --- | --- | --- | --- |
| `whisper-tiny` | 39 M | 75 MB | ~400 MB | `verified-multilingual` | Fastest; accuracy on Persian is modest. |
| `whisper-base` | 78 M | 145 MB | ~500 MB | `verified-multilingual` | Quick drafts. |
| `whisper-small` | 244 M | 487 MB | ~1.0 GB | `verified-multilingual` | **Default.** Best quality/speed balance on CPU. |
| `whisper-medium` | 769 M | 1.53 GB | ~2.6 GB | `verified-multilingual` | CPU is slow but usable; GPU advised. |
| `whisper-large-v3` | 1.55 B | 3.09 GB | ~4.7 GB | `verified-multilingual` | GPU strongly recommended (NVIDIA CUDA via `cuDNN`). |
| `whisper-large-v3-turbo` | 809 M | 1.62 GB | ~2.5 GB | `verified-multilingual` | Best large-model option for a laptop GPU. |

Whisper's language table includes `fa`, and `settings.stt.language = "fa"` pins the
decoder so no auto-detection mistakes leak into Persian transcripts. CPU inference
uses `compute_type=int8`; on a CUDA GPU Tixi Voice switches to `float16`.

## Persian diacritization

| Model id | Name | Download | Persian | Licence | Hardware |
| --- | --- | --- | --- | --- | --- |
| `canine-fa-diacritizer` | Persian Diacritizer — CANINE (neural) | 140 MB | `native` | MIT (model), Apache-2.0 (base `google/canine-s`) | CPU, ~180 MB RAM, 3–8 sentences/s on a modern laptop |
| `fa-pronunciation-lexicon` | Persian Pronunciation Lexicon (built-in, approximate) | shipped | `native` | MIT (Tixi Voice) | any CPU |

The neural model is [PedramR/canine-fa-diacritizer](https://huggingface.co/PedramR/canine-fa-diacritizer).
It is **gated on Hugging Face**: you must accept the model's terms on the website and
paste a read token under *AI Models ▸ Persian Diacritization ▸ Access token* before the
download can start. Reported metrics on its 359-sentence Persian test set: character
accuracy 91.7 %, diacritization error rate 8.3 %, macro-F1 0.889. It is weakest on the
word-final Ezafe kasra, poetry, technical prose and dialectal text, and it truncates at
2046 characters per request (Tixi Voice splits longer text for you).

Without that model, `LexiconDiacritizer` (a curated high-frequency pronunciation
lexicon plus morphology rules, ~320 entries, no runtime dependency) keeps the
diacritization workspace usable and is **always labelled approximate** in the UI. Type
corrections in the workspace and they are stored as learned overrides in the
pronunciation dictionary, so coverage grows as you work. It is explicitly not a neural
model and it never invents vowels for words it does not know.

## Built-in (no download)

| Model id | Purpose | Persian | Licence |
| --- | --- | --- | --- |
| `fa-text-normalization` | Persian normalisation: Arabic→Persian letter folding, ZWNJ repair, digit conversion, number verbalisation, sentence splitting | `native` | MIT |
| `tixi-spectral-denoise` | Spectral-gate noise reduction used before transcription and export | `unknown` | MIT |

## Adding your own models

* **Piper voices** — drop any `.onnx` + `.onnx.json` pair into *AI Models ▸ Import*.
  The voice must be a Piper VITS voice; the JSON config is required.
* **Whisper checkpoints** — import a CTranslate2-converted directory
  (`model.bin` + `config.json` + `tokenizer.json` + `vocabulary.*`).
* **Diacritizers** — the ONNX route expects a `model.onnx` plus the matching
  `config.json` (`id2label` map) and, for BPE models, `tokenizer.json`.

Imports are validated by `tixi.models.model_validator` before anything is registered:
missing files, wrong shapes and unreadable configs are reported instead of silently
half-installing a model.

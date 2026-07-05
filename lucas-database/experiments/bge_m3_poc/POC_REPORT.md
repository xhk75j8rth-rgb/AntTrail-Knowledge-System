# BGE-M3 Local Embedding Provider PoC Report

Date: 2026-07-01

Status: success.

## 1. Environment

- Windows version: Microsoft Windows 11 Home Chinese, version `10.0.26100`, build `26100`, 64-bit.
- Python version: `3.11.9`.
- Virtual environment: `.venv-bge-m3`.
- System memory: about 31.2 GiB physical RAM.
- NVIDIA GPU: present, `NVIDIA GeForce RTX 5070 Ti Laptop GPU`, 12227 MiB VRAM, driver `595.71`.
- Other GPU: `AMD Radeon(TM) 610M`.
- torch CUDA available: yes.
- Verified command result:
  - torch: `2.11.0+cu128`
  - `torch.cuda.is_available()`: `True`
  - CUDA device: `NVIDIA GeForce RTX 5070 Ti Laptop GPU`
- Key dependency versions:
  - torch: `2.11.0+cu128`
  - sentence-transformers: `5.6.0`
  - psutil: `7.2.2`

## 2. Model

- Model: `BAAI/bge-m3`.
- Local snapshot:
  `<local-huggingface-cache>\models--BAAI--bge-m3\snapshots\<snapshot-id>`
- Required model weight file: `pytorch_model.bin`.
- Local weight file size: `2,271,145,830` bytes, about 2.12 GiB.
- Download result: completed.
- Embedding backend used in this PoC: `sentence-transformers`.
- `FlagEmbedding.BGEM3FlagModel` is not used by the current PoC scripts because the local `SentenceTransformer` snapshot path loads successfully on this machine.
- Load result: success on CUDA.
- Embedding dimension: `1024`.
- Multilingual coverage in smoke input: Chinese, English, and mixed Chinese/English texts all produced stable 1024-dimensional vectors.

Sample embedding run:

```powershell
.\.venv-bge-m3\Scripts\python.exe experiments\bge_m3_poc\bge_m3_embed.py `
  --input experiments\bge_m3_poc\sample_inputs.json `
  --output experiments\bge_m3_poc\output_sample.json
```

Result:

- `ok`: `true`
- sample count: `10`
- batch size: `12`
- model load elapsed: `3725 ms`
- encode elapsed: `232 ms`
- total one-shot process elapsed: `10890 ms`
- output: `experiments/bge_m3_poc/output_sample.json`

## 3. Performance

Benchmark command:

```powershell
.\.venv-bge-m3\Scripts\python.exe experiments\bge_m3_poc\run_benchmark.py `
  --input experiments\bge_m3_poc\sample_inputs.json `
  --output experiments\bge_m3_poc\benchmark_output.json
```

Benchmark result:

- `ok`: `true`
- embedding dimension: `1024`
- batch size: `12`
- model load elapsed: `4082 ms`
- total benchmark process elapsed: `12298 ms`

| Text count | Encode elapsed | Avg per text | Process RSS before | Process RSS after |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `224 ms` | `224.00 ms` | `1197.2 MiB` | `1731.6 MiB` |
| 10 | `102 ms` | `10.20 ms` | `1731.6 MiB` | `1817.4 MiB` |
| 100 | `465 ms` | `4.65 ms` | `1817.4 MiB` | `1822.7 MiB` |

Interpretation:

- GPU inference is viable for local embedding.
- Cold-start cost is still significant because a one-shot Python process pays import + model load each time.
- Formal integration should use a long-running Python worker or equivalent model cache. Do not load BGE-M3 once per API request.

## 4. Node Calls Python

Node-to-Python command:

```powershell
node experiments\bge_m3_poc\node_call_python_test.mjs `
  --python .\.venv-bge-m3\Scripts\python.exe
```

Result:

- Node spawn result: success, exit code `0`.
- Python payload result: `ok: true`.
- sample count: `10`.
- embedding dimension: `1024`.
- total one-shot elapsed from Python payload: `11981 ms`.
- model load elapsed: `4259 ms`.
- encode elapsed: `362 ms`.
- output: `experiments/bge_m3_poc/node_output_sample.json`.

Node can spawn the Python script, capture stdout/stderr, and read the structured JSON payload.

## 5. Adaptation Recommendation

- Formal integration recommendation: BGE-M3 is viable as the real local embedding provider on this machine, but this PoC intentionally did not change main business code.
- Next integration step: add a provider adapter behind the existing embedding provider boundary, keeping `chunks`, `embedding_jobs`, and `embedding_vectors` as rebuildable derived indexes.
- Runtime shape:
  - use a long-running Python worker or service;
  - load `SentenceTransformer` once at startup;
  - use CUDA when `torch.cuda.is_available()` is true;
  - fallback to CPU with explicit warning and lower batch size;
  - normalize embeddings before storage;
  - batch requests rather than embedding one text per process;
  - allow `BGE_M3_MODEL_DIR` to point at the local snapshot.
- Packaging:
  - do not bundle the 2.12 GiB model weight in Electron by default;
  - treat the model cache and Python venv as local runtime dependencies;
  - document CUDA-enabled PyTorch installation separately from normal Python dependencies.

## 6. Risks

- Cold start: one-shot process runs take about 11-12 seconds even though actual encode time is much lower.
- Memory: process RSS reached about 1.8 GiB during the 100-text benchmark; GPU memory was not separately sampled in the JSON output.
- Dependency pinning: CPU-only PyTorch silently prevents GPU use. This PoC required CUDA-enabled `torch 2.11.0+cu128`.
- Node stderr: model loading may emit progress text on stderr. Production integration should either suppress loader progress or treat stderr as logs unless the process exits non-zero.
- Model size: `pytorch_model.bin` is about 2.12 GiB, so download/resume behavior and local cache validation matter.
- Product integration: replacing `MockEmbeddingProvider` should be a separate step with explicit API/provider tests; this PoC only validates local model feasibility.

## 7. Verification Summary

Completed on 2026-07-01:

- BAAI/bge-m3 weight download: completed.
- CUDA-enabled PyTorch install: completed.
- `torch.cuda.is_available()`: verified `True`.
- `bge_m3_embed.py`: passed.
- `run_benchmark.py`: passed.
- `node_call_python_test.mjs`: passed.
- Main business code: not modified by this PoC continuation.

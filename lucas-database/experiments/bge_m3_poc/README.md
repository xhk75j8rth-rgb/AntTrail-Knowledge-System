# BGE-M3 Local Embedding PoC

This directory is an isolated experiment for validating whether `BAAI/bge-m3` can run in the current Windows local environment.

It does not replace `MockEmbeddingProvider`, does not write to Lucas Database SQLite tables, and does not modify the production indexing flow.

## Setup

```powershell
python -m venv .venv-bge-m3
.\.venv-bge-m3\Scripts\python.exe -m pip install --upgrade pip
.\.venv-bge-m3\Scripts\python.exe -m pip install -r experiments\bge_m3_poc\requirements.txt
```

## Run Sample Embedding

```powershell
.\.venv-bge-m3\Scripts\python.exe experiments\bge_m3_poc\bge_m3_embed.py `
  --input experiments\bge_m3_poc\sample_inputs.json `
  --output experiments\bge_m3_poc\output_sample.json
```

The output keeps only embedding previews, not full 1024-dimensional vectors.

## Run Benchmark

```powershell
.\.venv-bge-m3\Scripts\python.exe experiments\bge_m3_poc\run_benchmark.py `
  --input experiments\bge_m3_poc\sample_inputs.json `
  --output experiments\bge_m3_poc\benchmark_output.json
```

## Node Calls Python Check

```powershell
node experiments\bge_m3_poc\node_call_python_test.mjs `
  --python .\.venv-bge-m3\Scripts\python.exe
```

## Files

- `requirements.txt`: Python dependencies for the PoC.
- `sample_inputs.json`: multilingual sample texts.
- `bge_m3_embed.py`: one-shot embedding script.
- `run_benchmark.py`: simple 1 / 10 / 100 text benchmark.
- `node_call_python_test.mjs`: verifies Node can call the Python script.
- `POC_REPORT.md`: recorded results and integration recommendation.

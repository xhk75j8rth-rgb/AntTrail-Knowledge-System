import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import type { EmbeddingProvider, EmbeddingResult } from './embeddingProvider';

export const bgeM3EmbeddingModel = 'BAAI/bge-m3';
export const bgeM3EmbeddingDim = 1024;

interface BgeM3ProviderOptions {
  pythonPath?: string;
  workerPath?: string;
  modelName?: string;
  modelDir?: string;
  batchSize?: number;
  timeoutMs?: number;
}

interface PendingRequest {
  resolve: (response: BgeM3WorkerResponse) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

interface BgeM3WorkerResponse {
  id: string;
  ok: boolean;
  model?: string;
  model_dir?: string;
  embedding_dim?: number;
  count?: number;
  elapsed_ms?: number;
  model_load_elapsed_ms?: number | null;
  embeddings?: number[][];
  error?: {
    type?: string;
    message?: string;
  };
}

const parsePositiveInt = (value: string | undefined, fallback: number) => {
  const parsed = Number.parseInt(value || '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

const projectRoot = () => path.resolve(__dirname, '..', '..');

const defaultPythonPath = () => {
  const venvDir = path.join(projectRoot(), '.venv-bge-m3');
  return process.platform === 'win32'
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python');
};

const summarizeWorkerLog = (log: string) => {
  const trimmed = log.trim();
  if (!trimmed) {
    return '';
  }
  return trimmed.slice(-2000);
};

export class BgeM3EmbeddingProvider implements EmbeddingProvider {
  embeddingModel = bgeM3EmbeddingModel;
  embeddingDim = bgeM3EmbeddingDim;

  private readonly pythonPath: string;
  private readonly workerPath: string;
  private readonly modelName: string;
  private readonly modelDir?: string;
  private readonly batchSize: number;
  private readonly timeoutMs: number;
  private workerProcess: ChildProcessWithoutNullStreams | null = null;
  private stdoutBuffer = '';
  private stderrBuffer = '';
  private readonly pending = new Map<string, PendingRequest>();

  constructor(options: BgeM3ProviderOptions = {}) {
    this.pythonPath = path.resolve(projectRoot(), options.pythonPath || process.env.LUCAS_BGE_M3_PYTHON || defaultPythonPath());
    this.workerPath = path.resolve(
      projectRoot(),
      options.workerPath || process.env.LUCAS_BGE_M3_WORKER || path.join(__dirname, 'bge_m3_worker.py')
    );
    this.modelName = options.modelName || process.env.LUCAS_BGE_M3_MODEL || bgeM3EmbeddingModel;
    this.modelDir = options.modelDir || process.env.BGE_M3_MODEL_DIR;
    this.batchSize = options.batchSize || parsePositiveInt(process.env.LUCAS_BGE_M3_BATCH_SIZE, 12);
    this.timeoutMs = options.timeoutMs || parsePositiveInt(process.env.LUCAS_BGE_M3_TIMEOUT_MS, 120000);
  }

  async embedBatch(texts: string[]): Promise<EmbeddingResult[]> {
    if (texts.length === 0) {
      return [];
    }

    const response = await this.sendEmbedRequest(texts);
    if (!response.ok) {
      const message = response.error?.message || 'unknown BGE-M3 worker error';
      throw new Error(`BGE-M3 embedding failed: ${message}`);
    }

    const embeddings = response.embeddings;
    if (!Array.isArray(embeddings) || embeddings.length !== texts.length) {
      throw new Error(`BGE-M3 worker returned ${embeddings?.length ?? 0} vectors for ${texts.length} texts`);
    }

    return embeddings.map((embedding, index) => {
      if (!Array.isArray(embedding) || embedding.length !== this.embeddingDim) {
        throw new Error(
          `BGE-M3 worker returned invalid dimension for item ${index}: ${embedding?.length ?? 0}`
        );
      }

      return {
        text: texts[index],
        embedding,
        embeddingModel: response.model || this.embeddingModel,
        embeddingDim: embedding.length
      };
    });
  }

  private sendEmbedRequest(texts: string[]) {
    this.ensureWorker();

    const id = randomUUID();
    const payload = {
      id,
      type: 'embed',
      model: this.modelName,
      texts,
      batch_size: this.batchSize
    };

    return new Promise<BgeM3WorkerResponse>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.stopWorker();
        reject(new Error(`BGE-M3 embedding timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);

      this.pending.set(id, { resolve, reject, timer });
      this.workerProcess?.stdin.write(`${JSON.stringify(payload)}\n`, 'utf8');
    });
  }

  private ensureWorker() {
    if (this.workerProcess) {
      return;
    }

    if (!fs.existsSync(this.pythonPath)) {
      throw new Error(`BGE-M3 python executable not found: ${this.pythonPath}`);
    }
    if (!fs.existsSync(this.workerPath)) {
      throw new Error(`BGE-M3 worker script not found: ${this.workerPath}`);
    }

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
      HF_HUB_DISABLE_PROGRESS_BARS: '1',
      TOKENIZERS_PARALLELISM: 'false'
    };
    if (this.modelDir) {
      env.BGE_M3_MODEL_DIR = this.modelDir;
    }

    this.stdoutBuffer = '';
    this.stderrBuffer = '';
    this.workerProcess = spawn(this.pythonPath, [this.workerPath], {
      cwd: projectRoot(),
      env,
      windowsHide: true
    });

    this.workerProcess.stdout.setEncoding('utf8');
    this.workerProcess.stderr.setEncoding('utf8');
    this.workerProcess.stdout.on('data', (chunk: string) => this.handleStdout(chunk));
    this.workerProcess.stderr.on('data', (chunk: string) => {
      this.stderrBuffer = (this.stderrBuffer + chunk).slice(-4000);
    });
    this.workerProcess.on('error', (error) => this.rejectAll(error));
    this.workerProcess.on('close', (code, signal) => {
      if (this.pending.size > 0) {
        const details = summarizeWorkerLog(this.stderrBuffer);
        const suffix = details ? ` Last stderr: ${details}` : '';
        this.rejectAll(new Error(`BGE-M3 worker exited with code=${code} signal=${signal}.${suffix}`));
      }
      this.workerProcess = null;
    });
  }

  private handleStdout(chunk: string) {
    this.stdoutBuffer += chunk;
    let newlineIndex = this.stdoutBuffer.indexOf('\n');
    while (newlineIndex >= 0) {
      const line = this.stdoutBuffer.slice(0, newlineIndex).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      if (line) {
        this.handleResponseLine(line);
      }
      newlineIndex = this.stdoutBuffer.indexOf('\n');
    }
  }

  private handleResponseLine(line: string) {
    let response: BgeM3WorkerResponse;
    try {
      response = JSON.parse(line) as BgeM3WorkerResponse;
    } catch (error) {
      this.rejectAll(new Error(`BGE-M3 worker emitted invalid JSON: ${line.slice(0, 500)}`));
      return;
    }

    const pending = this.pending.get(response.id);
    if (!pending) {
      return;
    }

    this.pending.delete(response.id);
    clearTimeout(pending.timer);
    pending.resolve(response);
  }

  private rejectAll(error: Error) {
    for (const [id, pending] of this.pending.entries()) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this.pending.delete(id);
    }
    this.stopWorker();
  }

  private stopWorker() {
    if (!this.workerProcess) {
      return;
    }
    this.workerProcess.kill();
    this.workerProcess = null;
  }
}

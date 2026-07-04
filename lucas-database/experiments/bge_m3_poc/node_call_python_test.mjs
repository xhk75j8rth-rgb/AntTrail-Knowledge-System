import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..', '..');

const args = process.argv.slice(2);
const getArg = (name, fallback) => {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) {
    return args[index + 1];
  }
  return fallback;
};

const python = getArg(
  '--python',
  process.platform === 'win32'
    ? path.join(repoRoot, '.venv-bge-m3', 'Scripts', 'python.exe')
    : path.join(repoRoot, '.venv-bge-m3', 'bin', 'python')
);
const input = getArg('--input', path.join(scriptDir, 'sample_inputs.json'));
const output = getArg('--output', path.join(scriptDir, 'node_output_sample.json'));
const embedScript = path.join(scriptDir, 'bge_m3_embed.py');

const child = spawn(
  python,
  [embedScript, '--input', input, '--output', output],
  {
    cwd: repoRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  }
);

let stdout = '';
let stderr = '';

child.stdout.on('data', (chunk) => {
  stdout += chunk.toString();
});

child.stderr.on('data', (chunk) => {
  stderr += chunk.toString();
});

child.on('close', async (code) => {
  const fs = await import('node:fs/promises');
  let payload = null;
  try {
    payload = JSON.parse(await fs.readFile(output, 'utf8'));
  } catch {
    payload = null;
  }

  if (code !== 0) {
    console.error(JSON.stringify({ ok: false, code, payload, stdout, stderr }, null, 2));
    process.exitCode = code || 1;
    return;
  }

  console.log(
    JSON.stringify(
      {
        ok: payload.ok,
        count: payload.count,
        embedding_dim: payload.embedding_dim,
        elapsed_ms: payload.elapsed_ms,
        output,
        stdout: stdout.trim(),
        stderr: stderr.trim()
      },
      null,
      2
    )
  );
});

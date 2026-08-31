import path from "node:path";
import { spawn } from "node:child_process";
import { mkdir, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";

function pad2(n) { return String(n).padStart(2, "0"); }

export function formatRangePart(ms, { safe = false } = {}) {
  const d = new Date(ms);
  const date = `${pad2(d.getUTCDate())}.${pad2(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
  const sep = safe ? "-" : ":";
  return `${date}_${pad2(d.getUTCHours())}${sep}${pad2(d.getUTCMinutes())}`;
}

export function humanRangeLabel(startMs, endMs) {
  return `${formatRangePart(startMs)} - ${formatRangePart(endMs)}`;
}

export function safeRangeLabel(startMs, endMs) {
  return `${formatRangePart(startMs, { safe: true })} - ${formatRangePart(endMs, { safe: true })}`;
}

export function runCommand(command, args, { cwd = undefined, capture = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      stdio: capture ? ["ignore", "pipe", "pipe"] : ["ignore", "inherit", "inherit"],
      windowsHide: true,
    });
    const stdout = [];
    const stderr = [];
    if (capture) {
      child.stdout.on("data", (x) => stdout.push(x));
      child.stderr.on("data", (x) => stderr.push(x));
    }
    child.on("error", (err) => reject(new Error(`Nie udało się uruchomić '${command}': ${err.message}`)));
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") });
      } else {
        reject(new Error(`${command} zakończył się kodem ${code}${capture ? `: ${Buffer.concat(stderr).toString("utf8").trim()}` : ""}`));
      }
    });
  });
}

async function uniqueArchivePath(archivesDir, baseName) {
  let candidate = path.join(archivesDir, `${baseName}.tar.gz`);
  let i = 2;
  while (true) {
    try { await stat(candidate); }
    catch (err) { if (err?.code === "ENOENT") return candidate; throw err; }
    candidate = path.join(archivesDir, `${baseName}_${i}.tar.gz`);
    i++;
  }
}

export async function archiveBatchDir(batchDir, archivesDir) {
  await mkdir(archivesDir, { recursive: true });
  const manifestPath = path.join(batchDir, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  if (!Number.isFinite(manifest.startMs) || !Number.isFinite(manifest.endMs)) {
    throw new Error(`Manifest paczki nie ma poprawnego zakresu czasu: ${manifestPath}`);
  }

  const archivePath = await uniqueArchivePath(archivesDir, safeRangeLabel(manifest.startMs, manifest.endMs));
  const tempPath = `${archivePath}.partial`;
  const parent = path.dirname(batchDir);
  const name = path.basename(batchDir);

  await rm(tempPath, { force: true });
  await runCommand("tar", ["-czf", tempPath, "-C", parent, name]);
  // Pełne przejście po skompresowanym archiwum. Jeśli gzip/tar jest uszkodzony, komenda zwróci błąd.
  const listing = await runCommand("tar", ["-tzf", tempPath], { capture: true });
  if (!listing.stdout.includes("market.jsonl") || !listing.stdout.includes("manifest.json")) {
    await rm(tempPath, { force: true });
    throw new Error(`Archiwum nie zawiera market.jsonl i manifest.json: ${tempPath}`);
  }
  await rename(tempPath, archivePath);
  await rm(batchDir, { recursive: true, force: true });
  return { archivePath, manifest };
}

export async function listArchiveEntries(archivePath) {
  const { stdout } = await runCommand("tar", ["-tzf", archivePath], { capture: true });
  return stdout.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
}

export async function readArchiveManifest(archivePath) {
  const entries = await listArchiveEntries(archivePath);
  const manifestEntry = entries.find((x) => /(^|\/)manifest\.json$/.test(x));
  if (!manifestEntry) throw new Error(`Brak manifest.json w ${archivePath}`);
  const { stdout } = await runCommand("tar", ["-xOzf", archivePath, manifestEntry], { capture: true });
  return JSON.parse(stdout);
}

export async function extractArchive(archivePath, destinationDir) {
  await mkdir(destinationDir, { recursive: true });
  await runCommand("tar", ["-xzf", archivePath, "-C", destinationDir]);
  return destinationDir;
}

export async function writeJsonAtomic(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp`;
  await writeFile(tmp, JSON.stringify(value, null, 2) + "\n", "utf8");
  await rename(tmp, filePath);
}

export async function childDirectories(dir) {
  try {
    return (await readdir(dir, { withFileTypes: true })).filter((e) => e.isDirectory()).map((e) => path.join(dir, e.name));
  } catch (err) {
    if (err?.code === "ENOENT") return [];
    throw err;
  }
}

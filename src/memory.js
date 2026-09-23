import { mkdir, readFile, rename, writeFile, unlink, open, stat, readdir } from 'node:fs/promises';
import { dirname, basename, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { validateInput, validateResponse } from './validator.js';

const emptyMemory = () => ({ version: 1, entries: [] });

function validateMemory(data) {
  if (!data || data.version !== 1 || !Array.isArray(data.entries)) throw new Error('Invalid memory');
  data.entries = data.entries.filter(entry => {
    try {
    validateInput(entry.input);
    validateResponse(entry.result?.answer);
    if (typeof entry.result.source !== 'string' || !entry.result.source.trim()
        || !(entry.result.fallbackReason === null || typeof entry.result.fallbackReason === 'string')
        || typeof entry.at !== 'string' || !Number.isFinite(Date.parse(entry.at))) {
      throw new Error('Invalid memory entry');
    }
    return true;
    } catch { return false; }
  });
  return data;
}

export class JsonMemory {
  constructor(path, { warn = message => console.error(message), maxEntries = 100, maxBackups = 2,
    lockTimeoutMs = 10000, staleLockMs = 30000 } = {}) {
    if (!Number.isInteger(maxEntries) || maxEntries < 1) throw new Error('Invalid maxEntries');
    if (!Number.isInteger(maxBackups) || maxBackups < 1) throw new Error('Invalid maxBackups');
    Object.assign(this, { path: resolve(path), warn, maxEntries, maxBackups, lockTimeoutMs, staleLockMs });
  }

  async load() {
    return this.withLock(() => this.readUnlocked());
  }

  async readUnlocked() {
    let raw;
    try {
      raw = await readFile(this.path, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') return emptyMemory();
      throw error;
    }
    try {
      return validateMemory(JSON.parse(raw));
    } catch {
      // Preserve the damaged file before allowing any replacement.
      await rename(this.path, `${this.path}.corrupt-${randomUUID()}`);
      await this.pruneBackups();
      this.warn('Повреждённая память сохранена в .corrupt-резервной копии; создана пустая память.');
      return emptyMemory();
    }
  }

  async append(input, result) {
    const temp = `${this.path}.${randomUUID()}.tmp`;
    try {
      return await this.withLock(async () => {
      const data = await this.readUnlocked();
      data.entries.push({ at: new Date().toISOString(), input, result });
      data.entries = data.entries.slice(-this.maxEntries);
      validateMemory(data);
      await mkdir(dirname(this.path), { recursive: true });
      await writeFile(temp, JSON.stringify(data, null, 2), { encoding: 'utf8', flag: 'wx' });
      await rename(temp, this.path);
      return true;
      });
    } catch {
      this.warn('Не удалось сохранить память; ответ доступен без сохранения.');
      return false;
    } finally {
      await unlink(temp).catch(() => {});
    }
  }

  async pruneBackups() {
    const dir = dirname(this.path);
    const prefix = `${basename(this.path)}.corrupt-`;
    const files = await Promise.all((await readdir(dir)).filter(name => name.startsWith(prefix))
      .map(async name => ({ path: join(dir, name), time: (await stat(join(dir, name))).mtimeMs })));
    files.sort((a, b) => b.time - a.time || a.path.localeCompare(b.path));
    for (const file of files.slice(this.maxBackups)) await unlink(file.path);
  }

  async withLock(operation) {
    await mkdir(dirname(this.path), { recursive: true });
    const lockPath = `${this.path}.lock`;
    const owner = JSON.stringify({ pid: process.pid, token: randomUUID() });
    const started = Date.now();
    let handle;
    while (!handle) {
      try {
        handle = await open(lockPath, 'wx');
        await handle.writeFile(owner);
      } catch (error) {
        if (handle) { await handle.close(); await unlink(lockPath).catch(() => {}); throw error; }
        if (error.code !== 'EEXIST') throw error;
        // Reaping is serialized too. Never reclaim a lock owned by a live process.
        let reaper;
        try {
          reaper = await open(`${lockPath}.reap`, 'wx');
          const info = await stat(lockPath);
          if (Date.now() - info.mtimeMs > this.staleLockMs) {
            const raw = await readFile(lockPath, 'utf8');
            let alive = false;
            try {
              const pid = JSON.parse(raw).pid;
              if (Number.isInteger(pid) && pid > 0) {
                try { process.kill(pid, 0); alive = true; }
                catch (e) { alive = e.code !== 'ESRCH'; }
              }
            } catch { /* An abandoned, partially written lock. */ }
            if (!alive && await readFile(lockPath, 'utf8') === raw) await unlink(lockPath);
          }
        } catch { /* Another writer/reaper owns the lock or it has just disappeared. */ }
        finally { if (reaper) { await reaper.close(); await unlink(`${lockPath}.reap`).catch(() => {}); } }
        if (Date.now() - started >= this.lockTimeoutMs) throw new Error('Memory lock timeout');
        await delay(20 + Math.floor(Math.random() * 30));
      }
    }
    try { return await operation(); }
    finally {
      await handle.close();
      if (await readFile(lockPath, 'utf8').catch(() => '') === owner) await unlink(lockPath).catch(() => {});
    }
  }
}

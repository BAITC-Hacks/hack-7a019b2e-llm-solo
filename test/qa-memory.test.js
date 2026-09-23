import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, readdir, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JsonMemory } from '../src/memory.js';

// Real files on a real filesystem: an in-memory fake would hide exactly the
// races and rename semantics these tests exist to catch.
async function sandbox(t) {
  const dir = await mkdtemp(join(tmpdir(), 'llm-solo-mem-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const warnings = [];
  return {
    dir,
    warnings,
    memory: (file = 'memory.json', options = {}) =>
      new JsonMemory(join(dir, file), { warn: m => warnings.push(m), ...options }),
    read: async (file = 'memory.json') => JSON.parse(await readFile(join(dir, file), 'utf8')),
    ls: () => readdir(dir),
  };
}

const result = (i = 0, source = 'rules') => ({
  answer: { intent: 'help', reply: `ответ ${i}` }, source, fallbackReason: null,
});

test('пишет запись и читает её обратно', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory();
  assert.equal(await memory.append('привет', result(1)), true);
  const data = await fs.read();
  assert.equal(data.version, 1);
  assert.equal(data.entries.length, 1);
  assert.equal(data.entries[0].input, 'привет');
  assert.ok(Number.isFinite(Date.parse(data.entries[0].at)));
});

test('создаёт недостающий каталог', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory(join('глубоко', 'вложенно', 'memory.json'));
  assert.equal(await memory.append('x', result()), true);
});

test('держит maxEntries, выбрасывая самые старые', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory('memory.json', { maxEntries: 3 });
  for (let i = 0; i < 6; i++) await memory.append(`вход ${i}`, result(i));
  const { entries } = await fs.read();
  assert.equal(entries.length, 3);
  assert.deepEqual(entries.map(e => e.input), ['вход 3', 'вход 4', 'вход 5']);
});

test('отсутствующий файл — это пустая память, а не ошибка', async t => {
  const fs = await sandbox(t);
  assert.deepEqual(await fs.memory().load(), { version: 1, entries: [] });
});

test('битый файл уводится в карантин, а не затирается', async t => {
  const fs = await sandbox(t);
  await writeFile(join(fs.dir, 'memory.json'), '{ это не json');
  assert.equal(await fs.memory().append('после порчи', result()), true);
  const backups = (await fs.ls()).filter(f => f.includes('.corrupt-'));
  assert.equal(backups.length, 1, 'повреждённые данные обязаны сохраниться');
  assert.equal(await readFile(join(fs.dir, backups[0]), 'utf8'), '{ это не json');
});

test('не оставляет .tmp-мусора ни при успехе, ни при провале', async t => {
  const fs = await sandbox(t);
  await fs.memory().append('ок', result());
  await fs.memory(join('каталог-а-не-файл')).append('провал', result());
  await mkdir(join(fs.dir, 'каталог-а-не-файл'), { recursive: true }).catch(() => {});
  assert.equal((await fs.ls()).filter(f => f.includes('.tmp')).length, 0);
});

test('путь указывает на каталог — деградируем, а не падаем', async t => {
  const fs = await sandbox(t);
  await mkdir(join(fs.dir, 'занято'));
  assert.equal(await fs.memory('занято').append('x', result()), false);
});

test('отвергает бессмысленный maxEntries', async t => {
  const fs = await sandbox(t);
  for (const bad of [0, -1, 1.5, NaN, '10', null]) {
    assert.throws(() => fs.memory('m.json', { maxEntries: bad }), /Invalid maxEntries/);
  }
});

test('ДЕФЕКТ-1: параллельные append теряют записи и врут про успех', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory();
  const total = 20;
  const saved = await Promise.all(
    Array.from({ length: total }, (_, i) => memory.append(`вход ${i}`, result(i))),
  );
  const claimed = saved.filter(Boolean).length;
  const { entries } = await fs.read();
  assert.ok(entries.length >= claimed,
    `append() вернул true ${claimed} раз(а), но в файле только ${entries.length} записей — ` +
    'потерянное обновление: каждый вызов читает файл целиком и перезаписывает его, ' +
    'последний rename стирает работу остальных, а вызывающий получает memorySaved: true');
});

test('ДЕФЕКТ-2: одна несовместимая запись уничтожает всю историю', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory();
  for (let i = 0; i < 5; i++) await memory.append(`вход ${i}`, result(i));

  // A future version adds a third source. Today's validator does not know it.
  const data = await fs.read();
  data.entries[2].result.source = 'cache';
  await writeFile(join(fs.dir, 'memory.json'), JSON.stringify(data));

  await memory.append('новый запрос', result(99));
  const { entries } = await fs.read();
  assert.ok(entries.length >= 5,
    `валидация памяти работает по принципу «всё или ничего»: из-за одной незнакомой записи ` +
    `осталось ${entries.length} вместо 6 — вся переписка пользователя стёрта`);
});

test('ДЕФЕКТ-3: повторяющаяся порча плодит .corrupt-копии без ограничений', async t => {
  const fs = await sandbox(t);
  const memory = fs.memory();
  for (let i = 0; i < 5; i++) {
    await writeFile(join(fs.dir, 'memory.json'), `{ порча ${i}`);
    await memory.append(`вход ${i}`, result(i));
  }
  const backups = (await fs.ls()).filter(f => f.includes('.corrupt-'));
  assert.ok(backups.length <= 2,
    `накопилось ${backups.length} .corrupt-копий и ни одна не удаляется: ` +
    'при систематической порче (например, две версии приложения пишут по очереди) ' +
    'каждый запуск оставляет копию памяти на диске');
});

// scripts/wiki-refs.js の回帰テスト。
//
//   node --test scripts/test/wiki-refs.test.mjs
//
// コード参照の解釈は wiki-lint.js と wiki_context.mjs が共有する。ここが緩むと lint が host:port で落ち、
// 締まりすぎるとルート直下のファイルへの参照が黙って無視される。

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { parseCodeRef } from '../wiki-refs.js';

const root = mkdtempSync(join(tmpdir(), 'wiki-refs-test-'));
after(() => rmSync(root, { recursive: true, force: true }));
writeFileSync(join(root, 'CLAUDE.md'), '# x\n');
mkdirSync(join(root, 'example.com'));

test('ルート直下に実在するファイルは、スラッシュも記号名も無くても参照として読む', () => {
  assert.deepEqual(parseCodeRef('CLAUDE.md:12', root), { path: 'CLAUDE.md', line: 12, anchor: undefined });
});

test('スラッシュも記号名も無く、ファイルが実在しないもの(host:port)は参照ではない', () => {
  assert.equal(parseCodeRef('example.org:443', root), null);
  // 同名のディレクトリがあってもファイルではない。
  assert.equal(parseCodeRef('example.com:443', root), null);
});

test('記号名があれば、ルート直下で実在しなくても参照として読む(lint が実在を検査する)', () => {
  assert.deepEqual(parseCodeRef('missing.py:3 main()', root), { path: 'missing.py', line: 3, anchor: 'main()' });
});

test('スラッシュを含むパスは実在を問わず参照として読む', () => {
  assert.deepEqual(parseCodeRef('app/auth.py:1', root), { path: 'app/auth.py', line: 1, anchor: undefined });
});

test('行番号の無いもの・拡張子の無いものは参照ではない', () => {
  assert.equal(parseCodeRef('CLAUDE.md', root), null);
  assert.equal(parseCodeRef('localhost:3000', root), null);
});

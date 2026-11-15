// scripts/wiki-lint.js のコード参照・孤立ページ検査の回帰テスト。
//
//   node --test scripts/test/wiki-lint.test.mjs

import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const LINT = fileURLToPath(new URL('../wiki-lint.js', import.meta.url));
const REFS = fileURLToPath(new URL('../wiki-refs.js', import.meta.url));

const sandbox = mkdtempSync(join(tmpdir(), 'wiki-lint-test-'));
after(() => rmSync(sandbox, { recursive: true, force: true }));

function runLint(name, { pageBody, codeFiles = {}, linkFromOverview = true }) {
  const root = join(sandbox, name);
  mkdirSync(join(root, 'scripts'), { recursive: true });
  mkdirSync(join(root, 'wiki', 'components'), { recursive: true });
  copyFileSync(LINT, join(root, 'scripts', 'wiki-lint.js'));
  copyFileSync(REFS, join(root, 'scripts', 'wiki-refs.js'));

  for (const [rel, content] of Object.entries(codeFiles)) {
    const full = join(root, rel);
    mkdirSync(join(full, '..'), { recursive: true });
    writeFileSync(full, content);
  }

  writeFileSync(
    join(root, 'wiki', 'overview.md'),
    '---\ntype: concept\nsummary: 全体像\nupdated: <YYYY-MM-DD>\nrelated: [auth]\n---\n\n' +
      (linkFromOverview ? '[[auth]] を参照。\n' : '本文リンクなし。\n'),
  );
  writeFileSync(
    join(root, 'wiki', 'components', 'auth.md'),
    `---\ntype: component\nsummary: 認証\nupdated: <YYYY-MM-DD>\nrelated: [overview]\n---\n\n${pageBody}\n`,
  );
  writeFileSync(
    join(root, 'wiki', 'index.md'),
    '# Index\n\n<!-- wiki-index:start -->\n<!-- wiki-index:end -->\n\n## 未作成ページ\n\n_(なし)_\n',
  );

  const gen = spawnSync(process.execPath, [join(root, 'scripts', 'wiki-lint.js'), '--write-index'], {
    cwd: root,
    encoding: 'utf8',
  });
  assert.equal(gen.status, 0, gen.stderr);

  return spawnSync(process.execPath, [join(root, 'scripts', 'wiki-lint.js')], {
    cwd: root,
    encoding: 'utf8',
  });
}

test('記号名が実在し行番号が ±5 行以内なら注意なしで通る', () => {
  const res = runLint('exact', {
    pageBody: '入口は `app/auth.py:2 login()`。',
    codeFiles: { 'app/auth.py': '# header\ndef login():\n    pass\n' },
  });
  assert.equal(res.status, 0, res.stderr);
  assert.doesNotMatch(res.stdout, /行番号がずれている/);
});

test('記号名が実在すれば、行番号が ±5 行を超えてずれていても注意止まり(exit 0)にする', () => {
  const lines = Array.from({ length: 20 }, (_, i) => `# line ${i + 1}`);
  lines[14] = 'def login():';
  const res = runLint('shifted', {
    pageBody: '入口は `app/auth.py:2 login()`。',
    codeFiles: { 'app/auth.py': lines.join('\n') + '\n' },
  });
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /注意: wiki\/components\/auth\.md: コード参照 app\/auth\.py:2 login\(\) の行番号がずれている\(login は 15 行目\)/);
});

test('ファイルが短くなって旧行番号が末尾を超えても、記号名が実在すれば注意止まり(exit 0)にする', () => {
  const res = runLint('shrunk', {
    pageBody: '入口は `app/auth.py:50 login()`。',
    codeFiles: { 'app/auth.py': 'def login():\n    return True\n' },
  });
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /注意: wiki\/components\/auth\.md: コード参照 app\/auth\.py:50 login\(\) の行番号がずれている\(login は 1 行目\)/);
});

test('記号名がファイル内に見つからない場合はエラー(exit 1)にする', () => {
  const res = runLint('missing-symbol', {
    pageBody: '入口は `app/auth.py:1 removed_fn()`。',
    codeFiles: { 'app/auth.py': 'def login():\n    pass\n' },
  });
  assert.equal(res.status, 1);
  assert.match(res.stderr, /コード参照 app\/auth\.py:1 removed_fn\(\) の記号名が app\/auth\.py に見つからない/);
});

test('記号名なしの参照は行番号が範囲内なら注意(exit 0)、範囲外ならエラー(exit 1)にする', () => {
  const ok = runLint('no-anchor-ok', {
    pageBody: '設定は `app/auth.py:2`。',
    codeFiles: { 'app/auth.py': 'x = 1\ny = 2\n' },
  });
  assert.equal(ok.status, 0, ok.stderr);
  assert.match(ok.stdout, /注意: wiki\/components\/auth\.md: コード参照 app\/auth\.py:2 に記号名が無い/);

  const outOfRange = runLint('no-anchor-oob', {
    pageBody: '設定は `app/auth.py:10`。',
    codeFiles: { 'app/auth.py': 'x = 1\ny = 2\n' },
  });
  assert.equal(outOfRange.status, 1);
  assert.match(outOfRange.stderr, /コード参照 app\/auth\.py:10 の行番号がファイルの行数 \(3\) を超えている/);
});

test('参照先ファイルが存在しない場合はエラー(exit 1)にする', () => {
  const res = runLint('missing-file', {
    pageBody: '入口は `app/missing.py:1 login()`。',
  });
  assert.equal(res.status, 1);
  assert.match(res.stderr, /コード参照 app\/missing\.py:1 login\(\) のファイルが存在しない/);
});

test('孤立ページは index.md 以外からリンクが無いときにエラー(exit 1)にする', () => {
  const res = runLint('orphan', {
    pageBody: '本文。',
    linkFromOverview: false,
  });
  assert.equal(res.status, 1);
  assert.match(res.stderr, /孤立ページ\(index\.md 以外のどこからも \[\[リンク\]\] されていない\)/);
});

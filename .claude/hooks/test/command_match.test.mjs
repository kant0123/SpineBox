// .claude/hooks/command_match.mjs と、それを使う check_agy_review.sh / check_wiki_updated.sh の回帰テスト (#51)。
//
//   node --test .claude/hooks/test/command_match.test.mjs
//
// hook は実プロセスとして起動する(Claude Code が実際に呼ぶのと同じ形: 標準入力に JSON)。

import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

import { findInvocations } from '../command_match.mjs';

const HOOKS = fileURLToPath(new URL('..', import.meta.url));
const MERGE = ['gh', 'pr', 'merge'];

const sandbox = mkdtempSync(join(tmpdir(), 'command-match-test-'));
after(() => rmSync(sandbox, { recursive: true, force: true }));

describe('findInvocations: マージを実行するコマンドを見つける', () => {
  const hits = [
    ['素の呼び出し', 'gh pr merge 5 --merge', [['5', '--merge']]],
    ['cd の後ろ', 'cd ../x && gh pr merge 5 --merge', [['5', '--merge']]],
    ['; の後ろ', 'git fetch; gh pr merge 5', [['5']]],
    ['| の後ろ', 'echo 5 | gh pr merge 5', [['5']]],
    ['改行の後ろ', 'git fetch\ngh pr merge 5', [['5']]],
    ['|| の後ろ', 'false || gh pr merge 5', [['5']]],
    ['環境変数の前置き', 'GH_TOKEN=x gh pr merge 5', [['5']]],
    ['sudo / time の前置き', 'time gh pr merge 5', [['5']]],
    ['パス付き・.exe 付き', '"C:/Program Files/GitHub CLI/gh.exe" pr merge 5', [['5']]],
    ['番号が引用符の中', 'gh pr merge "5" --merge', [['5', '--merge']]],
    ['番号が無い', 'gh pr merge --merge', [['--merge']]],
    ['if の中', 'if gh pr merge 5; then echo ok; fi', [['5']]],
    ['サブシェル', '(cd x && gh pr merge 5)', [['5']]],
    ['$(...) の中', 'echo $(gh pr merge 5)', [['5']]],
    ['引用符の中の $(...)', 'echo "$(gh pr merge 5)"', [['5']]],
    ['バッククォート', 'echo `gh pr merge 5`', [['5']]],
    ['bash -c', 'bash -c "gh pr merge 5 --merge"', [['5', '--merge']]],
    ['bash -lc の入れ子', "bash -lc 'cd x && gh pr merge 5'", [['5']]],
    ['eval', 'eval gh pr merge 5', [['5']]],
    ['pwsh -Command', 'pwsh -Command "gh pr merge 5"', [['5']]],
    ['ヒアドキュメント本文の展開', 'cat <<EOF\n$(gh pr merge 5)\nEOF', [['5']]],
    ['ヒアドキュメントの後ろ', 'cat <<EOF\ntext\nEOF\ngh pr merge 5', [['5']]],
    ['CRLF のヒアドキュメントの後ろ', 'cat <<EOF\r\ntext\r\nEOF\r\ngh pr merge 5\r\n', [['5']]],
    ['CRLF の引用符付きヒアドキュメントの後ろ', "cat <<'EOF'\r\ntext\r\nEOF\r\ngh pr merge 5", [['5']]],
    ['<<- のヒアドキュメントの後ろ', 'cat <<-EOF\n\ttext\n\tEOF\ngh pr merge 5', [['5']]],
    ['複数','gh pr merge 5 && gh pr merge 6', [['5'], ['6']]],
    ['bash -c と eval の組み合わせでも重複しない', 'bash -c "eval gh pr merge 5"', [['5']]],
  ];
  for (const [name, command, expected] of hits) {
    test(name, () => assert.deepEqual(findInvocations(command, MERGE), expected));
  }

  const misses = [
    ['issue の本文', 'gh issue create --body "hook (gh pr merge の hook) の話"'],
    ['grep のパターン', 'grep -n "gh pr merge" .claude/hooks/check_agy_review.sh'],
    ['echo の引数', 'echo gh pr merge 5'],
    ['単一引用符の中', "echo 'gh pr merge 5'"],
    ['別のサブコマンド', 'gh pr view 5'],
    ['別のコマンドの前置きだけ', 'gh pr checks 5 --watch'],
    ['コメント', 'echo ok # gh pr merge 5'],
    ['ヒアドキュメント本文', 'cat <<EOF\ngh pr merge 5\nEOF'],
    ['引用符付き区切り語のヒアドキュメント本文の展開', "cat <<'EOF'\n$(gh pr merge 5)\nEOF"],
    ['ファイル名', 'cat gh-pr-merge.md'],
    ['PR 本文を渡す gh pr create', 'gh pr create --title "x" --body "マージは gh pr merge 5 で"'],
    ['別のコマンドの引数に gh', 'git log --grep="gh pr merge"'],
  ];
  for (const [name, command] of misses) {
    test(`対象外: ${name}`, () => assert.deepEqual(findInvocations(command, MERGE), []));
  }

  test('引用符が閉じていなくても例外にならない', () => {
    assert.deepEqual(findInvocations('echo "gh pr merge 5', MERGE), []);
    assert.deepEqual(findInvocations("gh pr merge '5", MERGE), [['5']]);
  });

  test('gh pr create も同じ仕組みで探せる', () => {
    assert.equal(findInvocations('gh pr create --fill', ['gh', 'pr', 'create']).length, 1);
    assert.equal(findInvocations('echo "gh pr create"', ['gh', 'pr', 'create']).length, 0);
  });
});

describe('CLI', () => {
  const cli = (input, ...words) =>
    spawnSync('node', [join(HOOKS, 'command_match.mjs'), ...words], { input, encoding: 'utf8' });

  test('見つかれば exit 0 で呼び出しごとに 1 行', () => {
    const r = cli('cd x && gh pr merge 5 --merge && gh pr merge 6', ...MERGE);
    assert.equal(r.status, 0);
    assert.equal(r.stdout, 'M 5 --merge\nM 6\n');
  });

  test('番号が無くても行は出る', () => {
    const r = cli('gh pr merge', ...MERGE);
    assert.equal(r.status, 0);
    assert.equal(r.stdout, 'M\n');
  });

  test('無ければ exit 1', () => {
    assert.equal(cli('echo hi', ...MERGE).status, 1);
  });

  test('語を渡さなければ exit 2', () => {
    assert.equal(cli('echo hi').status, 2);
  });
});

/** hook に PreToolUse の入力(JSON)を渡して実行する。 */
function runHook(script, command, cwd, extra = {}) {
  return spawnSync('bash', [join(HOOKS, script)], {
    input: JSON.stringify({ tool_name: 'Bash', tool_input: { command } }),
    encoding: 'utf8',
    cwd,
    ...extra,
  });
}

function git(cwd, ...args) {
  const r = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@example.com', ...args], { cwd, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
}

/** review/agy_review.py の代わりに、渡された引数を記録して決まった終了コードで終わるスクリプトを置いたリポジトリ。 */
function makeReviewRepo(name, exitCode) {
  const root = join(sandbox, name);
  mkdirSync(join(root, 'review'), { recursive: true });
  git(root, 'init', '-q');
  writeFileSync(
    join(root, 'review', 'agy_review.py'),
    `import sys\nopen(r'${join(root, 'calls.txt').replace(/\\/g, '/')}', 'a').write(' '.join(sys.argv[1:]) + '\\n')\nsys.exit(${exitCode})\n`,
  );
  return root;
}

// Windows の Python はテキストモードで \r\n を書くので、改行をそろえて比べる。
const calls = (root) =>
  existsSync(join(root, 'calls.txt')) ? readFileSync(join(root, 'calls.txt'), 'utf8').replace(/\r\n/g, '\n') : '';

describe('check_agy_review.sh', () => {
  test('マージしないコマンドは、本文に gh pr merge があっても通す', () => {
    const root = makeReviewRepo('agy-through', 1);
    for (const command of [
      'gh issue create --body "hook (gh pr merge の hook) の話"',
      'grep -n "gh pr merge" .claude/hooks/check_agy_review.sh',
    ]) {
      const r = runHook('check_agy_review.sh', command, root);
      assert.equal(r.status, 0, r.stderr);
    }
    assert.equal(calls(root), '', '判定のスクリプトを呼んでいない');
  });

  test('番号の無いマージは差し止める', () => {
    const root = makeReviewRepo('agy-no-number', 0);
    for (const command of ['gh pr merge --merge', 'cd x && gh pr merge', 'bash -c "gh pr merge --merge"']) {
      const r = runHook('check_agy_review.sh', command, root);
      assert.equal(r.status, 2, command);
      assert.match(r.stderr, /PR 番号が指定されていない/);
    }
    assert.equal(calls(root), '');
  });

  test('番号つきのマージは、その番号で判定して結果に従う', () => {
    const ok = makeReviewRepo('agy-ok', 0);
    assert.equal(runHook('check_agy_review.sh', 'cd ../x && gh pr merge 7 --merge', ok).status, 0);
    assert.equal(calls(ok), '--check --pr 7\n');

    const ng = makeReviewRepo('agy-ng', 1);
    const r = runHook('check_agy_review.sh', 'gh pr merge "#8" --merge', ng);
    assert.equal(r.status, 2);
    assert.match(r.stderr, /差し止めました/);
    assert.equal(calls(ng), '--check --pr 8\n');
  });

  test('マージが複数あれば、すべての番号を判定し、1 つでも通らなければ止める', () => {
    const ok = makeReviewRepo('agy-multi', 0);
    assert.equal(runHook('check_agy_review.sh', 'gh pr merge 5 && gh pr merge 6', ok).status, 0);
    assert.equal(calls(ok), '--check --pr 5\n--check --pr 6\n');

    const ng = makeReviewRepo('agy-multi-ng', 0);
    assert.equal(runHook('check_agy_review.sh', 'gh pr merge 5 && gh pr merge', ng).status, 2);
  });

  test('入力が JSON でなければ、従来の部分一致で止める(フェイルクローズ)', () => {
    const root = makeReviewRepo('agy-raw', 0);
    const r = spawnSync('bash', [join(HOOKS, 'check_agy_review.sh')], { input: 'gh pr merge --merge', encoding: 'utf8', cwd: root });
    assert.equal(r.status, 2);
    const r2 = spawnSync('bash', [join(HOOKS, 'check_agy_review.sh')], { input: 'gh pr merge 9 --merge', encoding: 'utf8', cwd: root });
    assert.equal(r2.status, 0);
    assert.equal(calls(root), '--check --pr 9\n');
  });
});

describe('check_wiki_updated.sh', () => {
  /** origin/main があり、その上にコードだけを変えたコミットがあるリポジトリ(wiki/ の更新が無い PR の状態)。 */
  function makeCodeOnlyRepo(name) {
    const root = join(sandbox, name);
    mkdirSync(root, { recursive: true });
    git(root, 'init', '-q');
    writeFileSync(join(root, 'a.txt'), 'a\n');
    git(root, 'add', 'a.txt');
    git(root, 'commit', '-q', '-m', 'base');
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD');
    writeFileSync(join(root, 'b.cs'), 'class B {}\n');
    git(root, 'add', 'b.cs');
    git(root, 'commit', '-q', '-m', 'code');
    return root;
  }

  test('wiki の更新が無い PR の作成は差し止める', () => {
    const root = makeCodeOnlyRepo('wiki-block');
    const r = runHook('check_wiki_updated.sh', 'cd x && gh pr create --title t --body b', root);
    assert.equal(r.status, 2);
    assert.match(r.stderr, /wiki\/ の更新が含まれていません/);
  });

  test('本文に gh pr create があるだけのコマンドは通す', () => {
    const root = makeCodeOnlyRepo('wiki-through');
    for (const command of [
      'gh issue create --body "PR は gh pr create で作る"',
      'grep -n "gh pr create" .claude/hooks/check_wiki_updated.sh',
    ]) {
      const r = runHook('check_wiki_updated.sh', command, root);
      assert.equal(r.status, 0, r.stderr);
    }
  });

  test('WIKI_SKIP=1 を付ければ通す', () => {
    const root = makeCodeOnlyRepo('wiki-skip');
    assert.equal(runHook('check_wiki_updated.sh', 'WIKI_SKIP=1 gh pr create --title t', root).status, 0);
  });
});

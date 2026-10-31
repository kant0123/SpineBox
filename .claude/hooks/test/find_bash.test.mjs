// find_bash.mjs の回帰テスト (#6): PATH に Git の bin ディレクトリが無い Windows でも bash.exe を見つけられること。
//
//   node --test .claude/hooks/test/find_bash.test.mjs

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { bashCandidates, findBash } from './find_bash.mjs';

const GIT_CMD = 'C:\\Program Files\\Git\\cmd\\git.exe';
const GIT_MINGW = 'C:\\Program Files\\Git\\mingw64\\bin\\git.exe';
const GIT_BASH = 'C:\\Program Files\\Git\\bin\\bash.exe';

describe('bashCandidates', () => {
  test('Git の cmd 直下の git.exe から、bin 直下の bash.exe を導く', () => {
    assert.ok(bashCandidates([GIT_CMD]).includes(GIT_BASH));
  });

  test('Git の mingw64 の bin にある git.exe からも、Git 直下の bin の bash.exe を導く', () => {
    assert.ok(bashCandidates([GIT_MINGW]).includes(GIT_BASH));
  });

  test('重複しない', () => {
    const list = bashCandidates([GIT_CMD, GIT_MINGW]);
    assert.equal(new Set(list).size, list.length);
  });
});

describe('findBash', () => {
  test('Windows で bin が PATH に無くても、git の場所から bash.exe を見つける', () => {
    const found = findBash({ platform: 'win32', locate: () => [GIT_CMD], exists: (p) => p === GIT_BASH });
    assert.equal(found, GIT_BASH);
  });

  test('Windows で候補がどれも無ければ PATH 任せの "bash"', () => {
    assert.equal(findBash({ platform: 'win32', locate: () => [GIT_CMD], exists: () => false }), 'bash');
  });

  test('Windows で git が見つからなくても "bash" に倒れる', () => {
    assert.equal(findBash({ platform: 'win32', locate: () => [], exists: () => true }), 'bash');
  });

  test('Windows 以外は何も探さず "bash"', () => {
    const never = () => assert.fail('探してはいけない');
    assert.equal(findBash({ platform: 'linux', locate: never, exists: never }), 'bash');
  });
});

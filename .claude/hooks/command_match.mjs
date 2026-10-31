#!/usr/bin/env node
// PreToolUse hook (Bash) 用の補助: シェルのコマンド文字列に「このコマンドを実行するもの」が含まれるかを判定する。
//
// 【なぜ要るか】hook は `gh pr merge` を「コマンド文字列に含まれるか」で見ていたため、`gh issue create --body "...gh pr merge..."` や
// `grep "gh pr merge" file` のような、マージしないコマンドまで止めた(#51)。ここでは文字列を最低限シェルとして読み、
// 実際に実行されるコマンド(`&&` `||` `;` `|` `&` 改行 `(` `)` で区切った各部分の先頭)だけを取り出す。
//
// 【読むもの】
// - 引用符('...' "...")の中・コメント・ヒアドキュメントの本文は、コマンドとして数えない
// - 区切りの直後(`cd x && gh pr merge 5` の後ろ)、先頭の `VAR=x`、`sudo`/`time`/`if`/`then` などの前置き、
//   `gh.exe` やパス付きの `gh` も同じ扱い
// - 実行される入れ子は読む: `$(...)`・バッククォート(引用符の中・ヒアドキュメント本文の展開も含む)、
//   `bash -c "..."` / `eval ...` / `pwsh -c ...` の引数
//
// 【フェイルクローズ】読み取りに失敗(想定外の例外)したら exit 2 を返す。呼び出し側は従来の部分一致に戻す
// (止めすぎる側に倒す)。引用符が閉じていない文字列は末尾まで引用符の中として読む。
// 読めないもの(`python -c` 等の別言語から組み立てる呼び出し、`$cmd` のような変数の展開)は、従来の部分一致でも
// 見えなかったか、見えても通せなかったもので、この hook は元から助言に近い防御でしかない(回避手段は置かない方針は変えない)。
//
// 使い方: コマンド文字列を標準入力に渡し、探すコマンドの語を引数に並べる。
//
//   printf '%s' "$cmd" | node command_match.mjs gh pr merge
//
// 見つかった呼び出しごとに `M <サブコマンドより後ろの語を空白で連結>` を 1 行出し、exit 0。無ければ exit 1。

import { fileURLToPath } from 'node:url';

const MAX_DEPTH = 8;
const ENV_ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/;
// コマンド名の前に付けても、後ろの語が実行されるもの
const PREFIXES = new Set([
  '!', '{', 'time', 'command', 'exec', 'builtin', 'nohup', 'sudo', 'env', 'xargs',
  'if', 'then', 'else', 'elif', 'while', 'until', 'do',
]);
// 引数を別のコマンド文字列として実行するもの
const SHELLS = new Set(['bash', 'sh', 'zsh', 'dash', 'ksh', 'eval', 'pwsh', 'powershell', 'cmd']);

function baseName(word) {
  return word.split(/[\\/]/).pop().replace(/\.exe$/i, '').toLowerCase();
}

/** `$(` の直後 start から、対応する `)` の位置を返す。無ければ text.length。 */
function matchParen(text, start) {
  let depth = 1;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (c === '\\') { i++; continue; }
    if (c === "'") {
      const j = text.indexOf("'", i + 1);
      if (j < 0) return text.length;
      i = j;
      continue;
    }
    if (c === '"') {
      for (i++; i < text.length && text[i] !== '"'; i++) if (text[i] === '\\') i++;
      continue;
    }
    if (c === '(') depth++;
    else if (c === ')' && --depth === 0) return i;
  }
  return text.length;
}

/** バッククォートの直後 start から、閉じる位置を返す。無ければ text.length。 */
function matchBacktick(text, start) {
  for (let i = start; i < text.length; i++) {
    if (text[i] === '\\') i++;
    else if (text[i] === '`') return i;
  }
  return text.length;
}

/** 展開(`$(...)` とバッククォート)を含みうる本文から、中のコマンドを読む。ヒアドキュメントの本文用。 */
function scanExpansions(text, out, depth) {
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\\') i++;
    else if (text.startsWith('$(', i)) {
      const end = matchParen(text, i + 2);
      scan(text.slice(i + 2, end), out, depth + 1);
      i = end;
    } else if (text[i] === '`') {
      const end = matchBacktick(text, i + 1);
      scan(text.slice(i + 1, end), out, depth + 1);
      i = end;
    }
  }
}

function finishSegment(words, out, depth) {
  let k = 0;
  while (k < words.length && (ENV_ASSIGNMENT.test(words[k]) || PREFIXES.has(words[k]))) k++;
  const command = words.slice(k);
  if (command.length === 0) return;
  out.push(command);
  if (SHELLS.has(baseName(command[0]))) {
    const args = command.slice(1);
    for (const arg of args) scan(arg, out, depth + 1);
    // `eval gh pr merge 5` のように、語を連結して 1 つのコマンドになるもの
    if (args.length > 1) scan(args.join(' '), out, depth + 1);
  }
}

/** text を読み、実行される単純コマンド(前置きを除いた語の配列)を out に足す。 */
function scan(text, out, depth) {
  if (depth > MAX_DEPTH) return;
  let words = [];
  let cur = '';
  let inWord = false;
  const heredocs = [];
  const endWord = () => {
    if (inWord) words.push(cur);
    cur = '';
    inWord = false;
  };
  const endSegment = () => {
    endWord();
    finishSegment(words, out, depth);
    words = [];
  };

  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === '\\') {
      if (text[i + 1] !== '\n') {
        cur += text[i + 1] ?? '';
        inWord = true;
      }
      i += 2;
    } else if (c === "'") {
      const j = text.indexOf("'", i + 1);
      const end = j < 0 ? text.length : j;
      cur += text.slice(i + 1, end);
      inWord = true;
      i = end + 1;
    } else if (c === '"') {
      inWord = true;
      i++;
      while (i < text.length && text[i] !== '"') {
        if (text[i] === '\\') {
          cur += text[i + 1] ?? '';
          i += 2;
        } else if (text.startsWith('$(', i)) {
          const end = matchParen(text, i + 2);
          scan(text.slice(i + 2, end), out, depth + 1);
          cur += text.slice(i, end + 1);
          i = end + 1;
        } else if (text[i] === '`') {
          const end = matchBacktick(text, i + 1);
          scan(text.slice(i + 1, end), out, depth + 1);
          cur += text.slice(i, end + 1);
          i = end + 1;
        } else {
          cur += text[i++];
        }
      }
      i++;
    } else if (text.startsWith('$(', i)) {
      const end = matchParen(text, i + 2);
      scan(text.slice(i + 2, end), out, depth + 1);
      cur += text.slice(i, end + 1);
      inWord = true;
      i = end + 1;
    } else if (c === '`') {
      const end = matchBacktick(text, i + 1);
      scan(text.slice(i + 1, end), out, depth + 1);
      cur += text.slice(i, end + 1);
      inWord = true;
      i = end + 1;
    } else if (c === '#' && !inWord) {
      while (i < text.length && text[i] !== '\n') i++;
    } else if (text.startsWith('<<', i) && text[i + 2] !== '<') {
      // ヒアドキュメント。区切り語を控え、次の改行の後ろから終端行までを本文として読み飛ばす。
      endWord();
      let j = i + 2;
      const strip = text[j] === '-';
      if (strip) j++;
      while (text[j] === ' ' || text[j] === '\t') j++;
      let delim = '';
      let quoted = false;
      while (j < text.length && !/[\s;&|<>()]/.test(text[j])) {
        if (text[j] === "'" || text[j] === '"') {
          const k = text.indexOf(text[j], j + 1);
          const end = k < 0 ? text.length : k;
          delim += text.slice(j + 1, end);
          quoted = true;
          j = end + 1;
        } else if (text[j] === '\\') {
          delim += text[j + 1] ?? '';
          quoted = true;
          j += 2;
        } else {
          delim += text[j++];
        }
      }
      heredocs.push({ delim, strip, quoted });
      i = j;
    } else if (c === '\n') {
      endSegment();
      i++;
      for (const { delim, strip, quoted } of heredocs.splice(0)) {
        let body = '';
        while (i < text.length) {
          let end = text.indexOf('\n', i);
          if (end < 0) end = text.length;
          let line = text.slice(i, end).replace(/\r$/, ''); // CRLF の終端行も区切り語と一致させる
          i = end + 1;
          if (strip) line = line.replace(/^\t+/, '');
          if (line === delim) break;
          body += line + '\n';
        }
        // 区切り語を引用符で囲んでいなければ、本文の `$(...)` は実行される。
        if (!quoted) scanExpansions(body, out, depth);
      }
    } else if (c === ';' || c === '&' || c === '|' || c === '(' || c === ')') {
      endSegment();
      i++;
    } else if (c === ' ' || c === '\t' || c === '\r') {
      endWord();
      i++;
    } else {
      cur += c;
      inWord = true;
      i++;
    }
  }
  endSegment();
}

/** コマンド文字列から、実行される単純コマンド(語の配列)をすべて取り出す。 */
export function parseCommands(text) {
  const out = [];
  scan(text, out, 0);
  return out;
}

/**
 * `words`(例: ['gh', 'pr', 'merge'])で始まる呼び出しを探し、その後ろの語の配列を呼び出しごとに返す。
 * 先頭の語はパス付き・`.exe` 付きでも合う。
 */
export function findInvocations(text, words) {
  const found = [];
  const seen = new Set();
  for (const command of parseCommands(text)) {
    if (command.length < words.length) continue;
    if (baseName(command[0]) !== words[0]) continue;
    if (!words.slice(1).every((w, n) => command[n + 1] === w)) continue;
    // `bash -lc "..."` は引数ごとと連結の両方で読むため、同じ呼び出しが重なる。
    const key = JSON.stringify(command);
    if (seen.has(key)) continue;
    seen.add(key);
    found.push(command.slice(words.length));
  }
  return found;
}

function main() {
  const words = process.argv.slice(2);
  if (words.length === 0) {
    console.error('usage: command_match.mjs <word>... < command');
    return 2;
  }
  let text = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { text += chunk; });
  process.stdin.on('end', () => {
    let found;
    try {
      found = findInvocations(text, words);
    } catch (error) {
      console.error(`command_match: ${error.message}`);
      process.exitCode = 2;
      return;
    }
    for (const args of found) console.log(`M ${args.join(' ').replace(/\s+/g, ' ')}`.trimEnd());
    process.exitCode = found.length > 0 ? 0 : 1;
  });
  return null;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const code = main();
  if (code !== null) process.exitCode = code;
}

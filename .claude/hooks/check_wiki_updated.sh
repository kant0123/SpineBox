#!/usr/bin/env bash
# [オプション] PR 作成前に wiki/ が更新されているかを確認する PreToolUse hook。
#
# `gh pr create` を実行しようとしたとき、origin/main からの差分に wiki/ 配下の
# 変更が 1 件も無ければ、exit 2 でツール呼び出しをブロックして Ingest を促す。
#
# 意図的に Wiki 更新なしで PR を作る場合(タイポ修正など)は、コマンド先頭に
# WIKI_SKIP=1 を付けて再実行する。ただし CLAUDE.md の規約どおり、
# その理由を PR 本文に一行書くこと。
#
# .claude/settings.json への登録例は .claude/settings.example.json を参照。
# 使わない場合はこのファイルと該当 hook 設定を削除してよい。
input=$(cat)

# コマンド文字列の取り出しは python に依存する。python が無い環境・JSON が壊れている場合は
# 生の入力をそのまま検査対象にする(フェイルクローズ)。ここで空文字にして素通しさせると、
# 「フックを入れたのに一度も発火しない」状態に黙って落ちるため。
if command=$(printf '%s' "$input" | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null); then
    parsed=1
else
    command="$input"
    parsed=0
fi

# 実際に gh pr create を実行するコマンドだけを対象にする。引数・引用符・ヒアドキュメントの中の文字列には反応しない (#51)。
# 解釈の本体は command_match.mjs (終了コード 1 = 該当なし)。node が無い・解釈に失敗した場合は、
# 従来の部分一致に戻す (止めすぎる側に倒す)。
if [[ $parsed == 1 ]]; then
    printf '%s' "$command" | node "$(dirname "$0")/command_match.mjs" gh pr create >/dev/null 2>&1
    case $? in
        0) ;;
        1) exit 0 ;;
        *) parsed=0 ;;
    esac
fi
if [[ $parsed != 1 ]]; then
    [[ "$command" == *"gh pr create"* ]] || exit 0
fi

# 明示的な回避
[[ "$command" == *"WIKI_SKIP=1"* ]] && exit 0

# git リポジトリでなければ何もしない
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# 比較対象。origin/main が無いリポジトリでは何もしない
base="origin/main"
git rev-parse --verify --quiet "$base" >/dev/null || exit 0

changed=$(git diff --name-only "$base"...HEAD 2>/dev/null)
[[ -z "$changed" ]] && exit 0

# wiki/ 配下の変更があれば OK
printf '%s\n' "$changed" | grep -q '^wiki/' && exit 0

# wiki 更新が無く、かつコード/設定の変更がある場合のみ促す
# (README.md だけ直したような PR では鳴らさない)
if printf '%s\n' "$changed" | grep -qv -e '^README\.md$' -e '^\.gitignore$' -e '^docs/'; then
    echo "この PR には wiki/ の更新が含まれていません。CLAUDE.md の「ナレッジ Wiki」→ Ingest に従い、影響するページと wiki/log.md を同じ PR で更新してください。更新不要と判断した場合は、その理由を PR 本文に一行書いた上で、コマンド先頭に WIKI_SKIP=1 を付けて再実行してください。" >&2
    exit 2
fi

exit 0

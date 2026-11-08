"""7z / rar のリーダー(7-Zip CLI。`7z.exe`)。

目次は `7z l -slt -ba` の出力(`キー = 値` の行が空行で区切られる)を読み、1 エントリの取り出しは
`7z e -so` の標準出力をそのまま受け取る。ディスクに展開しない・元の階層を作らない。
複数エントリは 1 回の `7z e -so` に名前を並べて取り出し、書庫内の順に連結された出力を目次のサイズで切り分け、
CRC で確かめる(`read_entries`)。ソリッド 7z では 1 エントリごとに塊の先頭から展開し直すため、まとめると大きく効く。
エントリ名は 7z が出したままの文字列(Windows では `\\` 区切り)を取り出しの指定にだけ使い、
ディスク上のパスには使わない。名前の検証と展開上限は `base.list_entries()` が行う。

7z の出力は信用しすぎない(wiki/concepts/recurring-review-findings の「外部コマンドの異常系」):
文字コードは `-sccUTF-8` で固定し、CRLF・前置き・空出力・非ゼロ終了・タイムアウトを `ArchiveError` にする。
ファイル名のワイルドカード解釈は `-spd` で止める(`*` `?` を含む名前を別のエントリに誤爆させない)。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zlib
from collections import Counter
from pathlib import Path
from typing import BinaryIO

from ..config import ExtractionLimits
from .base import ArchiveEntry, ArchiveError, ArchiveReader, LimitExceeded, UnsupportedArchive
from .safety import entry_limit_problem

log = logging.getLogger(__name__)

EXTS = (".7z", ".cb7", ".rar", ".cbr")
LIST_TIMEOUT_SEC = 300
EXTRACT_TIMEOUT_SEC = 1800
_CHUNK = 1024 * 1024
_ERROR_LINE_MAX = 200  # 画面・一覧の理由に出す 7-Zip のエラー行の上限(字数)
# 目次の「リンク」を表すキー。リンクは画像として読まない
_LINK_KEYS = ("Symbolic Link", "Hard Link")

_COMMON = ["-y", "-bd", "-spd", "-sccUTF-8", "-scsUTF-8"]
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_sevenzip(configured: str) -> str | None:
    """設定 `sevenzip_path` から実行ファイルを探す。パスならそのファイル、名前だけなら PATH から。"""
    if not configured:
        return None
    if os.sep in configured or "/" in configured or Path(configured).is_absolute():
        return configured if Path(configured).is_file() else None
    return shutil.which(configured)


def sevenzip_available(configured: str) -> bool:
    return find_sevenzip(configured) is not None


def list_command(exe: str, archive: Path) -> list[str]:
    return [exe, "l", "-slt", "-ba", *_COMMON, "--", str(archive)]


def extract_command(exe: str, archive: Path, *raw_paths: str) -> list[str]:
    # -bso0 -bsp0: 標準出力にはデータだけを流す
    return [exe, "e", "-so", "-bso0", "-bsp0", *_COMMON, "--", str(archive), *raw_paths]


# --- 目次の解釈 ---------------------------------------------------------------


def parse_listing(output: bytes) -> list[dict[str, str]]:
    """`-slt` の出力を、`Path` を持つ記録(キー → 値)のリストにする。

    CRLF・BOM・先頭の前置き(`-ba` が効かない版の見出し。`----------` より前)に耐える。
    `splitlines()` は U+2028 等でも割れて名前を壊すので使わない。
    """
    text = output.decode("utf-8", errors="replace").lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if "----------" in lines:
        lines = lines[lines.index("----------") + 1:]
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: str | None = None
    for line in lines + [""]:
        if not line:
            if "Path" in current:
                records.append(current)
            current, last_key = {}, None
            continue
        key, sep, value = line.partition(" = ")
        if not sep:
            if line.endswith(" =") and "=" not in line[:-2]:
                key, value = line[:-2], ""  # `Created = ` の末尾の空白が落ちた行
            elif last_key is not None:
                current[last_key] += "\n" + line  # 名前に改行を含むときの続き
                continue
            else:
                continue
        current[key] = value
        last_key = key
    return records


def _is_dir(record: dict[str, str]) -> bool:
    if record.get("Folder") == "+":
        return True
    attrs = record.get("Attributes", "").split()
    return bool(attrs) and "D" in attrs[0]


def _crc(record: dict[str, str]) -> int | None:
    """目次の CRC(8 桁の 16 進)。無い・読めないなら None(まとめ取り出しの検証に使うだけ)。"""
    text = record.get("CRC", "").strip()
    if len(text) != 8:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def entries_from_records(records: list[dict[str, str]]) -> tuple[list[ArchiveEntry], dict[int, str]]:
    """(目次, index → 7z が出した名前)。リンクは除く。サイズを読めないファイルは ArchiveError。"""
    entries, raw_paths, _ = _entries_from_records(records)
    return entries, raw_paths


def _entries_from_records(records):
    entries: list[ArchiveEntry] = []
    raw_paths: dict[int, str] = {}
    crcs: dict[int, int] = {}
    for record in records:
        if any(record.get(k) for k in _LINK_KEYS):
            continue
        path = record["Path"]
        is_dir = _is_dir(record)
        size_text = record.get("Size", "").strip()
        try:
            size = int(size_text) if size_text else 0
        except ValueError:
            raise ArchiveError(f"7z の目次のサイズを解釈できない: {path!r}: {size_text!r}") from None
        if not size_text and not is_dir:
            raise ArchiveError(f"7z の目次にサイズが無い: {path!r}")
        index = len(entries)
        raw_paths[index] = path
        crc = _crc(record)
        if crc is not None:
            crcs[index] = crc
        # 7z はフォルダ名の末尾に区切りを付けない。zip と同じく末尾 `/` でフォルダと分かる名前にそろえる
        name = path + "/" if is_dir and not path.endswith(("/", "\\")) else path
        entries.append(ArchiveEntry(index, name, path.encode("utf-8", "surrogatepass"), "utf-8 (7z)", size, is_dir))
    return entries, raw_paths, crcs


# --- 外部コマンドの実行 -------------------------------------------------------


def _archive_arg(cmd: list[str]) -> str:
    """list_command / extract_command は `--` の直後にアーカイブのパスを置く。"""
    if "--" in cmd and cmd.index("--") + 1 < len(cmd):
        return cmd[cmd.index("--") + 1]
    return ""


def _error_line(text: str, archive: str) -> str:
    """標準エラーの先頭のエラーの塊(`ERROR:` で始まる行から空行・`ERRORS:` の手前まで)を 1 行にする。
    7-Zip は `ERROR: <パス> : <パス>` のあとに `Open ERROR: 理由` を続けるので、塊で読むと理由が残る。
    改行・アーカイブのフルパスは出さない(`<ファイル>` に置き換える)。"""
    lines = [ln.strip() for ln in re.split(r"\r\n|\r|\n", text)]
    start = next((i for i, ln in enumerate(lines) if ln.startswith("ERROR")), None)
    if start is None:
        block = [ln for ln in lines if ln][:1]
    else:
        block = []
        for ln in lines[start:]:
            if not ln or ln.startswith("ERRORS"):
                break
            block.append(ln)
    if not block:
        return "(標準エラーの出力なし)"
    line = " ".join(block)
    if archive:
        for form in {archive, archive.replace("\\", "/")}:
            line = line.replace(form, "<ファイル>")
    line = "".join(ch for ch in line if ch.isprintable())
    return line[:_ERROR_LINE_MAX]


def _failure(cmd: list[str], returncode: int, stderr: bytes) -> ArchiveError:
    """7-Zip の非ゼロ終了を ArchiveError にする。理由は先頭のエラー行だけ、標準エラーの全文はログに残す。"""
    text = stderr.decode("utf-8", errors="replace").strip()
    log.warning("7-Zip が失敗した(終了コード %s): %s\n%s", returncode, _archive_arg(cmd), text)
    return ArchiveError(f"7-Zip が失敗した(終了コード {returncode}): {_error_line(text, _archive_arg(cmd))}")


def _popen(cmd: list[str], **kw) -> subprocess.Popen:
    try:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, creationflags=_CREATIONFLAGS, **kw)
    except OSError as e:
        raise ArchiveError(f"7-Zip を起動できない: {e}") from e


def _run_capture(cmd: list[str], timeout: float) -> bytes:
    """標準出力を全部受け取る。非ゼロ終了・タイムアウトは ArchiveError。"""
    with tempfile.TemporaryFile() as err:
        proc = _popen(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise ArchiveError(f"7-Zip が {timeout:g} 秒で終わらない") from None
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        if proc.returncode != 0:
            err.seek(0)
            raise _failure(cmd, proc.returncode, err.read())
    return out


def _stream(cmd: list[str], dest: BinaryIO, max_bytes: int, timeout: float) -> int:
    """標準出力を dest へ流し、書いたバイト数を返す。max_bytes を超えたら止めて LimitExceeded。"""
    with tempfile.TemporaryFile() as err:
        proc = _popen(cmd, stdout=subprocess.PIPE, stderr=err)
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, on_timeout)
        timer.start()
        written = 0
        try:
            assert proc.stdout is not None
            while chunk := proc.stdout.read(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise LimitExceeded(f"エントリの展開サイズが上限を超える(> {max_bytes} バイト)")
                dest.write(chunk)
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            timer.cancel()
            if proc.stdout:
                proc.stdout.close()
        if timed_out.is_set():
            raise ArchiveError(f"7-Zip が {timeout:g} 秒で終わらない")
        if proc.returncode != 0:
            err.seek(0)
            raise _failure(cmd, proc.returncode, err.read())
    return written


def _stream_split(cmd: list[str], sizes: list[int], timeout: float) -> list[bytes]:
    """標準出力を sizes の順に切り分ける。合計が sizes の和と違えば ArchiveError(余分は読まずに止める)。"""
    expected = sum(sizes)

    class _Collect:
        def __init__(self) -> None:
            self.buf = bytearray()

        def write(self, chunk: bytes) -> None:
            self.buf += chunk

    sink = _Collect()
    try:
        written = _stream(cmd, sink, expected, timeout)  # type: ignore[arg-type]
    except LimitExceeded:
        raise ArchiveError(f"まとめて取り出した量が目次の合計を超える(> {expected} バイト)") from None
    if written != expected:
        raise ArchiveError(f"まとめて取り出した量が目次の合計と合わない({written} != {expected} バイト)")
    out, pos = [], 0
    for size in sizes:
        out.append(bytes(sink.buf[pos:pos + size]))
        pos += size
    return out


# --- リーダー -----------------------------------------------------------------


class SevenZipReader(ArchiveReader):
    batch_reads = True

    def __init__(self, path: Path, exe: str):
        self._path = Path(path)
        self._exe = exe
        self._raw_paths: dict[int, str] = {}
        self._crcs: dict[int, int] = {}
        self._entries: list[ArchiveEntry] | None = None

    def raw_entries(self) -> list[ArchiveEntry]:
        if self._entries is None:
            output = _run_capture(list_command(self._exe, self._path), LIST_TIMEOUT_SEC)
            if not output.strip():
                raise ArchiveError("7-Zip の目次が空(壊れたアーカイブか、7-Zip の異常)")
            records = parse_listing(output)
            if not records:
                raise ArchiveError("7-Zip の目次を解釈できない")
            self._entries, self._raw_paths, self._crcs = _entries_from_records(records)
        return self._entries

    # 目次の持ち越し(RAM 層)。同じ版のファイルを開き直すたびに `7z l` を起動しないため
    def listing_state(self) -> tuple:
        self.raw_entries()
        return (self._entries, self._raw_paths, self._crcs)

    def restore_listing(self, state: tuple) -> None:
        self._entries, self._raw_paths, self._crcs = state

    def read_entries(self, entries: list[ArchiveEntry], limits: ExtractionLimits) -> dict[int, bytes]:
        """1 回の `7z e -so` で取り出す。名前が目次で一意なものだけをまとめ、残りは 1 つずつ読む。

        出力は書庫内の順(= index の順)に連結されるので、目次のサイズで切り分け、CRC があれば照合する。
        合計・CRC が合わなければ ArchiveError(別のエントリの中身を返さない)。
        """
        self.raw_entries()
        for e in entries:
            problem = entry_limit_problem(e.size, limits)
            if problem:
                raise LimitExceeded(problem)
        counts = Counter(self._raw_paths.values())
        batch = sorted((e for e in entries if counts[self._raw_paths[e.index]] == 1), key=lambda e: e.index)
        rest = [e for e in entries if counts[self._raw_paths[e.index]] != 1]
        result: dict[int, bytes] = {}
        if len(batch) == 1:
            rest += batch
        elif batch:
            cmd = extract_command(self._exe, self._path, *(self._raw_paths[e.index] for e in batch))
            parts = _stream_split(cmd, [e.size for e in batch], EXTRACT_TIMEOUT_SEC)
            for e, data in zip(batch, parts):
                crc = self._crcs.get(e.index)
                if crc is not None and zlib.crc32(data) & 0xFFFFFFFF != crc:
                    raise ArchiveError(f"まとめて取り出した中身の CRC が目次と合わない: {e.name}")
                result[e.index] = data
        for e in rest:
            result[e.index] = self.read_entry(e, limits)
        return result

    def copy_entry(self, entry: ArchiveEntry, dest: BinaryIO, max_bytes: int) -> int:
        if entry.size > max_bytes:
            raise LimitExceeded(f"エントリの展開サイズが上限を超える({entry.size} > {max_bytes} バイト)")
        self.raw_entries()
        raw_path = self._raw_paths[entry.index]
        written = _stream(extract_command(self._exe, self._path, raw_path), dest, max_bytes, EXTRACT_TIMEOUT_SEC)
        if written != entry.size:
            # 同名のエントリが複数ある・目次と中身が食い違う等。別のものを返さない
            raise ArchiveError(f"取り出したサイズが目次と合わない({written} != {entry.size} バイト)")
        return written

    def close(self) -> None:
        pass


def make_reader(path: Path, sevenzip_path: str) -> SevenZipReader:
    exe = find_sevenzip(sevenzip_path)
    if exe is None:
        raise UnsupportedArchive(f"未対応: 7-Zip が見つからない(sevenzip_path = {sevenzip_path!r})")
    return SevenZipReader(path, exe)

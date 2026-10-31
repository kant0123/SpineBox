"""アーカイブの目次読みと 1 エントリの読み出し(ディスクに展開しない)。"""
from .base import (ARCHIVE_EXTS, ArchiveEntry, ArchiveError, ArchiveReader, LimitExceeded, Listing,
                   UnsupportedArchive, is_archive_name, list_entries, open_archive, register_reader)

__all__ = ["ARCHIVE_EXTS", "ArchiveEntry", "ArchiveError", "ArchiveReader", "LimitExceeded", "Listing",
           "UnsupportedArchive", "is_archive_name", "list_entries", "open_archive", "register_reader"]

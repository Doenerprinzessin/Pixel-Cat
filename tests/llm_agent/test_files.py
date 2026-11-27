"""pixelcat.llm.files: reading/writing text files exactly, backups, refused file types."""
from __future__ import annotations

import codecs
import os
import re
from pathlib import Path

import pytest

from pixelcat import paths
from pixelcat.llm import files
from pixelcat.llm.files import FileError, TextFile


@pytest.mark.parametrize(("data", "text", "enc", "newline", "bom"), [
    (b"a\nb\n", "a\nb\n", "utf-8", "\n", b""),
    (b"a\r\nb\r\n", "a\nb\n", "utf-8", "\r\n", b""),
    (b"a\rb", "a\nb", "utf-8", "\n", b""),
    (codecs.BOM_UTF8 + "Grüße\r\n".encode(), "Grüße\n", "utf-8", "\r\n", codecs.BOM_UTF8),
    ("Grüße\r\nx".encode("utf-16"), "Grüße\nx", "utf-16", "\r\n", b""),
    ("Grüße aus Bern\n".encode("cp1252"), "Grüße aus Bern\n", "cp1252", "\n", b""),
    (b"", "", "utf-8", "\n", b""),
])
def test_read_text_detects_encoding_and_line_ends(tmp_path: Path, data: bytes, text: str, enc: str, newline: str,
                                                  bom: bytes) -> None:
    p = tmp_path / "f.txt"
    p.write_bytes(data)
    tf = files.read_text(p)
    assert (tf.path, tf.text, tf.encoding, tf.newline, tf.bom) == (p, text, enc, newline, bom)


@pytest.mark.parametrize("data", [
    b"a\nb\n", b"a\r\nb\r\n", codecs.BOM_UTF8 + "Grüße\r\nx".encode(), "Grüße\r\nx".encode("utf-16"),
    "Grüße\n".encode("cp1252"),
])
def test_write_text_round_trips_byte_for_byte(tmp_path: Path, data: bytes) -> None:
    (tmp_path / "w").mkdir()
    p = tmp_path / "w" / "f.txt"
    p.write_bytes(data)
    tf = files.read_text(p)
    files.write_text(tf, tf.text)
    assert p.read_bytes() == data
    assert [c.name for c in p.parent.iterdir()] == ["f.txt"]     # the temp file is gone


def test_write_text_normalises_line_ends_to_the_files_own(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_bytes(b"x\r\n")
    files.write_text(files.read_text(p), "a\nb\r\nc\rd")
    assert p.read_bytes() == b"a\r\nb\r\nc\r\nd"


def test_write_text_refuses_characters_the_encoding_lacks(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    with pytest.raises(UnicodeEncodeError):
        files.write_text(TextFile(p, "", "cp1252", "\n", b""), "😺")
    assert not p.exists()


def test_read_text_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(FileError, match="does not exist"):
        files.read_text(tmp_path / "nope.txt")
    with pytest.raises(FileError, match="does not exist"):
        files.read_text(tmp_path)
    binary = tmp_path / "pic.png"
    binary.write_bytes(b"\x89PNG\x00\x00")
    with pytest.raises(FileError, match="^pic.png is not a text file$"):
        files.read_text(binary)
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 3000)
    monkeypatch.setattr(files, "MAX_READ_BYTES", 2048)
    with pytest.raises(FileError, match=r"^big.txt is too big \(2 KB\) for me to read$"):
        files.read_text(big)


def test_backup_goes_to_the_app_data_folder(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("old")
    dest = files.backup(src)
    assert dest.parent == paths.data_dir() / "backups"
    assert re.fullmatch(r"\d{8}_\d{6}_notes\.txt", dest.name)
    assert dest.read_text() == "old" and src.read_text() == "old"


def test_backup_keeps_the_newest_100(tmp_path: Path) -> None:
    folder = paths.data_dir() / "backups"
    folder.mkdir(parents=True)
    for i in range(105):
        p = folder / f"old_{i:03d}.txt"
        p.write_text(str(i))
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    src = tmp_path / "now.txt"
    src.write_text("now")
    dest = files.backup(src)
    left = sorted(p.name for p in folder.iterdir())
    assert len(left) == 100 and dest.name in left
    assert "old_005.txt" not in left and "old_006.txt" in left    # the 6 oldest were removed


@pytest.mark.parametrize("name", ["a.exe", "b.DLL", "c.bat", "d.cmd", "e.ps1", "f.vbs", "g.lnk", "h.reg", "i.msi",
                                  "j.scr", "k.jar", "l.hta", "m.url", "n.appref-ms"])
def test_programs_are_never_written(name: str) -> None:
    with pytest.raises(FileError, match=r"programs and system files are off limits$"):
        files.check_writable_name(Path(name))


@pytest.mark.parametrize("name", ["desktop.ini", "NTUSER.DAT", "Thumbs.db"])
def test_system_files_are_left_alone(name: str) -> None:
    with pytest.raises(FileError, match=f"^{name} is a system file - I leave it alone$"):
        files.check_writable_name(Path(name))


@pytest.mark.parametrize("name", ["notes.txt", "app.py", "data.json", "README", "page.html", "x.exe.txt"])
def test_text_files_may_be_written(name: str) -> None:
    files.check_writable_name(Path(name))


def test_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "a.txt"
    tf = TextFile(p, "one\ntwo", "utf-8", "\n", b"")
    assert files.show(tf) == f'File {p} - 2 lines, 7 characters:\n"""\none\ntwo\n"""'
    monkeypatch.setattr(files, "MAX_SHOW_CHARS", 5)
    tf = TextFile(p, "x" * 1234, "utf-8", "\n", b"")
    assert files.show(tf) == f'File {p} - 1 lines, 1,234 characters (showing the first 5 characters):\n"""\nxxxxx\n"""'

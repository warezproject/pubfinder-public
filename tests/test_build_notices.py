"""배포 zip의 제3자 고지와 licenses 폴더. exe는 만들지 않고 가짜 exe로 zip만 만들어 본다."""

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_exe", ROOT / "scripts" / "build_exe.py")
build_exe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_exe)

FOUND = {"python": "3.11.9", "tcl": "8.6.12", "openssl": "3.0.13", "expat": "2.6.0", "mpdec": "2.5.1"}


class BuildNoticeTests(unittest.TestCase):
    def test_license_texts_are_in_the_repo(self):
        for name in build_exe.LICENSE_SOURCES:
            path = build_exe.THIRD_PARTY / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 500, name)

    def test_version_mismatch_is_reported(self):
        self.assertEqual(build_exe.version_mismatches(FOUND), [])
        self.assertEqual(len(build_exe.version_mismatches({**FOUND, "python": "3.12.1"})), 1)

    def test_every_listed_license_file_goes_into_the_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "pubfinder.exe"
            exe.write_bytes(b"MZ")
            archive = Path(folder) / "out.zip"
            build_exe.write_zip(archive, exe, FOUND)
            with zipfile.ZipFile(archive) as zin:
                names = set(zin.namelist())
                notice = zin.read("pubfinder/제3자 고지.txt").decode("utf-8-sig")
        self.assertIn("pubfinder/pubfinder.exe", names)
        self.assertIn("pubfinder/처음 쓰는 법.txt", names)
        for name in build_exe.LICENSE_SOURCES:
            self.assertIn(f"pubfinder/licenses/{name}", names)
        for line in notice.splitlines():
            if line.strip().startswith("원문: licenses/"):
                listed = line.split("licenses/", 1)[1].split("의 ", 1)[0]
                self.assertIn(f"pubfinder/licenses/{listed}", names)
        for word in ("Python 3.11.9", "OpenSSL 3.0.13", "expat 2.6.0", "libmpdec 2.5.1", "Tcl 8.6.12", "Microsoft"):
            self.assertIn(word, notice)
        self.assertIn("\r\n", notice)


if __name__ == "__main__":
    unittest.main()

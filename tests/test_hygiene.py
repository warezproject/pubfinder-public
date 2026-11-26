"""공개 전 위생 검사. 저장소의 모든 글 파일을 훑는다.

- 정해 둔 낱말(기관·업무 시스템 이름 등)과 문서번호 꼴이 없어야 한다.
- 사람 확인 화면 관련 낱말은 정해 둔 파일에만 있어야 한다(2026-11-15 결정으로 누르기 지시문·코드는 넣었다).
- 정해 둔 바깥 모듈을 import하지 않는다.
- 시험 자료의 사람 이름은 지어낸 이름만 쓴다.

낱말 목록과 바깥 모듈 이름은 저장소에 넣지 않는다. git이 무시하는 `tests/hygiene.local.json`
(또는 환경 변수 `PUBFINDER_HYGIENE_FILE`이 가리키는 파일)에 {"words": [...], "outside_modules": [...]}로 두고,
파일이 없으면 그 두 검사는 건너뛴다.
만든 사람의 이름·소속·연락처는 낱말 검사에서 뺀다. README는 `<!-- author:start -->`~`<!-- author:end -->` 사이,
pyproject.toml은 `authors = [...]` 한 줄이다.
"""

import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LOCAL_FILE = Path(os.environ.get("PUBFINDER_HYGIENE_FILE") or ROOT / "tests" / "hygiene.local.json").resolve()


def _local_lists() -> dict:
    try:
        data = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_LOCAL = _local_lists()
_WORDS = [str(word).casefold() for word in _LOCAL.get("words") or []]
_OUTSIDE_NAMES = [str(name) for name in _LOCAL.get("outside_modules") or []]
_AUTHOR_BLOCKS = {
    "README.md": re.compile(r"<!-- author:start -->.*?<!-- author:end -->", re.DOTALL),
    "pyproject.toml": re.compile(r"^authors = \[[^\n\]]*\]$", re.MULTILINE),
}
_DOC_NUMBER = re.compile(r"(?<!\d)\d{4}-20\d{2}-\d{5}(?!\d)")
_CHALLENGE_WORDS = re.compile(r"captcha|turnstile|cloudflare|challenge", re.IGNORECASE)
_CHALLENGE_FILES = {
    "README.md", "docs/decisions.md", "docs/aside-interface.md", "docs/adoption-rules.md",
    "src/pubfinder/prompts.py", "src/pubfinder/aside/capture.py",
    "src/pubfinder/aside/pages.py", "src/pubfinder/quotes.py",
    "src/pubfinder/output.py",
    "tests/test_capture.py", "tests/test_hygiene.py", "tests/test_inputs_and_results.py",
    "tests/test_runner_e2e.py", "tests/fakes/fake_aside.py",
}
_OUTSIDE_MODULES = (re.compile(r"^\s*(?:from|import)\s+(" + "|".join(map(re.escape, _OUTSIDE_NAMES)) + r")\b",
                               re.MULTILINE) if _OUTSIDE_NAMES else None)
_ALLOWED_NAMES = {"Alex Sample", "Riley Fixture", "홍길동", "김예시"}
_TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".json", ".cfg", ".ini", ".yml", ".yaml", ".html", ".js", ".css", ""}
_SKIPPED_DIRS = (".git/", "cpf-runs/", "pubfinder-runs/", ".venv/", ".venv-build/", "build/", "dist/", ".ruff_cache/")


def text_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT).as_posix()
        if (not path.is_file() or relative.startswith(_SKIPPED_DIRS) or path.resolve() == _LOCAL_FILE
                or "__pycache__" in relative or ".egg-info" in relative or path.suffix not in _TEXT_SUFFIXES):
            continue
        yield relative, path.read_text(encoding="utf-8")


class HygieneTests(unittest.TestCase):
    def test_no_listed_words_or_doc_numbers(self):
        found = []
        for relative, text in text_files():
            if relative in _AUTHOR_BLOCKS:
                text = _AUTHOR_BLOCKS[relative].sub("", text)
            lower = text.casefold()
            for index, word in enumerate(_WORDS):
                pattern = r"(?<![0-9a-z])" + re.escape(word) + r"(?![0-9a-z])" if word.isascii() else re.escape(word)
                if re.search(pattern, lower):
                    # 낱말 자체는 시험 출력에 남기지 않는다. 목록 순번만 적는다.
                    found.append((relative, f"word#{index}"))
            if _DOC_NUMBER.search(text):
                found.append((relative, "doc-number"))
        self.assertEqual(found, [])

    def test_challenge_words_stay_in_known_files(self):
        found = [relative for relative, text in text_files()
                 if _CHALLENGE_WORDS.search(text) and relative not in _CHALLENGE_FILES]
        self.assertEqual(found, [])

    @unittest.skipUnless(_WORDS, "낱말 목록 파일이 없어 건너뜀(tests/hygiene.local.json)")
    def test_listed_words_file_is_present(self):
        """목록 파일이 있을 때만 위 검사가 낱말까지 본다. 없으면 이 시험이 건너뜀으로 보여 알 수 있다."""
        self.assertTrue(_WORDS)

    @unittest.skipUnless(_OUTSIDE_MODULES, "바깥 모듈 목록 파일이 없어 건너뜀(tests/hygiene.local.json)")
    def test_no_imports_from_outside_this_program(self):
        found = [relative for relative, text in text_files() if relative.endswith(".py")
                 and _OUTSIDE_MODULES.search(text)]
        self.assertEqual(found, [])

    def test_fixture_people_are_made_up(self):
        from tests import helpers

        names = set(helpers.AUTHORS)
        self.assertLessEqual(names, _ALLOWED_NAMES)


if __name__ == "__main__":
    unittest.main()

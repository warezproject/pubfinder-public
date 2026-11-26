"""가짜 Aside 장면 만들기와 실행 도우미."""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import Path

from pubfinder.aside.settings import AsideSettings
from pubfinder.prompts import PRIMARY_SCHEMA, REVIEW_SCHEMA
from tests import helpers as h

FAKE = Path(__file__).with_name("fake_aside.py")


def fake_resolve(host, port, type=0):  # noqa: A002 - socket.getaddrinfo와 같은 인자 이름
    if host.endswith(".internal.example"):
        return [(2, 1, 6, "", ("10.0.0.5", port))]
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def tiny_pdf(text: str) -> bytes:
    """글자 층이 있는 한 쪽짜리 PDF."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = zlib.compress(f"BT /F1 10 Tf 40 760 Td ({escaped}) Tj ET".encode("latin-1"))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def primary_result(obs: dict, outcome: str = "found") -> dict:
    return {"schema_version": PRIMARY_SCHEMA, "run_id": "{run_id}", "input_fingerprint": "{input_fingerprint}",
            "outcome": outcome, "reason": "", "limitations": [],
            "trail": [{"query": "made-up query", "url": h.PROGRAM_URL, "purpose": "program", "result": "confirmed",
                       "controls": "", "observation": "program page"}],
            "identity": obs["identity"], "event": obs["event"], "fields": obs["fields"]}


def review_result(obs: dict) -> dict:
    return {"schema_version": REVIEW_SCHEMA, "run_id": "{run_id}", "review_run_id": "{review_run_id}",
            "limitations": [], "identity": obs["identity"], "event": obs["event"], "fields": obs["fields"]}


def default_scene(model: str = "fake-provider/fake-model") -> dict:
    return {
        "pages": {url: {"text": text, "html": f"<html><body>{text}</body></html>"} for url, text in h.PAGES.items()},
        "files": {},
        "exec": {
            "primary": {"result": primary_result(h.observations())},
            "review": {"result": review_result(h.observations())},
            "reconcile": {"final_text": h.agree_all()},
        },
    }


def pdf_entry(text: str) -> dict:
    return {"b64": base64.b64encode(tiny_pdf(text)).decode(), "content_type": "application/pdf"}


@contextmanager
def fake_aside(scene: dict):
    """장면 파일·가짜 홈을 만들고 환경 변수를 잠깐 바꾼다. (settings, 폴더, 명령 기록 읽기 함수)를 돌려준다."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scene.json").write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
        old = {key: os.environ.get(key) for key in ("CPF_FAKE_SCENARIO", "CPF_FAKE_HOME", "CPF_FAKE_LOG")}
        os.environ.update(CPF_FAKE_SCENARIO=str(root / "scene.json"), CPF_FAKE_HOME=str(root / "home"),
                          CPF_FAKE_LOG=str(root / "calls"))
        settings = AsideSettings(model="fake-provider/fake-model", effort="medium", executable=str(FAKE),
                                 launcher=(sys.executable, "-X", "utf8", "-B"), home=str(root / "home"),
                                 launch_interval=0.0, research_timeout=60.0, reconcile_timeout=60.0,
                                 capture_timeout=30.0, stop_timeout=10.0)

        def calls() -> list[list[str]]:
            folder = root / "calls"
            return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))] \
                if folder.is_dir() else []

        try:
            yield settings, root, calls
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

"""Windows용 pubfinder.exe와 배포 zip을 만든다.

    python scripts/build_exe.py

- 저장소 안 `.venv-build` 가상환경에서 빌드한다(전역 Python은 건드리지 않는다). PyInstaller와 pypdf는 없을 때만
  PyPI에서 설치하고, 이 프로그램은 설치하지 않고 저장소의 `src`를 그대로 넣는다.
- 결과: `dist/pubfinder.exe`, `dist/pubfinder-windows.zip`(exe, 처음 쓰는 법, 제3자 고지, licenses 폴더).
- 제3자 고지에는 exe에 함께 들어가는 구성 요소(Python과 그에 딸린 OpenSSL·libffi·bzip2·xz·expat·libmpdec,
  Tcl/Tk, pypdf, PyInstaller 부트로더, Microsoft C 런타임)의 판과 라이선스를 적고, 원문은 licenses 폴더에 넣는다.
- 라이선스 원문은 `scripts/third_party/`에 둔다. 빌드에 쓰는 Python의 판이 원문의 판과 다르면 빌드하지 않는다.
- exe를 더블클릭하면 pubfinder 창(tkinter)이 뜬다. 웹 브라우저나 서버는 쓰지 않는다. 설정과 결과는 exe가 있는 폴더에 남는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv-build"
DIST = ROOT / "dist"
THIRD_PARTY = ROOT / "scripts" / "third_party"
# scripts/third_party/ 원문의 출처(https://raw.githubusercontent.com/ 뒤의 경로). 2026-11-26에 받았다.
# 모두 Python 3.11.9 Windows 빌드에 들어 있는 판이다. 빌드용 Python을 바꾸면 원문도 그 판으로 다시 받고 EXPECTED를 고친다.
LICENSE_SOURCES = {
    "python-3.11.9-LICENSE.txt": "python/cpython/v3.11.9/LICENSE",
    "python-3.11.9-docs-license.txt": "python/cpython/v3.11.9/Doc/license.rst",
    "tcl-8.6.12-license.txt": "python/cpython-source-deps/tcl-core-8.6.12.1/license.terms",
    "tk-8.6.12-license.txt": "python/cpython-source-deps/tk-8.6.12.1/license.terms",
    "openssl-3.0.13-LICENSE.txt": "openssl/openssl/openssl-3.0.13/LICENSE.txt",
    "libffi-3.4.4-LICENSE.txt": "libffi/libffi/v3.4.4/LICENSE",
    "bzip2-1.0.8-LICENSE.txt": "python/cpython-source-deps/bzip2-1.0.8/LICENSE",
    "xz-5.2.5-COPYING.txt": "python/cpython-source-deps/xz-5.2.5/COPYING",
}
EXPECTED = {"python": "3.11.9", "tcl": "8.6.12", "openssl": "3.0.13"}
# 빌드용 Python에서 exe에 들어가는 구성 요소의 판을 읽는다.
_PROBE = ("import json, platform, ssl, pyexpat, decimal, tkinter; print(json.dumps({"
          "'python': platform.python_version(), 'tcl': tkinter.Tcl().call('info', 'patchlevel'), "
          "'openssl': ssl.OPENSSL_VERSION.split()[1], 'expat': pyexpat.EXPAT_VERSION.removeprefix('expat_'), "
          "'mpdec': decimal.__libmpdec_version__}))")
GUIDE = """pubfinder 처음 쓰는 법

1. Aside를 설치하고 로그인한 뒤, Aside 앱 설정에서 조사에 쓸 모델 공급자를 연결한다.
   Aside CLI도 로그인한다(PowerShell에서 aside login 뒤 Aside 앱 Settings > Account > Devices에서 승인).
2. 이 폴더의 pubfinder.exe를 더블클릭한다.
   처음 실행할 때 Windows의 PC 보호 화면이 뜨면 추가 정보를 누르고 실행을 누른다.
3. pubfinder 창이 뜬다. 설정 탭에서 모델을 확인하고 추론 강도를 골라 저장한 뒤 점검을 누른다.
   모델은 처음에 claude-code/claude-sonnet-5-5(Claude 계정)이고, 이 모델이 한도·잔액·연결 문제로 거절되면
   anthropic/claude-sonnet-5-5(Anthropic API)로 다시 조사한다. 다른 모델은 Aside에 나온 이름 그대로 적는다.
4. 한 건 조사 탭에서 논문명, 학술대회명, 저자를 넣고 조사 시작을 누른다. 한 건에 보통 1~4분 걸린다.
5. 끝낼 때는 창을 닫는다. 조사 중이면 멈출지 묻고, 진행 중인 Aside 세션을 멈춘 뒤 끝난다.

설정(pubfinder.local.toml)과 결과(pubfinder-runs 폴더)는 이 폴더에 남는다.
새 판을 다른 폴더에 풀면 설정을 다시 정하거나 예전 폴더의 pubfinder.local.toml을 옮겨 온다.
조사에는 Aside에 연결한 모델의 사용료가 든다(한 건 평균 약 0.35달러).
exe에 함께 든 제3자 소프트웨어는 제3자 고지.txt와 licenses 폴더에 적었다.
자세한 설명: https://github.com/warezproject/pubfinder-public
"""


def run(*args: str) -> None:
    print(">", " ".join(args), flush=True)
    subprocess.run(list(args), check=True, cwd=ROOT)


def _dist_info(name: str) -> Path | None:
    found = sorted((VENV / "Lib" / "site-packages").glob(f"{name}-*.dist-info"))
    return found[-1] if found else None


def _version(dist_info: Path | None) -> str:
    """`pypdf-7.1.0.dist-info` → `7.1.0`."""
    return dist_info.name.removesuffix(".dist-info").split("-", 1)[1] if dist_info else ""


def _first_text(dist_info: Path | None, pattern: str) -> str:
    if not dist_info:
        return ""
    return next((path.read_text(encoding="utf-8") for path in sorted(dist_info.rglob(pattern)) if path.is_file()), "")


def bundled_versions(python: str) -> dict[str, str]:
    """빌드용 Python에 든 Python·Tcl/Tk·OpenSSL·expat·libmpdec의 판."""
    out = subprocess.run([python, "-I", "-c", _PROBE], check=True, capture_output=True, text=True, cwd=ROOT).stdout
    return json.loads(out)


def version_mismatches(found: dict[str, str]) -> list[str]:
    return [f"{key}: 빌드용 {found.get(key, '?')}, 라이선스 원문 {want}"
            for key, want in EXPECTED.items() if found.get(key) != want]


def license_files() -> dict[str, str]:
    """zip의 licenses 폴더에 넣을 원문. 이름 → 내용."""
    files = {name: (THIRD_PARTY / name).read_text(encoding="utf-8") for name in LICENSE_SOURCES}
    pypdf, pyinstaller = _dist_info("pypdf"), _dist_info("pyinstaller")
    files[f"pypdf-{_version(pypdf)}-LICENSE.txt"] = _first_text(pypdf, "LICENSE*")
    files[f"pyinstaller-{_version(pyinstaller)}-COPYING.txt"] = _first_text(pyinstaller, "COPYING*")
    return {name: text for name, text in files.items() if text}


def third_party_notices(found: dict[str, str], licenses: dict[str, str]) -> str:
    """exe에 함께 들어가는 제3자 소프트웨어의 판·라이선스와 licenses 폴더의 원문 파일 이름."""
    pypdf = next((name for name in licenses if name.startswith("pypdf-")), "")
    pyinstaller = next((name for name in licenses if name.startswith("pyinstaller-")), "")
    rows = [
        (f"Python {found['python']}", "PSF License Agreement", "python-3.11.9-LICENSE.txt"),
        (f"Python에 함께 든 소프트웨어(expat {found['expat']}, libmpdec {found['mpdec']}, zlib 등)",
         "항목마다 다름(MIT, BSD 등)", "python-3.11.9-docs-license.txt의 Licenses and Acknowledgements for "
         "Incorporated Software"),
        (f"OpenSSL {found['openssl']}", "Apache-2.0", "openssl-3.0.13-LICENSE.txt"),
        ("libffi 3.4.4", "MIT", "libffi-3.4.4-LICENSE.txt"),
        ("bzip2 1.0.8", "bzip2 License(BSD 계열)", "bzip2-1.0.8-LICENSE.txt"),
        ("xz(liblzma) 5.2.5", "퍼블릭 도메인", "xz-5.2.5-COPYING.txt"),
        (f"Tcl {found['tcl']}", "Tcl/Tk License(BSD 계열)", "tcl-8.6.12-license.txt"),
        (f"Tk {found['tcl']}", "Tcl/Tk License(BSD 계열)", "tk-8.6.12-license.txt"),
        (f"pypdf {pypdf.split('-')[1] if pypdf else ''}", "BSD-3-Clause", pypdf),
        (f"PyInstaller {pyinstaller.split('-')[1] if pyinstaller else ''} 부트로더",
         "GPL-2.0-or-later, 부트로더 예외 조항", pyinstaller),
        ("Microsoft Visual C++ 런타임(VCRUNTIME140.dll), Universal C Runtime(ucrtbase.dll, api-ms-win-*.dll)",
         "Microsoft 재배포 조건", ""),
    ]
    lines = ["pubfinder.exe에 함께 들어 있는 제3자 소프트웨어", "",
             "각 구성 요소는 아래 라이선스를 따른다. 원문은 같은 폴더의 licenses 폴더에 있다.", ""]
    for index, (name, license_name, file) in enumerate(rows, 1):
        lines.append(f"{index}. {name}")
        lines.append(f"   라이선스: {license_name}")
        if file:
            lines.append(f"   원문: licenses/{file}")
        lines.append("")
    lines += [
        "PyInstaller 부트로더: 예외 조항에 따라 PyInstaller로 만든 실행 파일은 GPL의 조건을 따르지 않아도 된다.",
        "Microsoft C 런타임: Microsoft가 재배포를 허용한 런타임 파일로, exe가 돌아가는 데 필요해 PyInstaller가 함께 넣었다.",
    ]
    return "\n".join(lines) + "\n"


def _zip_text(zout: zipfile.ZipFile, name: str, text: str) -> None:
    """메모장에서 바로 읽히도록 UTF-8 BOM과 CRLF로 넣는다."""
    zout.writestr(name, "﻿" + text.replace("\r\n", "\n").replace("\n", "\r\n"))


def write_zip(archive: Path, exe: Path, found: dict[str, str]) -> None:
    licenses = license_files()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.write(exe, "pubfinder/pubfinder.exe")
        _zip_text(zout, "pubfinder/처음 쓰는 법.txt", GUIDE)
        _zip_text(zout, "pubfinder/제3자 고지.txt", third_party_notices(found, licenses))
        for name, text in licenses.items():
            _zip_text(zout, f"pubfinder/licenses/{name}", text)


def main() -> int:
    if sys.platform != "win32":
        print("Windows에서만 만든다")
        return 1
    if not VENV.exists():
        venv.create(VENV, with_pip=True)
    python = str(VENV / "Scripts" / "python.exe")
    found = bundled_versions(python)
    if mismatches := version_mismatches(found):
        print("빌드용 Python의 판이 scripts/third_party/의 라이선스 원문과 다르다. 원문을 그 판으로 바꾼 뒤 다시 만든다.")
        print("\n".join(mismatches))
        return 1
    missing = [spec for name, spec in (("pyinstaller", "pyinstaller>=6"), ("pypdf", "pypdf>=4")) if not _dist_info(name)]
    if missing:
        run(python, "-m", "pip", "install", "--quiet", *missing)
    # --paths src: 가상환경에 이 프로그램을 설치하지 않고(예전 판이 깔려 있어도) 저장소의 지금 소스로 만든다.
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "pubfinder",
        "--paths", str(ROOT / "src"), "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "scripts" / "pubfinder_entry.py"))
    exe = DIST / "pubfinder.exe"
    archive = DIST / "pubfinder-windows.zip"
    write_zip(archive, exe, found)
    print(f"만들었다: {exe} ({exe.stat().st_size // 1024 // 1024}MB), {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

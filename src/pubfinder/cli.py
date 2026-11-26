"""명령줄.

    python -m pubfinder search --title "..." --venue "..." --author "..." [--author "..."]
        [--year 2026] --model provider/model --effort medium [--config cpf.toml] [--json]
    python -m pubfinder verify pubfinder-runs/<run_id>/result.json
    python -m pubfinder check-aside [--with-exec] --model ... --effort ...

종료 코드: 0 조사 완료(채택 여부와 무관), 1 조사를 끝내지 못함, 2 입력·설정 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from .aside.settings import AsideSettings, SettingsError
from .config import load
from .output import render_text
from .query import QueryError, make_query
from .runner import RunOptions, run_query
from .verdict import verify_result


def _aside_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="TOML 설정 파일")
    parser.add_argument("--model", help="Aside 모델(provider/model). 기본값 없음")
    parser.add_argument("--effort", help="추론 강도(예: low, medium, high). 기본값 없음")
    parser.add_argument("--fallback-model", dest="fallback_model",
                        help="공급자 잔액 부족으로 --model이 거절되면 그 세션을 다시 돌릴 모델(provider/model)")
    parser.add_argument("--aside-executable", dest="executable", help="Aside CLI 실행 파일 경로")
    parser.add_argument("--account", help="Aside 계정(기본 u0)")
    parser.add_argument("--host", help="Aside 호스트(기본 local)")
    parser.add_argument("--profile", help="브라우저 프로필 ID")
    parser.add_argument("--research-timeout", type=float, help="조사 세션 하나의 제한 시간(초, 기본 1200)")
    parser.add_argument("--autostart", action="store_true", default=None,
                        help="Aside 앱이 꺼져 있으면 설정의 app_path로 한 번 시작")


def settings_from(args: argparse.Namespace) -> tuple[AsideSettings, dict[str, Any]]:
    aside, research = load(args.config)
    for key in ("model", "effort", "fallback_model", "executable", "account", "host", "profile", "research_timeout", "autostart"):
        value = getattr(args, key, None)
        if value is not None:
            aside[key] = value
    known = {item.name for item in dataclass_fields(AsideSettings)}
    settings = AsideSettings(**{"model": "", "effort": "", **{k: v for k, v in aside.items() if k in known}})
    return settings.check(), research


def _search(args: argparse.Namespace) -> int:
    try:
        query = make_query(args.title, args.venue, args.author or [], args.year or "")
        settings, research = settings_from(args)
    except (QueryError, SettingsError) as exc:
        print(f"입력·설정 오류: {exc}", file=sys.stderr)
        return 2
    options = RunOptions(
        out_dir=Path(args.out or research.get("out_dir") or "pubfinder-runs"),
        korean_place_as_korea=(not args.no_korean_place_rule) and research.get("korean_place_as_korea", True),
        excluded_domains=tuple(args.excluded_domain) if args.excluded_domain else research.get(
            "excluded_domains", RunOptions().excluded_domains),
        run_deadline=float(args.run_deadline or research.get("run_deadline") or RunOptions().run_deadline),
        keep_captures=bool(args.keep_captures or research.get("keep_captures", False)),
    )
    try:
        result = run_query(query, settings, options)
    except KeyboardInterrupt:
        print("중단했습니다. 이 프로그램이 띄운 Aside 세션은 멈추게 했습니다.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(render_text(result))
        print(f"결과 파일: {Path(result['run']['out_dir']) / 'result.json'}")
    return 1 if result["status"] == "failed" else 0


def _verify(args: argparse.Namespace) -> int:
    try:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"결과 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
        return 2
    if result.get("status") == "failed":
        print("판정까지 가지 못한 실행이라 다시 계산할 판정이 없습니다.")
        return 1
    ok = verify_result(result)
    print("판정 재계산: 같음" if ok else "판정 재계산: 다름(결과 파일이 바뀌었거나 판정 규칙이 달라졌습니다)")
    return 0 if ok else 1


def _check(args: argparse.Namespace) -> int:
    from .check import check_aside

    try:
        settings, _ = settings_from(args)
    except SettingsError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    report = check_aside(settings, with_exec=args.with_exec, out_dir=Path(args.out or "pubfinder-runs") / "check")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pubfinder",
                                     description="학술대회 발표 정보를 Aside로 두 번 따로 조사하고 근거를 대조한다")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="한 건 조사")
    search.add_argument("--title", required=True, help="논문명(한글 또는 영문)")
    search.add_argument("--venue", required=True, help="출처명(학술대회명)")
    search.add_argument("--author", action="append", help="저자명. 여러 명이면 여러 번 쓴다")
    search.add_argument("--year", help="연도 또는 날짜(선택, 넣기를 권함). 예: 2026, 2026-06-10, June 2026")
    search.add_argument("--out", help="실행 기록 폴더(기본 pubfinder-runs)")
    search.add_argument("--json", action="store_true", help="결과 JSON 전체를 표준출력으로")
    search.add_argument("--no-korean-place-rule", action="store_true",
                        help="국가명 없는 한글 장소를 한국 개최로 보는 규칙을 끈다")
    search.add_argument("--excluded-domain", action="append", help="근거로 받지 않을 도메인(기본 arxiv.org)")
    search.add_argument("--run-deadline", type=float, help="한 건 전체 상한(초, 기본 3600)")
    search.add_argument("--keep-captures", action="store_true", help="다시 연 원문 본문을 실행 폴더에 남긴다")
    _aside_args(search)
    search.set_defaults(handler=_search)
    verify = sub.add_parser("verify", help="저장된 결과의 판정을 다시 계산해 같은지 본다")
    verify.add_argument("result")
    verify.set_defaults(handler=_verify)
    check = sub.add_parser("check-aside", help="Aside 접점 점검")
    check.add_argument("--with-exec", action="store_true",
                       help="짧은 조사 세션 하나를 실제로 띄워 결과 파일 접점까지 본다(모델 사용량을 쓴다)")
    check.add_argument("--out", help="점검 기록 폴더")
    _aside_args(check)
    check.set_defaults(handler=_check)
    app = sub.add_parser("app", help="Windows 창으로 쓰기")
    app.add_argument("--data-dir", help="설정과 결과를 둘 폴더(기본: 현재 폴더)")
    app.set_defaults(handler=_app)
    return parser


def _app(args: argparse.Namespace) -> int:
    from .app import main as app_main

    return app_main(["--data-dir", args.data_dir] if args.data_dir else [])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

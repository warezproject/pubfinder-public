"""창에 보이는 이름과 안내 문구."""

from __future__ import annotations

FIELD_LABELS = {
    "venue_name": "행사명", "presentation_date": "발표일", "event_start": "행사 시작일", "event_end": "행사 종료일",
    "organizer": "주관", "location": "장소", "host_country": "개최국", "conference_scope": "국내·국제 구분",
    "title_ko": "한글 제목", "title_en": "영문 제목", "observed_authors": "저자(원문 순서)", "session": "세션",
    "presentation_time": "발표 시각",
}

STATUS_LABELS = {
    "verified": "확인됨", "partial": "일부 확인", "not_verified": "채택 없음", "not_found": "논문 못 찾음",
    "blocked": "접근 차단", "failed": "조사 중단", "queued": "대기", "running": "조사 중", "done": "끝남",
    "cancelled": "취소됨",
}

CHECK_LABELS = {
    "settings": "조사 설정(모델·추론 강도)", "preflight": "Aside 준비(CLI·로그인·브라우저)", "version": "Aside 판",
    "host_profiles": "브라우저 프로필", "repl_page_capture": "화면 다시 열기", "repl_file_capture": "PDF 받기",
    "exec_session_id": "짧은 세션 실행", "model_fallback": "대체 모델로 전환", "session_folder": "세션 폴더",
    "result_file": "결과 파일", "transcript": "모델 응답", "session_list": "세션 목록",
}

# 앞에 있는 것이 먼저 맞는다. 잔액 부족은 research_unavailable보다 먼저 본다.
HINTS = [
    ("aside_executable_missing", "Aside CLI를 찾지 못했습니다. Aside를 설치했는지 보고, 다른 곳에 있으면 설정의 CLI 경로를 적으세요."),
    ("aside_not_running", "Aside 브라우저가 꺼져 있습니다. Aside 앱을 켜고 다시 하세요."),
    ("aside_account_not_signed_in", "Aside CLI가 로그인되지 않았습니다. PowerShell에서 aside login을 실행하고 "
                                    "Aside 앱의 Settings > Account > Devices에서 승인하세요."),
    ("aside_browser_unavailable", "Aside 브라우저와 연결되지 않았습니다. Aside 앱을 껐다 켜고 다시 하세요."),
    ("aside_repl_timeout", "Aside 브라우저 응답이 늦습니다. Aside 앱을 껐다 켜고 다시 하세요."),
    ("out_of_credit", "모델 공급자 계정의 잔액(크레딧)이 부족합니다. 공급자 결제 화면에서 충전하거나, 설정 탭의 "
                      "'주 모델을 못 쓸 때 쓸 모델'에 Aside 앱에 연결한 다른 모델 이름을 적고 저장하세요."),
    ("research_unavailable", "모델을 쓸 수 없습니다. 사용량 한도, 모델 연결, 모델 이름을 확인하세요."),
    ("research_not_started", "Aside 세션이 시작되지 않았습니다. 잠시 뒤 다시 하세요."),
    ("instructions_unreadable", "조사 세션이 지시 파일을 읽지 못했습니다. 설정 파일 [aside]에 "
                                "instructions_file = false를 적으면 지시를 통째로 보냅니다."),
    ("incomplete", "조사 시간이 넘었습니다. 다시 하거나 설정에서 제한 시간을 늘리세요."),
    ("result_missing", "조사가 결과 파일 없이 끝났습니다. 다시 하세요."),
    ("invalid_result", "조사 결과 형식이 맞지 않았습니다. 다시 하세요."),
    ("Aside 모델을 정하세요", "설정 탭의 Aside 모델 칸에 모델 이름을 적거나 목록에서 고르고 저장을 누르세요."),
    ("추론 강도를 정하세요", "설정 탭에서 추론 강도를 고르고 저장을 누르세요."),
    ("model_usage_limit", "모델 공급자의 사용량 한도에 걸렸습니다. 잠시 뒤 다시 하거나, 설정 탭의 "
                          "'주 모델을 못 쓸 때 쓸 모델'에 쓸 수 있는 다른 모델을 적으세요."),
    ("model_auth", "모델 공급자 연결이나 인증에 문제가 있습니다. Aside 앱 설정에서 그 공급자가 연결돼 있는지, "
                   "모델 이름이 Aside에 나온 그대로인지 확인하세요."),
    ("model_error", "모델 공급자가 오류를 냈습니다. 문구를 보고, 모델 이름이 Aside 앱에 나온 이름과 같은지 확인하세요."),
    ("session_record_missing", "Aside가 세션 기록을 두는 곳이 바뀌었을 수 있습니다. pubfinder를 고쳐야 하니 알려 주세요."),
    ("done_marker_missing", "모델이 점검 지시를 끝까지 따르지 않았습니다. 다시 하거나 다른 모델로 해 보세요."),
]


def hint_for(reason: str) -> str:
    text = reason or ""
    for key, hint in HINTS:
        if key in text:
            return hint
    return ""


def run_time(run_id: str) -> str:
    """실행 ID(20261125T170512-…)의 조사 시각. 꼴이 다르면 빈 문자열."""
    if len(run_id or "") < 13 or run_id[8] != "T":
        return ""
    return f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]} {run_id[9:11]}:{run_id[11:13]}"

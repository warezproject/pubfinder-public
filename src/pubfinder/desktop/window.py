"""pubfinder Windows 창(tkinter). 화면은 `Controller`만 부르고, 조사는 작업 대기열 스레드에서 돈다.

창은 1초마다 작업 상태를 읽어 진행 단계와 결과를 그린다. 창을 닫으면 진행 중인 조사의 Aside 세션을 멈추고 끝낸다.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Any

from .controller import BatchError, Controller
from .labels import CHECK_LABELS, FIELD_LABELS, STATUS_LABELS, hint_for, run_time
from .storage import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL

WINDOW_TITLE = "pubfinder"
POLL_MS = 1000
FINISHED = {"done", "failed", "cancelled"}
EFFORTS = ("low", "medium", "high")
COLORS = {"bad": "#b3261e", "warn": "#8a5a00", "ok": "#1e6b34", "muted": "#5f6368", "link": "#0b57d0"}
STATUS_COLORS = {"verified": "ok", "partial": "warn", "not_verified": "warn", "not_found": "bad", "blocked": "bad",
                 "failed": "bad", "cancelled": "muted"}
ADOPTION_NOTE = "채택은 두 조사가 서로 모르게 같은 값을 냈다는 뜻입니다. 같은 논문인지는 근거를 열어 확인하세요."


# 공통 부품 ---------------------------------------------------------------------------------------------------------

def setup_style(root: tk.Tk) -> None:
    """맑은 고딕(있으면)과 Windows 기본 테마. 표 줄 높이를 글자 크기에 맞춘다."""
    families = set(tkfont.families(root))
    family = next((name for name in ("Malgun Gothic", "맑은 고딕") if name in families), "")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
        try:
            named = tkfont.nametofont(name)
        except tk.TclError:
            continue
        named.configure(size=10, **({"family": family} if family else {}))
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    base = tkfont.nametofont("TkDefaultFont")
    heading = base.copy()
    heading.configure(size=12, weight="bold")
    root._pubfinder_fonts = [heading]  # type: ignore[attr-defined] - 글꼴 객체가 사라지지 않게 잡아 둔다.
    style.configure("Heading.TLabel", font=heading)
    for name, color in COLORS.items():
        style.configure(f"{name.title()}.TLabel", foreground=color)
        style.configure(f"Heading{name.title()}.TLabel", foreground=color, font=heading)
    style.configure("Treeview", rowheight=base.metrics("linespace") + 8)


class ReadOnlyText(tk.Text):
    """줄바꿈되는 읽기 전용 글 상자. 글을 골라 복사할 수 있다."""

    def __init__(self, parent: tk.Misc, height: int) -> None:
        super().__init__(parent, height=height, wrap="word", relief="flat", borderwidth=0, padx=6, pady=4,
                         highlightthickness=1, highlightbackground="#d0d0d0", font="TkDefaultFont", cursor="arrow")
        for name, color in COLORS.items():
            self.tag_configure(name, foreground=color)
        self.configure(state="disabled")

    def set(self, parts: list[tuple[str, str]] | str) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        for text, tag in ([(parts, "")] if isinstance(parts, str) else parts):
            self.insert("end", text, tag or ())
        self.configure(state="disabled")

    def fit(self, max_lines: int = 6) -> None:
        """높이를 지금 폭에서 보이는 줄 수(줄바꿈 포함)에 맞춘다. 아직 그려지지 않았으면 그대로 둔다."""
        try:
            counted = self.count("1.0", "end-1c", "displaylines")
        except tk.TclError:
            return
        lines = counted[0] if isinstance(counted, tuple) else counted
        if not lines or self.winfo_width() <= 1:
            return
        lines = max(1, min(max_lines, int(lines)))
        if int(self.cget("height")) != lines:
            self.configure(height=lines)


def wrap_label(parent: tk.Misc, text: str = "", style: str = "TLabel") -> ttk.Label:
    """폭에 맞춰 줄바꿈되는 글. 처음 줄바꿈 폭을 정해 둬서, 그려지기 전 긴 글이 창 배치를 넓히지 않게 한다."""
    label = ttk.Label(parent, text=text, style=style, justify="left", wraplength=360)

    def rewrap(event: tk.Event) -> None:
        width = max(200, event.width - 8)
        if abs(width - int(float(str(label.cget("wraplength")) or 0))) > 4:
            label.configure(wraplength=width)

    label.bind("<Configure>", rewrap)
    return label


def place_sash(panes: ttk.PanedWindow, share: float, minimum: int) -> None:
    """창이 처음 그려질 때 나눔 막대를 비율로 놓는다. 그 뒤로는 사용자가 옮긴 자리를 그대로 둔다."""
    horizontal = str(panes.cget("orient")) == "horizontal"

    def place(_event: tk.Event) -> None:
        size = panes.winfo_width() if horizontal else panes.winfo_height()
        if size > 1:
            panes.sashpos(0, max(minimum, int(size * share)))
            panes.unbind("<Map>")

    panes.bind("<Map>", place)


def run_loop(root: tk.Tk, interval: float = 0.04) -> None:
    """`root.mainloop()` 대신 쓰는 이벤트 고리.

    Windows에서 다른 프로그램이 창에 메시지를 보내 그려 가면(창 캡처로 재현, 6번에 1번꼴) Tk의 타이머 깨우기(WM_TIMER)가
    끊겨 `after()`로 건 일(조사 진행 표시)이 멈출 때가 있었다. 짧은 간격으로 update()를 직접 불러, 타이머가 메시지가 아니라
    시각으로 돌게 한다. 창이 닫히면(destroy) 끝난다.
    """
    while True:
        try:
            root.winfo_exists()
            root.update()
        except tk.TclError:
            return
        time.sleep(interval)


def is_web_url(url: str) -> bool:
    return str(url or "").lower().startswith(("http://", "https://"))


# 결과 보기 ---------------------------------------------------------------------------------------------------------

class ResultView(ttk.Frame):
    """조사 한 건의 결과: 상태, 주의, 항목 13개 표, 고른 항목의 값·근거 원문."""

    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent, padding=(8, 4))
        self.app = app
        self.run_id = ""
        self.fields: dict[str, dict] = {}
        self.url = ""
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.status = ttk.Label(head, style="Heading.TLabel")
        self.status.pack(side="left")
        self.open_button = ttk.Button(head, text="결과 폴더 열기", command=self._open)
        self.open_button.pack(side="right")
        self.export_button = ttk.Button(head, text="엑셀로 저장…", command=self._export)
        self.export_button.pack(side="right", padx=(0, 6))
        self.input = wrap_label(self, style="Muted.TLabel")
        self.input.pack(fill="x", pady=(2, 4))
        self.notice = ReadOnlyText(self, height=3)
        # 주의 글이 줄바꿈돼도 다 보이게, 폭이 바뀔 때마다 보이는 줄 수에 맞춰 높이를 고친다.
        self.notice.bind("<Configure>", lambda _event: self.notice.fit(), add="+")
        # 아래쪽(근거 열기 줄, 상세 칸)을 먼저 배치해 창이 작아도 늘 보이게 하고, 항목 표가 남은 자리를 쓴다.
        foot = ttk.Frame(self)
        foot.pack(side="bottom", fill="x")
        self.link_button = ttk.Button(foot, text="근거 열기(웹 브라우저)", command=self._open_link, state="disabled")
        self.link_button.pack(side="left")
        wrap_label(foot, ADOPTION_NOTE, "Muted.TLabel").pack(side="left", fill="x", expand=True, padx=8)
        self.detail = ReadOnlyText(self, height=6)
        self.detail.pack(side="bottom", fill="x", pady=(6, 2))
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("result", "value", "evidence"), show="tree headings",
                                 selectmode="browse", height=6)
        # 값 칸만 늘고 줄어든다. 처음 폭의 합을 좁은 창(한 건 조사 탭 오른쪽)보다 작게 둬 근거 칸이 잘리지 않게 한다.
        for column, text, width, stretch in (("#0", "항목", 110, False), ("result", "결과", 72, False),
                                             ("value", "값 / 사유", 240, True), ("evidence", "근거", 90, False)):
            self.tree.heading(column, text=text, anchor="w")
            self.tree.column(column, width=width, minwidth=width if not stretch else 120, stretch=stretch, anchor="w")
        self.tree.tag_configure("adopted", foreground=COLORS["ok"])
        self.tree.tag_configure("not", foreground=COLORS["muted"])
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_detail())
        self.clear()

    def clear(self, text: str = "") -> None:
        self.run_id, self.fields, self.url = "", {}, ""
        self.status.configure(text=text or "결과가 여기에 나옵니다", style="Muted.TLabel")
        self.input.configure(text="")
        self.notice.pack_forget()
        self.tree.delete(*self.tree.get_children())
        self.detail.set("")
        for button in (self.export_button, self.open_button, self.link_button):
            button.configure(state="disabled")

    def show_run(self, run_id: str) -> None:
        try:
            result = self.app.controller.result(run_id)
        except (OSError, ValueError) as exc:
            self.clear()
            self.status.configure(text=f"결과를 읽지 못했습니다: {exc}", style="Bad.TLabel")
            return
        self.render(result, run_id)

    def render(self, result: dict, run_id: str) -> None:
        self.clear()
        self.run_id = run_id
        status = str(result.get("status") or "")
        color = STATUS_COLORS.get(status, "")
        text = " · ".join(part for part in (STATUS_LABELS.get(status, status), str(result.get("status_text") or ""))
                          if part)
        self.status.configure(text=text, style=f"Heading{color.title()}.TLabel" if color else "Heading.TLabel")
        query = result.get("query") or {}
        line = f"입력: {query.get('title', '')} / {query.get('venue', '')} / {', '.join(query.get('authors') or [])}"
        self.input.configure(text=line + (f" / {query['year']}" if query.get("year") else "")
                             + (f"    (조사 {run_time(run_id)})" if run_time(run_id) else ""))
        parts: list[tuple[str, str]] = []
        if status == "failed":
            reason = str(result.get("reason") or "")
            parts.append((f"사유: {reason}\n", "bad"))
            if hint_for(reason):
                parts.append((hint_for(reason) + "\n", "bad"))
        if result.get("warnings"):
            parts.append(("주의\n", "warn"))
            parts += [(f"• {warning}\n", "warn") for warning in result["warnings"]]
        if parts:
            self.notice.configure(height=min(6, sum(text.count("\n") for text, _ in parts)))
            self.notice.set(parts)
            self.notice.pack(fill="x", pady=(0, 6), before=self.tree.master)
            self.notice.fit()
        self.fields = result.get("fields") or {}
        for name, label in FIELD_LABELS.items():
            field = self.fields.get(name) or {}
            adopted = field.get("status") == "adopted"
            evidence = field.get("evidence") or {}
            if adopted:
                value = str(field.get("value") or "") + (f" [{field['country_code']}]" if field.get("country_code") else "")
                proof = ("인용 확인" if evidence.get("quote_verified") else "인용 미확인") if evidence.get("url") else ""
            else:
                value, proof = str(field.get("reason_text") or ""), ""
            self.tree.insert("", "end", iid=name, text=label, values=("✓ 채택" if adopted else "· 미채택", value, proof),
                             tags=("adopted" if adopted else "not",))
        if self.fields:
            self.detail.set([("줄을 고르면 그 항목의 값, 두 조사가 낸 값, 근거 주소와 원문 인용이 여기에 나옵니다.", "muted")])
        self.export_button.configure(state="normal")
        self.open_button.configure(state="normal")

    def _show_detail(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        name = selected[0]
        field = self.fields.get(name) or {}
        adopted = field.get("status") == "adopted"
        evidence = field.get("evidence") or {}
        parts: list[tuple[str, str]] = [(f"{FIELD_LABELS.get(name, name)} — ", ""),
                                        ("채택" if adopted else "미채택", "ok" if adopted else "muted"), ("\n", "")]
        if adopted:
            code = f" [{field['country_code']}]" if field.get("country_code") else ""
            parts.append((f"값: {field.get('value', '')}{code}\n", ""))
        elif field.get("reason_text"):
            parts.append((f"사유: {field['reason_text']}\n", ""))
        candidates = field.get("candidates") or {}
        for label, key in (("1차 조사", "primary"), ("2차 조사", "review")):
            value = (candidates.get(key) or {}).get("value")
            if value:
                parts.append((f"{label}: {value}\n", "muted"))
        self.url = str(evidence.get("url") or "") if adopted else ""
        if self.url:
            parts.append((f"근거: {self.url}", "link"))
            parts.append((" · 인용 확인\n" if evidence.get("quote_verified") else " · 인용 미확인\n", ""))
        if adopted and evidence.get("quote"):
            parts.append((f"“{evidence['quote']}”", "muted"))
        self.detail.set(parts)
        self.link_button.configure(state="normal" if is_web_url(self.url) else "disabled")

    def _open_link(self) -> None:
        if is_web_url(self.url):
            webbrowser.open(self.url)

    def _export(self) -> None:
        if self.run_id:
            self.app.export([self.run_id], f"pubfinder-{self.run_id}.csv")

    def _open(self) -> None:
        if self.run_id:
            self.app.open_folder(self.run_id)


# 한 건 조사 --------------------------------------------------------------------------------------------------------

class SearchTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.job_id = ""
        self.started = 0.0
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        form = ttk.Frame(panes, padding=12)
        self.result = ResultView(panes, app)
        panes.add(form, weight=2)
        panes.add(self.result, weight=5)
        place_sash(panes, 0.34, 340)
        ttk.Label(form, text="논문 한 편 조사", style="Heading.TLabel").pack(anchor="w")
        self.title = self._text(form, "논문명", "한글 또는 영문", 3)
        self.venue = self._entry(form, "학술대회명", "예: CHI 2026, 한국컴퓨터종합학술대회 2026")
        self.year = self._entry(form, "연도", "선택, 넣기를 권함 · 예: 2026", width=12)
        self.authors = self._text(form, "저자", "한 줄에 한 명, 또는 세미콜론(;)으로 구분", 5)
        actions = ttk.Frame(form)
        actions.pack(fill="x", pady=(10, 4))
        self.start_button = ttk.Button(actions, text="조사 시작", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(actions, text="입력 지우기", command=self.reset).pack(side="left", padx=6)
        wrap_label(form, "한 건에 보통 1~4분, Aside에 연결한 모델 사용료가 듭니다(평균 약 0.35달러).",
                   "Muted.TLabel").pack(fill="x")
        self.message = wrap_label(form, style="Bad.TLabel")
        self.message.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Frame(form)
        self.bar = ttk.Progressbar(self.progress, mode="indeterminate")
        self.bar.pack(fill="x")
        line = ttk.Frame(self.progress)
        line.pack(fill="x", pady=(4, 0))
        self.stage = ttk.Label(line, text="대기 중")
        self.stage.pack(side="left")
        self.elapsed = ttk.Label(line, style="Muted.TLabel")
        self.elapsed.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(line, text="취소", command=self.cancel)
        self.cancel_button.pack(side="right")

    def _label(self, parent: tk.Misc, text: str, hint: str) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 2))
        ttk.Label(row, text=text).pack(side="left")
        ttk.Label(row, text=hint, style="Muted.TLabel").pack(side="left", padx=6)

    def _text(self, parent: tk.Misc, text: str, hint: str, height: int) -> tk.Text:
        self._label(parent, text, hint)
        box = tk.Text(parent, height=height, width=40, wrap="word", font="TkDefaultFont", undo=True,
                      relief="flat", borderwidth=0, highlightthickness=1, highlightbackground="#adadad",
                      highlightcolor="#0078d4", padx=4, pady=3)
        box.pack(fill="x")
        # 탭 키로 다음 칸에 가게 한다(글 상자 안에 탭 글자를 넣지 않는다).
        box.bind("<Tab>", lambda event: (event.widget.tk_focusNext().focus_set(), "break")[1])
        return box

    def _entry(self, parent: tk.Misc, text: str, hint: str, width: int = 40) -> ttk.Entry:
        self._label(parent, text, hint)
        entry = ttk.Entry(parent, width=width)
        entry.pack(anchor="w", fill="x" if width >= 40 else "none")
        return entry

    def reset(self) -> None:
        for box in (self.title, self.authors):
            box.delete("1.0", "end")
        for entry in (self.venue, self.year):
            entry.delete(0, "end")
        self.message.configure(text="")

    def start(self) -> None:
        self.message.configure(text="")
        if not self.app.save_settings_if_dirty(self.message):
            return
        try:
            job = self.app.controller.submit_search(self.title.get("1.0", "end"), self.venue.get(),
                                                    self.authors.get("1.0", "end"), self.year.get())
        except ValueError as exc:
            self.message.configure(text=f"{exc} {hint_for(str(exc))}".strip())
            return
        self.job_id, self.started = job.id, time.monotonic()
        self.result.clear("조사 중입니다…")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.stage.configure(text="대기 중")
        self.progress.pack(fill="x", pady=(10, 0))
        self.bar.start(15)
        self.app.refresh_state()
        self.poll()

    def poll(self) -> None:
        view = self.app.controller.job(self.job_id)
        if view is None:
            self.finish(None)
            return
        item = (view.get("items") or [{}])[0]
        if view["state"] == "queued":
            stage = "앞 작업이 끝나기를 기다리는 중"
        else:
            stage = item.get("stage") or STATUS_LABELS.get(item.get("state", ""), "")
        if self.cancel_button.instate(["disabled"]):
            stage = "취소하는 중(Aside 세션을 멈추는 중)"
        self.stage.configure(text=stage)
        self.elapsed.configure(text=f"{int(time.monotonic() - self.started)}초")
        if view["state"] in FINISHED:
            self.finish(view)
        else:
            self.app.later(POLL_MS, self.poll)

    def finish(self, view: dict | None) -> None:
        self.bar.stop()
        self.progress.pack_forget()
        self.start_button.configure(state="normal")
        item = ((view or {}).get("items") or [{}])[0]
        if item.get("run_id"):
            self.result.show_run(item["run_id"])
        else:
            self.result.clear()
            error = item.get("error") or (view or {}).get("error") or "조사를 끝내지 못했습니다"
            if (view or {}).get("state") == "cancelled":
                error = "조사를 취소했습니다."
            self.message.configure(text=f"{error} {hint_for(error)}".strip())
        self.job_id = ""
        self.app.refresh_state()

    def cancel(self) -> None:
        if self.job_id:
            self.app.controller.cancel(self.job_id)
            self.cancel_button.configure(state="disabled")
            self.stage.configure(text="취소하는 중(Aside 세션을 멈추는 중)")


# 여러 건 조사 ------------------------------------------------------------------------------------------------------

def read_csv_file(path: Path) -> tuple[str, str]:
    """CSV 글과 안내. 엑셀의 'CSV UTF-8'이 아니라 일반 'CSV'(한국어 Windows는 CP949)로 저장한 파일도 읽는다."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace"), "UTF-8이 아니어서 한국어 Windows 기본 인코딩(CP949)으로 읽었습니다."


class BatchTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.job: dict = {}
        self.path: Path | None = None
        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True)
        top = ttk.Frame(panes, padding=12)
        self.result = ResultView(panes, app)
        panes.add(top, weight=2)
        panes.add(self.result, weight=5)
        place_sash(panes, 0.38, 180)
        ttk.Label(top, text="여러 건 조사", style="Heading.TLabel").pack(anchor="w")
        wrap_label(top, "엑셀에서 아래 칸으로 표를 만들고 CSV UTF-8 형식으로 저장해 고르세요. 칸 이름은 title, venue, authors, "
                        "year 또는 논문명, 학술대회명, 저자, 연도입니다. 저자가 여러 명이면 세미콜론(;)으로 나눕니다. "
                        "한 번에 200건까지 받고, 한 건씩 차례로 조사합니다.", "Muted.TLabel").pack(fill="x", pady=(2, 6))
        actions = ttk.Frame(top)
        actions.pack(fill="x")
        ttk.Button(actions, text="CSV 파일 고르기…", command=self.choose).pack(side="left")
        self.file_label = ttk.Label(actions, text="고른 파일 없음", style="Muted.TLabel")
        self.file_label.pack(side="left", padx=8)
        self.start_button = ttk.Button(actions, text="조사 시작", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="right")
        ttk.Button(actions, text="예시 CSV 저장…", command=self.save_sample).pack(side="right", padx=6)
        self.message = wrap_label(top)
        self.message.pack(fill="x", pady=(6, 0))
        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(6, 4))
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left")
        self.cancel_button = ttk.Button(bar, text="취소", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")
        self.export_button = ttk.Button(bar, text="엑셀로 저장…", command=self.export, state="disabled")
        self.export_button.pack(side="right", padx=6)
        body = ttk.Frame(top)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("title", "venue", "state", "date", "adopted"), show="tree headings",
                                 selectmode="browse", height=6)
        for column, text, width, stretch in (("#0", "#", 50, False), ("title", "논문명", 380, True),
                                             ("venue", "학술대회", 180, True), ("state", "진행", 220, True),
                                             ("date", "발표일", 100, False), ("adopted", "채택", 60, False)):
            self.tree.heading(column, text=text, anchor="w")
            self.tree.column(column, width=width, minwidth=40, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._select())
        ttk.Label(top, text="줄을 고르면 아래에 결과가 나옵니다.", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def choose(self) -> None:
        name = filedialog.askopenfilename(parent=self, title="조사할 CSV 고르기",
                                          filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")])
        if name:
            self.path = Path(name)
            self.file_label.configure(text=self.path.name, style="TLabel")

    def save_sample(self) -> None:
        name = filedialog.asksaveasfilename(parent=self, title="예시 CSV 저장", initialfile="pubfinder-sample.csv",
                                            defaultextension=".csv", filetypes=[("CSV 파일", "*.csv")])
        if name:
            try:
                self.app.controller.save_sample_csv(name)
            except OSError as exc:
                messagebox.showerror("pubfinder", f"저장하지 못했습니다: {exc}", parent=self)
                return
            self.app.flash(f"예시 CSV를 저장했습니다: {name}")

    def _message(self, text: str, kind: str = "") -> None:
        self.message.configure(text=text, style=f"{kind.title()}.TLabel" if kind else "TLabel")

    def start(self) -> None:
        if self.path is None:
            self._message("CSV 파일을 고르세요.", "bad")
            return
        try:
            text, note = read_csv_file(self.path)
        except OSError as exc:
            self._message(f"파일을 읽지 못했습니다: {exc}", "bad")
            return
        if not self.app.save_settings_if_dirty(self.message):
            return
        try:
            job, errors = self.app.controller.submit_batch(text)
        except BatchError as exc:
            self._message(" · ".join([str(exc), *exc.errors]), "bad")
            return
        except ValueError as exc:
            self._message(f"{exc} {hint_for(str(exc))}".strip(), "bad")
            return
        notes = [note] if note else []
        if errors:
            self._message(" · ".join(notes + ["건너뛴 줄: " + " · ".join(errors)]), "warn")
        else:
            self._message(" ".join(notes + [f"{len(job.items)}건을 차례로 조사합니다."]))
        self.tree.delete(*self.tree.get_children())
        self.result.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.job = job.view()
        self.app.refresh_state()
        self.poll()

    def poll(self) -> None:
        view = self.app.controller.job(self.job.get("id", ""))
        if view is None:
            self.start_button.configure(state="normal")
            return
        self.job = view
        self.render(view)
        if view["state"] in FINISHED:
            self.start_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.app.refresh_state()
        else:
            self.app.later(POLL_MS, self.poll)

    def render(self, job: dict) -> None:
        state = STATUS_LABELS.get(job["state"], job["state"])
        self.status.configure(text=f"{state} · {job['done']}/{job['total']}건")
        for item in job["items"]:
            summary = item.get("summary") or {}
            if item["state"] == "running":
                progress = item.get("stage") or "조사 중"
            else:
                progress = STATUS_LABELS.get(summary.get("status") or item["state"], item["state"])
                if item.get("error"):
                    progress += f" · {item['error']}"
            adopted = "" if summary.get("adopted_count") is None else f"{summary['adopted_count']}/13"
            values = (item["title"], item["venue"], progress, summary.get("presentation_date", ""), adopted)
            iid = str(item["index"])
            if self.tree.exists(iid):
                self.tree.item(iid, values=values)
            else:
                self.tree.insert("", "end", iid=iid, text=str(item["index"] + 1), values=values)
        self.export_button.configure(state="normal" if self._run_ids() else "disabled")

    def _run_ids(self) -> list[str]:
        return [item["run_id"] for item in self.job.get("items") or [] if item.get("run_id")]

    def _select(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item = next((item for item in self.job.get("items") or [] if str(item["index"]) == selected[0]), {})
        if item.get("run_id"):
            self.result.show_run(item["run_id"])
        else:
            self.result.clear("아직 결과가 없습니다")

    def export(self) -> None:
        if self._run_ids():
            self.app.export(self._run_ids(), "pubfinder-batch.csv")

    def cancel(self) -> None:
        if self.job.get("id"):
            self.app.controller.cancel(self.job["id"])
            self.cancel_button.configure(state="disabled")
            self._message("취소하는 중입니다. 진행 중인 건의 Aside 세션을 멈추고 나머지는 조사하지 않습니다.", "warn")


# 지난 결과 ---------------------------------------------------------------------------------------------------------

class ResultsTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.rows: list[dict] = []
        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True)
        top = ttk.Frame(panes, padding=12)
        self.result = ResultView(panes, app)
        panes.add(top, weight=2)
        panes.add(self.result, weight=5)
        place_sash(panes, 0.38, 180)
        bar = ttk.Frame(top)
        bar.pack(fill="x")
        ttk.Label(bar, text="지난 결과", style="Heading.TLabel").pack(side="left")
        ttk.Button(bar, text="결과 폴더 열기", command=lambda: self.app.open_folder()).pack(side="right")
        ttk.Button(bar, text="모두 엑셀로 저장…", command=self.export_all).pack(side="right", padx=6)
        ttk.Button(bar, text="고른 것 엑셀로 저장…", command=self.export_selected).pack(side="right")
        ttk.Button(bar, text="새로고침", command=self.load).pack(side="right", padx=6)
        body = ttk.Frame(top)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(body, columns=("time", "title", "venue", "status", "date", "adopted"), show="headings",
                                 selectmode="extended", height=5)
        for column, text, width, stretch in (("time", "조사 시각", 130, False), ("title", "논문명", 380, True),
                                             ("venue", "학술대회", 180, True), ("status", "상태", 100, False),
                                             ("date", "발표일", 100, False), ("adopted", "채택", 60, False)):
            self.tree.heading(column, text=text, anchor="w")
            self.tree.column(column, width=width, minwidth=40, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._select())
        self.empty = ttk.Label(top, text="줄을 고르면 아래에 결과가 나옵니다. Ctrl이나 Shift를 누른 채 누르면 여러 줄을 고릅니다.",
                               style="Muted.TLabel")
        self.empty.pack(anchor="w", pady=(4, 0))

    def load(self) -> None:
        self.rows = self.app.controller.results()
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            adopted = "" if row["status"] == "failed" else f"{row['adopted_count']}/13"
            self.tree.insert("", "end", iid=row["run_id"], values=(
                run_time(row["run_id"]), row["title"], row["venue"], STATUS_LABELS.get(row["status"], row["status"]),
                row["presentation_date"], adopted))
        if not self.rows:
            self.result.clear("아직 조사한 결과가 없습니다")

    def _select(self) -> None:
        selected = self.tree.selection()
        if selected:
            focus = self.tree.focus()
            self.result.show_run(focus if focus in selected else selected[0])

    def export_selected(self) -> None:
        selected = list(self.tree.selection())
        if not selected:
            self.app.flash("엑셀로 저장할 줄을 먼저 고르세요.")
            return
        self.app.export(selected, "pubfinder-selected.csv")

    def export_all(self) -> None:
        if self.rows:
            self.app.export([row["run_id"] for row in self.rows], "pubfinder-all.csv")


# 설정·점검 ---------------------------------------------------------------------------------------------------------

class SettingsTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.dirty = False
        self._loading = False
        self.model = tk.StringVar()
        self.fallback = tk.StringVar()
        self.effort = tk.StringVar()
        self.timeout = tk.StringVar()
        self.executable = tk.StringVar()
        self.korean = tk.BooleanVar(value=True)
        self.columnconfigure(0, weight=1, uniform="half")
        self.columnconfigure(1, weight=1, uniform="half")
        self.rowconfigure(0, weight=1)
        form = ttk.Frame(self, padding=(0, 0, 12, 0))
        form.grid(row=0, column=0, sticky="nsew")
        check = ttk.Frame(self, padding=(12, 0, 0, 0))
        check.grid(row=0, column=1, sticky="nsew")
        ttk.Label(form, text="조사 설정", style="Heading.TLabel").pack(anchor="w")
        models = (DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL)
        self._field(form, "Aside 모델", "공급자/모델 이름. Aside 앱 설정에서 연결한 모델. 기본: "
                    f"{DEFAULT_MODEL}(Claude 계정)", ttk.Combobox(form, textvariable=self.model, values=models))
        self._field(form, "주 모델을 못 쓸 때 쓸 모델", "사용량 한도·잔액 부족·공급자 연결 문제로 위 모델이 거절되면 이 모델로 "
                    f"그 세션을 다시 합니다. 비우면 바꾸지 않습니다. 기본: {DEFAULT_FALLBACK_MODEL}(Anthropic API)",
                    ttk.Combobox(form, textvariable=self.fallback, values=models))
        row = ttk.Frame(form)
        row.pack(fill="x", pady=(10, 0))
        left = ttk.Frame(row)
        left.pack(side="left")
        ttk.Label(left, text="추론 강도").pack(anchor="w")
        effort_row = ttk.Frame(left)
        effort_row.pack(anchor="w")
        ttk.Combobox(effort_row, textvariable=self.effort, values=EFFORTS, state="readonly", width=10).pack(side="left")
        self.effort_missing = ttk.Label(effort_row, text="← 고르세요", style="Bad.TLabel")
        right = ttk.Frame(row)
        right.pack(side="left", padx=24)
        ttk.Label(right, text="조사 제한 시간(초)").pack(anchor="w")
        ttk.Spinbox(right, textvariable=self.timeout, from_=60, to=86400, increment=60, width=8).pack(anchor="w")
        ttk.Label(form, text="Aside CLI 경로").pack(anchor="w", pady=(10, 0))
        exe_row = ttk.Frame(form)
        exe_row.pack(fill="x")
        ttk.Entry(exe_row, textvariable=self.executable).pack(side="left", fill="x", expand=True)
        ttk.Button(exe_row, text="찾아보기…", command=self.browse).pack(side="left", padx=(6, 0))
        self.exe_hint = wrap_label(form, style="Muted.TLabel")
        self.exe_hint.pack(fill="x")
        ttk.Checkbutton(form, text="국가명 없는 한글 장소를 한국 개최로 본다", variable=self.korean).pack(anchor="w", pady=(10, 0))
        actions = ttk.Frame(form)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="저장", style="Accent.TButton", command=self.save).pack(side="left")
        self.saved = wrap_label(actions, style="Muted.TLabel")
        self.saved.pack(side="left", fill="x", expand=True, padx=8)
        self.current = wrap_label(form, style="Muted.TLabel")
        self.current.pack(fill="x", pady=(8, 0))
        ttk.Label(check, text="Aside 점검", style="Heading.TLabel").pack(anchor="w")
        wrap_label(check, "Aside가 설치·로그인돼 있고 브라우저가 연결됐는지 봅니다. 기본 점검은 모델을 부르지 않습니다.",
                   "Muted.TLabel").pack(fill="x")
        buttons = ttk.Frame(check)
        buttons.pack(fill="x", pady=(8, 4))
        self.check_button = ttk.Button(buttons, text="점검", style="Accent.TButton", command=lambda: self.run_check(False))
        self.check_button.pack(side="left")
        self.exec_button = ttk.Button(buttons, text="모델까지 점검(사용료 조금)", command=lambda: self.run_check(True))
        self.exec_button.pack(side="left", padx=6)
        self.check_stage = wrap_label(check)
        self.check_stage.pack(fill="x")
        self.report = ReadOnlyText(check, height=14)
        self.report.pack(fill="both", expand=True, pady=(4, 0))
        self.folder = ttk.Frame(self)
        self.folder.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.folder_label = wrap_label(self.folder, style="Muted.TLabel")
        self.folder_label.pack(side="left", fill="x", expand=True)
        ttk.Button(self.folder, text="데이터 폴더 열기", command=self.open_data_dir).pack(side="right")
        for variable in (self.model, self.fallback, self.effort, self.timeout, self.executable, self.korean):
            variable.trace_add("write", self._changed)

    def _field(self, parent: tk.Misc, text: str, hint: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=text).pack(anchor="w", pady=(10, 0))
        widget.pack(fill="x")
        wrap_label(parent, hint, "Muted.TLabel").pack(fill="x")

    def _changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self.dirty = True
        self.saved.configure(text="저장하지 않은 변경이 있습니다. 점검이나 조사를 시작하면 먼저 저장합니다.", style="Warn.TLabel")
        self._mark_missing()

    def _mark_missing(self) -> None:
        if self.effort.get():
            self.effort_missing.pack_forget()
        else:
            self.effort_missing.pack(side="left", padx=6)

    def load(self) -> None:
        state = self.app.controller.state()
        settings = state["settings"]
        self.show_current(settings)
        self.folder_label.configure(text=f"데이터 폴더: {state['data_dir']} · 설정은 이 폴더의 pubfinder.local.toml, "
                                         "결과는 pubfinder-runs 폴더에 남습니다.")
        if self.dirty:
            return
        self._loading = True
        try:
            self.model.set(settings["model"])
            self.fallback.set(settings.get("fallback_model") or "")
            self.effort.set(settings["effort"])
            self.timeout.set(str(int(float(settings["research_timeout"]))))
            self.executable.set(settings["executable"])
            self.korean.set(bool(settings["korean_place_as_korea"]))
        finally:
            self._loading = False
        self.exe_hint.configure(text=f"비우면 기본 경로: {settings['default_executable']}")
        self._mark_missing()

    def show_current(self, settings: dict) -> None:
        # 화면 칸이 아니라 설정 파일에 저장된 값. 점검과 조사는 이 값으로 돈다.
        self.current.configure(text=f"점검·조사에 쓰는 설정: 모델 {settings['model'] or '(비어 있음)'} · 추론 강도 "
                                    f"{settings['effort'] or '(비어 있음)'} · 못 쓸 때 "
                                    f"{settings.get('fallback_model') or '(바꾸지 않음)'}")

    def values(self) -> dict:
        try:
            timeout = float(self.timeout.get() or 1200)
        except ValueError as exc:
            raise ValueError("조사 제한 시간은 초 단위 숫자로 적으세요") from exc
        return {"model": self.model.get(), "fallback_model": self.fallback.get(), "effort": self.effort.get(),
                "executable": self.executable.get(), "research_timeout": timeout,
                "korean_place_as_korea": bool(self.korean.get())}

    def save(self) -> bool:
        try:
            settings = self.app.controller.save_settings(self.values())
        except (ValueError, OSError) as exc:
            self.saved.configure(text=f"저장하지 못했습니다: {exc}", style="Bad.TLabel")
            return False
        self.dirty = False
        self.saved.configure(text="저장했습니다.", style="Ok.TLabel")
        self.show_current(settings)
        self.app.refresh_state()
        return True

    def browse(self) -> None:
        name = filedialog.askopenfilename(parent=self, title="Aside CLI(aside.exe) 고르기",
                                          filetypes=[("프로그램", "*.exe"), ("모든 파일", "*.*")])
        if name:
            self.executable.set(name)

    def open_data_dir(self) -> None:
        self.app.open_path(Path(self.app.controller.store.data_dir))

    def run_check(self, with_exec: bool) -> None:
        self.report.set("")
        if self.dirty and not self.save():
            self.check_stage.configure(text="설정을 저장하지 못해 점검하지 않았습니다.", style="Bad.TLabel")
            return
        job = self.app.controller.submit_check(with_exec=with_exec)
        for button in (self.check_button, self.exec_button):
            button.configure(state="disabled")
        self.check_stage.configure(text="점검 중…", style="TLabel")
        self.app.refresh_state()
        self._poll_check(job.id)

    def _poll_check(self, job_id: str) -> None:
        view = self.app.controller.job(job_id)
        if view is not None and view["state"] not in FINISHED:
            self.check_stage.configure(text="앞 작업이 끝나기를 기다리는 중" if view["state"] == "queued" else "점검 중…")
            self.app.later(POLL_MS, lambda: self._poll_check(job_id))
            return
        for button in (self.check_button, self.exec_button):
            button.configure(state="normal")
        self.app.refresh_state()
        if view is None or view.get("error"):
            error = (view or {}).get("error") or "점검을 끝내지 못했습니다"
            self.check_stage.configure(text=f"{error} {hint_for(error)}".strip(), style="Bad.TLabel")
            return
        report = view.get("report") or {}
        if report.get("ok"):
            self.check_stage.configure(text=f"모두 통과했습니다. Aside 판 {report.get('aside_version') or ''}".strip(),
                                       style="Ok.TLabel")
        else:
            self.check_stage.configure(text="통과하지 못한 항목이 있습니다.", style="Bad.TLabel")
        self.report.set(self.report_parts(report))

    @staticmethod
    def report_parts(report: dict) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        for check in report.get("checks") or []:
            detail_value = check.get("detail")
            is_object = isinstance(detail_value, dict)
            detail = str((detail_value.get("reason") or detail_value.get("status") or "") if is_object
                         else (detail_value or ""))
            # 안내는 종류(kind)로 고른다. 공급자 오류 문구 속 낱말이 엉뚱한 안내와 맞지 않게 한다.
            hint = hint_for(detail_value.get("kind") if is_object and detail_value.get("kind") else detail)
            name = CHECK_LABELS.get(check.get("name", ""), check.get("name", ""))
            parts.append(("통과  " if check.get("ok") else "실패  ", "ok" if check.get("ok") else "bad"))
            parts.append((name + "\n", ""))
            if check.get("ok") and check.get("name") in {"settings", "model_fallback"} and detail:
                parts.append((f"      {detail}\n", "muted"))
            elif not check.get("ok"):
                if detail:
                    parts.append((f"      {detail}\n", "bad"))
                if hint:
                    parts.append((f"      {hint}\n", "warn"))
        return parts


# 창 ---------------------------------------------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk, controller: Controller) -> None:
        self.root = root
        self.controller = controller
        self.closing = False
        self._closed = threading.Event()
        root.title(WINDOW_TITLE)
        scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        root.geometry(f"{int(1180 * scale)}x{int(780 * scale)}")
        root.minsize(int(960 * scale), int(640 * scale))
        setup_style(root)
        outer = ttk.Frame(root)
        outer.pack(fill="both", expand=True)
        # 상태 표시줄을 먼저 아래에 붙인다. 나중에 붙이면 창보다 내용이 클 때 밀려나 안 보인다.
        status = ttk.Frame(outer, padding=(10, 4))
        status.pack(side="bottom", fill="x")
        self.status = ttk.Label(status, text="")
        self.status.pack(side="left")
        ttk.Label(status, text=f"pubfinder {controller.state()['version']}", style="Muted.TLabel").pack(side="right")
        self.busy = ttk.Label(status, text="", style="Warn.TLabel")
        self.busy.pack(side="right", padx=12)
        self.banner = ttk.Frame(outer, padding=(12, 8))
        wrap_label(self.banner, "추론 강도가 아직 정해지지 않았습니다. 설정 탭에서 모델을 확인하고 추론 강도를 고른 뒤 저장하세요. "
                                "설정은 exe가 있는 폴더에 남으므로 exe를 새 폴더에 두면 다시 정해야 합니다.",
                   "Warn.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(self.banner, text="설정 열기", command=lambda: self.notebook.select(self.settings_tab)).pack(side="right")
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self.search_tab = SearchTab(self.notebook, self)
        self.batch_tab = BatchTab(self.notebook, self)
        self.results_tab = ResultsTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        for tab, text in ((self.search_tab, "한 건 조사"), (self.batch_tab, "여러 건 조사"),
                          (self.results_tab, "지난 결과"), (self.settings_tab, "설정")):
            self.notebook.add(tab, text=f"  {text}  ")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self._tab_changed())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.settings_tab.load()
        self.refresh_state()
        self.later(2000, self._tick)

    # 공통 동작 ---------------------------------------------------------------------------------------------------

    def later(self, ms: int, callback: Any) -> None:
        """창이 닫히는 중이면 다음 작업을 걸지 않는다."""
        if not self.closing:
            self.root.after(ms, callback)

    def flash(self, text: str) -> None:
        self.status.configure(text=text, style="TLabel")

    def refresh_state(self) -> None:
        state = self.controller.state()
        if state["settings"]["configured"]:
            self.banner.pack_forget()
        elif not self.banner.winfo_manager():
            self.banner.pack(fill="x", before=self.notebook)
        self.settings_tab.show_current(state["settings"])
        self.busy.configure(text="조사 진행 중" if state["busy"] else "")

    def _tick(self) -> None:
        self.busy.configure(text="조사 진행 중" if self.controller.busy() else "")
        self.later(2000, self._tick)

    def _tab_changed(self) -> None:
        if self.notebook.select() == str(self.results_tab):
            self.results_tab.load()

    def save_settings_if_dirty(self, message: ttk.Label) -> bool:
        """점검과 조사는 저장된 설정으로 돈다. 저장하지 않은 변경이 있으면 먼저 저장한다."""
        if self.settings_tab.dirty and not self.settings_tab.save():
            message.configure(text="설정을 저장하지 못했습니다. 설정 탭을 확인하세요.", style="Bad.TLabel")
            return False
        return True

    def export(self, run_ids: list[str], filename: str) -> None:
        name = filedialog.asksaveasfilename(parent=self.root, title="엑셀(CSV)로 저장", initialfile=filename,
                                            defaultextension=".csv", filetypes=[("CSV 파일", "*.csv")])
        if not name:
            return
        try:
            count = self.controller.export(run_ids, name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("pubfinder", f"저장하지 못했습니다: {exc}", parent=self.root)
            return
        self.flash(f"{count}건을 저장했습니다: {name}")

    def open_folder(self, run_id: str = "") -> None:
        try:
            self.controller.open_folder(run_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("pubfinder", f"폴더를 열지 못했습니다: {exc}", parent=self.root)

    def open_path(self, path: Path) -> None:
        try:
            self.controller.opener(path)
        except OSError as exc:
            messagebox.showerror("pubfinder", f"폴더를 열지 못했습니다: {exc}", parent=self.root)

    # 닫기 --------------------------------------------------------------------------------------------------------

    def close(self, *, ask: bool = True) -> None:
        if self.closing:
            return
        if ask and self.controller.busy() and not messagebox.askyesno(
                "pubfinder", "조사가 진행 중입니다. 종료하면 조사를 멈춥니다. 종료할까요?", parent=self.root):
            return
        self.closing = True
        self.status.configure(text="종료하는 중… 진행 중인 조사의 Aside 세션을 멈추고 있습니다.", style="Warn.TLabel")
        for index in range(len(self.notebook.tabs())):
            self.notebook.tab(index, state="disabled")
        worker = threading.Thread(target=self._shutdown, name="pubfinder-shutdown", daemon=True)
        worker.start()
        self.root.after(200, self._wait_closed)

    def _shutdown(self) -> None:
        try:
            self.controller.shutdown()
        finally:
            self._closed.set()

    def _wait_closed(self) -> None:
        if self._closed.is_set():
            self.root.destroy()
        else:
            self.root.after(200, self._wait_closed)

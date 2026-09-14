"""Do thoi gian pipeline. Chi log + ghi timing.json — khong doi thu tu/xu ly."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .utils import log

_state: dict[str, Any] | None = None
_where: ContextVar[str] = ContextVar("dyflow_timing_where", default="")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def begin(job_id: str, work_dir: Path | str, *, video_dur: float | None = None) -> None:
    global _state
    _state = {
        "job_id": job_id,
        "work_dir": str(work_dir),
        "video_dur_sec": video_dur,
        "started_at": _now(),
        "t0": time.perf_counter(),
        "steps": [],
        "notes": [],
        "llm": [],
        "tts": [],
        "segments": [],
        "buckets": [],
    }
    log.info("TIMING JOB START %s at %s", job_id, _state["started_at"])


def set_video_dur(sec: float) -> None:
    if _state is not None:
        _state["video_dur_sec"] = float(sec)


def note(msg: str, **fields: Any) -> None:
    row = {"at": _now(), "msg": msg, **fields}
    if _state is not None:
        _state["notes"].append(row)
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    log.info("TIMING NOTE %s%s", msg, f" {extra}" if extra else "")


@contextmanager
def where(name: str) -> Iterator[None]:
    """Gan nhan cho LLM/TTS/bucket (translate_budgeted vs Voiceover)."""
    token = _where.set(str(name or ""))
    try:
        yield
    finally:
        _where.reset(token)


def add_bucket(
    name: str,
    dur_sec: float,
    *,
    log_line: bool = True,
    **fields: Any,
) -> None:
    """Cong don 1 khoi noi bo (sleep, prefetch, parse, ffprobe, fallback...)."""
    row = {
        "name": str(name),
        "dur_sec": round(float(dur_sec), 4),
        "where": _where.get() or fields.pop("where", "") or "",
        "at": _now(),
        **fields,
    }
    if _state is not None:
        _state.setdefault("buckets", []).append(row)
    if log_line:
        extra = " ".join(
            f"{k}={v}" for k, v in fields.items() if v is not None and k != "where"
        )
        loc = f" [{row['where']}]" if row.get("where") else ""
        log.info(
            "TIMING INNER%s %s dur=%.3fs%s",
            loc, name, dur_sec, f" {extra}" if extra else "",
        )


@contextmanager
def phase(name: str, **fields: Any) -> Iterator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add_bucket(name, time.perf_counter() - t0, **fields)


def timed_sleep(seconds: float, reason: str) -> None:
    sec = max(0.0, float(seconds or 0))
    if sec <= 0:
        return
    t0 = time.perf_counter()
    time.sleep(sec)
    add_bucket("sleep", time.perf_counter() - t0, reason=reason)


async def timed_async_sleep(seconds: float, reason: str) -> None:
    sec = max(0.0, float(seconds or 0))
    if sec <= 0:
        return
    t0 = time.perf_counter()
    await asyncio.sleep(sec)
    add_bucket("sleep", time.perf_counter() - t0, reason=reason)


def add_llm(
    kind: str,
    dur_sec: float,
    *,
    i: int | None = None,
    attempt: int | None = None,
    n: int | None = None,
    retries: int | None = None,
) -> None:
    row = {
        "kind": kind,
        "dur_sec": round(float(dur_sec), 3),
        "i": i,
        "attempt": attempt,
        "n": n,
        "retries": retries,
        "where": _where.get() or "",
        "at": _now(),
    }
    if _state is not None:
        _state["llm"].append(row)
    loc = ""
    if row.get("where"):
        loc += f" [{row['where']}]"
    if i is not None:
        loc += f" i={i}"
    if attempt is not None:
        loc += f" attempt={attempt}"
    if n is not None:
        loc += f" n={n}"
    if retries:
        loc += f" retries={retries}"
    log.info("TIMING LLM %s%s dur=%.2fs", kind, loc, dur_sec)


def add_tts(i: int, dur_sec: float, *, n_calls: int = 1) -> None:
    row = {
        "i": i,
        "dur_sec": round(float(dur_sec), 3),
        "n_calls": int(n_calls),
        "where": _where.get() or "",
        "at": _now(),
    }
    if _state is not None:
        _state["tts"].append(row)
    log.info("TIMING TTS i=%d n_calls=%d dur=%.2fs", i, n_calls, dur_sec)


def record_step(
    name: str,
    dur_sec: float,
    *,
    start: str | None = None,
    end: str | None = None,
    extra: str = "",
) -> None:
    """Ghi 1 buoc da chay song song (khong dung contextmanager — tranh race _state)."""
    ts1 = end or _now()
    ts0 = start or ts1
    row = {
        "name": name,
        "start": ts0,
        "end": ts1,
        "dur_sec": round(float(dur_sec), 3),
        "error": None,
        "parallel": True,
    }
    if _state is not None:
        _state["steps"].append(row)
    log.info(
        "TIMING END %s at %s dur=%.2fs (parallel)%s",
        name, ts1, dur_sec, f" {extra}" if extra else "",
    )


def add_segment(i: int, dur_sec: float, **fields: Any) -> None:
    row = {"i": i, "dur_sec": round(float(dur_sec), 3), **fields}
    if _state is not None:
        _state["segments"].append(row)
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    log.info("TIMING SEG i=%d dur=%.2fs %s", i, dur_sec, extra)


@contextmanager
def step(name: str):
    t0 = time.perf_counter()
    ts0 = _now()
    log.info("TIMING START %s at %s", name, ts0)
    err = None
    try:
        yield
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
        raise
    finally:
        dur = time.perf_counter() - t0
        ts1 = _now()
        row = {
            "name": name,
            "start": ts0,
            "end": ts1,
            "dur_sec": round(dur, 3),
            "error": err,
        }
        if _state is not None:
            _state["steps"].append(row)
        log.info(
            "TIMING END %s at %s dur=%.2fs%s",
            name, ts1, dur, f" error={err}" if err else "",
        )


def _sum(rows: list[dict], key: str = "dur_sec") -> float:
    return round(sum(float(r.get(key) or 0) for r in rows), 3)


def _table_text(state: dict) -> str:
    lines = [
        f"job        : {state.get('job_id')}",
        f"video_dur  : {state.get('video_dur_sec')}",
        f"started_at : {state.get('started_at')}",
        f"ended_at   : {state.get('ended_at')}",
        f"wall_sec   : {state.get('wall_sec')}",
        "",
        "==== BUOC LON ====",
        "",
    ]
    for s in state.get("steps") or []:
        lines.append(
            f"{s['name']:<22}  {s['start']} -> {s['end']}  {s['dur_sec']:.2f}s"
            + (f"  ERR {s['error']}" if s.get("error") else "")
        )
    lines += ["", "==== LLM ====", ""]
    llm = state.get("llm") or []
    if not llm:
        lines.append("(khong co)")
    else:
        by: dict[str, float] = {}
        by_w: dict[str, float] = {}
        for r in llm:
            wk = f"{r.get('where') or '-'}|{r.get('kind') or '?'}"
            by[str(r.get("kind") or "?")] = by.get(str(r.get("kind") or "?"), 0) + float(r["dur_sec"])
            by_w[wk] = by_w.get(wk, 0) + float(r["dur_sec"])
        for k, v in by.items():
            n = sum(1 for r in llm if r.get("kind") == k)
            lines.append(f"{k:<22}  {n} lan  tong {v:.2f}s")
        if by_w:
            lines.append("")
            for wk, v in by_w.items():
                n = sum(
                    1 for r in llm
                    if f"{r.get('where') or '-'}|{r.get('kind') or '?'}" == wk
                )
                lines.append(f"  {wk:<28}  {n} lan  tong {v:.2f}s")
        lines.append("")
        for r in llm:
            loc = ""
            if r.get("where"):
                loc += f" [{r['where']}]"
            if r.get("i") is not None:
                loc += f" i={r['i']}"
            if r.get("attempt") is not None:
                loc += f" attempt={r['attempt']}"
            if r.get("n") is not None:
                loc += f" n={r['n']}"
            if r.get("retries"):
                loc += f" retries={r['retries']}"
            lines.append(f"  {r['kind']}{loc}  {r['dur_sec']:.2f}s")
    lines += ["", "==== NOI BO (sleep / prefetch / parse / fallback / ffmpeg) ====", ""]
    buckets = state.get("buckets") or []
    if not buckets:
        lines.append("(khong co — can chay lai job de do inner)")
    else:
        agg: dict[tuple[str, str, str], dict[str, float]] = {}
        for r in buckets:
            key = (
                str(r.get("where") or "-"),
                str(r.get("name") or "?"),
                str(r.get("reason") or ""),
            )
            g = agg.setdefault(key, {"n": 0.0, "dur": 0.0})
            g["n"] += 1
            g["dur"] += float(r.get("dur_sec") or 0)
        for (wh, name, reason), g in sorted(agg.items(), key=lambda x: -x[1]["dur"]):
            tag = f"{wh}/{name}" + (f" ({reason})" if reason else "")
            lines.append(f"{tag:<48}  {int(g['n']):>4} lan  tong {g['dur']:.2f}s")
        lines.append("")
        lines.append(f"tong inner buckets: {_sum(buckets):.2f}s")
    lines += ["", "==== TTS / doan ====", ""]
    tts = state.get("tts") or []
    if not tts:
        lines.append("(khong co)")
    else:
        lines.append(
            f"tong TTS wall (cong i): {_sum(tts):.2f}s  "
            f"n_calls={sum(int(r.get('n_calls') or 1) for r in tts)}"
        )
        for r in tts:
            lines.append(
                f"  i={r['i']}  n_calls={r.get('n_calls')}  {r['dur_sec']:.2f}s"
            )
    lines += ["", "==== NOTE ====", ""]
    for n in state.get("notes") or []:
        extra = " ".join(
            f"{k}={v}" for k, v in n.items() if k not in ("at", "msg")
        )
        lines.append(f"{n.get('at')}  {n.get('msg')}  {extra}".rstrip())
    return "\n".join(lines).rstrip() + "\n"


def finish(*, error: str | None = None) -> Path | None:
    global _state
    if _state is None:
        return None
    _state["ended_at"] = _now()
    _state["wall_sec"] = round(time.perf_counter() - float(_state["t0"]), 3)
    if error:
        _state["error"] = error
    work = Path(_state["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    text = _table_text(_state)
    json_path = work / "timing.json"
    txt_path = work / "timing.txt"
    try:
        json_path.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("Khong ghi timing.json")
        _state = None
        return None
    log.info("TIMING JOB END wall=%.2fs -> %s", _state["wall_sec"], txt_path)
    for line in text.splitlines():
        log.info("TIMING TABLE %s", line)
    out = txt_path
    _state = None
    return out

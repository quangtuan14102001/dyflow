"""Dung lai bien tap noi dung (gan_dung/sai) — chi hoi o day."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .utils import log


def _ask(prompt: str, *, default: str = "") -> str:
    tip = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{tip}: ").strip()
    except EOFError:
        return default
    return val or default


def editorial_gate(
    work: Path | str,
    *,
    interactive: bool = True,
    auto_accept: bool = False,
) -> list[dict]:
    """Doc content_review_full.json; neu gan_dung/sai thi hoi (hoac accept)."""
    work = Path(work)
    path = work / "content_review_full.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    reviews = data.get("reviews") if isinstance(data, dict) else data
    pending = [r for r in (reviews or []) if r.get("score") in ("gan_dung", "sai")]
    if not pending:
        return []

    log.warning("Editorial: %d doan gan_dung/sai can quyet dinh", len(pending))
    print("\n=== NOIDUNG CAN RA TAY (gan_dung / sai) ===")
    for r in pending:
        print(
            f"#{r.get('i')} [{r.get('score')}] "
            f"{(r.get('source_text') or '')[:70]}\n"
            f"  -> {(r.get('translated_text') or '')[:90]}\n"
            f"  ly do: {r.get('reason') or ''}\n"
            f"  goi y: {r.get('suggested_fix') or '(khong)'}\n"
        )

    if auto_accept or not interactive:
        print(f"(auto-accept) giu {len(pending)} cau, tiep tuc TTS/compose.")
        return pending

    if not sys.stdin.isatty():
        print("(stdin khong TTY) giu ban dich, tiep tuc.")
        return pending

    seg_path = work / "segments_vi_goc.json"
    if not seg_path.exists():
        seg_path = work / "segments_vi.json"
    segments = json.loads(seg_path.read_text(encoding="utf-8")) if seg_path.exists() else []

    for r in pending:
        i = int(r["i"])
        suggested = (r.get("suggested_fix") or "").strip()
        print(f"--- Doan #{i} ({r.get('score')}) ---")
        choice = _ask(
            "a=accept | s=suggested_fix | e=nhap tay | q=dung",
            default="a",
        ).lower()
        if choice == "q":
            raise SystemExit("Dung theo yeu cau bien tap.")
        if choice == "s" and suggested and 0 <= i < len(segments):
            segments[i]["text"] = suggested
            print(f"  da dung suggested: {suggested[:80]}")
        elif choice == "e" and 0 <= i < len(segments):
            new = _ask("Nhap cau moi")
            if new:
                segments[i]["text"] = new
        else:
            print("  giu ban dich hien tai")

    if segments:
        payload = json.dumps(segments, ensure_ascii=False, indent=2)
        seg_path.write_text(payload, encoding="utf-8")
        (work / "segments_vi.json").write_text(payload, encoding="utf-8")
        text = " ".join(s.get("text") or "" for s in segments)
        (work / "script_auto_vi.txt").write_text(text, encoding="utf-8")
    return pending


def reload_segments_after_edit(work: Path | str) -> tuple[str, list[dict]] | None:
    work = Path(work)
    path = work / "segments_vi_goc.json"
    if not path.exists():
        path = work / "segments_vi.json"
    if not path.exists():
        return None
    segments = json.loads(path.read_text(encoding="utf-8"))
    text = " ".join(s.get("text") or "" for s in segments)
    return text, segments

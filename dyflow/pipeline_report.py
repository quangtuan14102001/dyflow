"""Tong hop 1 file pipeline_report.json sau moi job."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import log


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def build_pipeline_report(
    work_dir: Path | str,
    *,
    job_id: str = "",
    profile: str | None = None,
    outputs: list | None = None,
    ambient_ok: bool | None = None,
    mix_test_path: str | None = None,
    editorial_pending: list[dict] | None = None,
) -> dict:
    work = Path(work_dir)
    qa = _load_json(work / "segments_qa.json") or []
    final = _load_json(work / "dub_final_report.json") or {}
    content = _load_json(work / "content_review_full.json") or {}
    energy = _load_json(work / "energy_check.json") or {}
    shorten = _load_json(work / "shorten_flags.json") or {}
    ambient_meta = _load_json(work / "audio_fx" / "ambient_meta.json") or {}

    reviews = content.get("reviews") if isinstance(content, dict) else None
    if reviews is None and isinstance(content, list):
        reviews = content
    reviews = reviews or []

    editorial = editorial_pending
    if editorial is None:
        editorial = [r for r in reviews if r.get("score") in ("gan_dung", "sai")]

    atempo_n = sum(1 for r in qa if abs(float(r.get("atempo") or 1) - 1.0) > 0.01)
    qua_dai = sum(1 for r in qa if "qua_dai" in (r.get("flags") or []))

    if ambient_ok is None:
        bed = work / "audio_fx" / "ambient_no_vocals.wav"
        ambient_ok = bed.exists() and bed.stat().st_size > 1000
        if ambient_meta.get("fallback") or ambient_meta.get("failed"):
            ambient_ok = False

    report = {
        "job_id": job_id or work.name,
        "profile": profile,
        "work_dir": str(work),
        "outputs": [str(p) for p in (outputs or [])],
        "mix_test": mix_test_path,
        "coverage": (final.get("summary") or {}).get("coverage")
        if isinstance(final, dict)
        else None,
        "cum_offset_ms": (final.get("summary") or {}).get("cum_offset_ms")
        if isinstance(final, dict)
        else None,
        "qa": {
            "segments": len(qa),
            "atempo_count": atempo_n,
            "qua_dai_count": qua_dai,
            "shorten_count": (
                shorten.get("count")
                if isinstance(shorten, dict)
                else len(shorten) if isinstance(shorten, list) else 0
            ),
            "final_summary": final.get("summary") if isinstance(final, dict) else final,
        },
        "content_review": {
            "khop": sum(1 for r in reviews if r.get("score") == "khop"),
            "gan_dung": sum(1 for r in reviews if r.get("score") == "gan_dung"),
            "sai": sum(1 for r in reviews if r.get("score") == "sai"),
            "editorial_needed": editorial,
        },
        "energy": {
            "flat_count": energy.get("flat_count") if isinstance(energy, dict) else None,
            "flat_threshold": energy.get("flat_threshold") if isinstance(energy, dict) else None,
        },
        "ambient": {
            "ok": bool(ambient_ok),
            "warning": None
            if ambient_ok
            else "Ambient Demucs fail/fallback — mix co the mat BGM (speech_gate)",
            "meta": ambient_meta or None,
        },
        "warnings": [],
    }
    if not ambient_ok:
        report["warnings"].append(report["ambient"]["warning"])
    if editorial:
        report["warnings"].append(
            f"{len(editorial)} doan can ra tay noi dung (gan_dung/sai)"
        )
    flat = report["energy"]["flat_count"]
    if isinstance(flat, int) and flat > 0:
        report["warnings"].append(f"{flat} doan energy phang (score thap)")

    return report


def write_pipeline_report(work_dir: Path | str, report: dict | None = None, **kwargs) -> Path:
    work = Path(work_dir)
    data = report or build_pipeline_report(work, **kwargs)
    out = work / "pipeline_report.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Ban copy o output/ neu co outputs
    for p in data.get("outputs") or []:
        try:
            op = Path(p)
            if op.parent.exists():
                (op.parent / f"{data.get('job_id') or work.name}_pipeline_report.json").write_text(
                    out.read_text(encoding="utf-8"), encoding="utf-8",
                )
                break
        except Exception:  # noqa: BLE001
            pass
    log.info(
        "pipeline_report: editorial=%d warn=%d -> %s",
        len((data.get("content_review") or {}).get("editorial_needed") or []),
        len(data.get("warnings") or []),
        out.name,
    )
    return out

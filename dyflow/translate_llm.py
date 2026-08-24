"""Chinh sua ban dich cho long tieng (OpenAI, tuy chon).

`polish` (mac dinh): chi sua doan co chu Han / draft loi ro — KHONG viet lai ca clip.
`openai`: viet lai tat ca (can prompt tot, khong cat cau).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .dub_constants import DUB_SHORTEN_CHAR_MARGIN, TARGET_CHARS_PER_SEC
from .utils import log

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_INCOMPLETE = re.compile(r"(?:^|\s)(?:cho việc|sau khi mang|có thể|sáng|gel chăm)\.?\s*$", re.I)
_BAD_VI = re.compile(r"rửa mặt|胳膊|刚周", re.I)
_LANG = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "pt": "Brazilian Portuguese",
    "hi": "Hindi",
    "de": "German",
    "id": "Indonesian",
    "tl": "Filipino",
}


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _load_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:  # noqa: BLE001
        pass
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _looks_broken(text: str, draft: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 4:
        return True
    if _has_cjk(t) or _BAD_VI.search(t):
        return True
    if _INCOMPLETE.search(t):
        return True
    # Cat giua cau: khong ket thuc cau nhung draft dai hon nhieu
    if not re.search(r"[.!?…]$", t) and len(draft) > len(t) + 12:
        return True
    return False


def _parse_lines(raw: str, n: int) -> list[str] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    lines = data.get("lines") if isinstance(data, dict) else data
    if not isinstance(lines, list) or len(lines) != n:
        return None
    out: list[str] = []
    for item in lines:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            out.append(str(item.get("text") or item.get("vi") or "").strip())
        else:
            return None
        if not out[-1]:
            return None
    return out


def _rewrite_batch(
    segments: list[dict],
    *,
    target: str,
    mode: str,
) -> list[dict]:
    key = _load_key()
    if not key:
        log.warning("Thieu OPENAI_API_KEY — bo qua LLM dich")
        return segments
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("Chua cai openai — bo qua LLM dich")
        return segments

    lang = _LANG.get((target or "vi").lower(), target or "Vietnamese")
    payload = []
    for i, seg in enumerate(segments, 1):
        payload.append({
            "i": i,
            "slot_sec": round(max(0.0, float(seg.get("end", 0)) - float(seg.get("start", 0))), 2),
            "source": str(seg.get("source_text") or ""),
            "draft": str(seg.get("text") or ""),
        })

    if mode == "polish":
        system = (
            f"Fix ONLY broken machine translations into natural spoken {lang}.\n"
            "Input: JSON array with source (Chinese) and draft (Vietnamese).\n"
            "Rules:\n"
            f"- {lang} only. Complete sentences. Never truncate mid-sentence.\n"
            "- Fix wrong words (e.g. 清潔/厕所 = wash after toilet, NOT 'rửa mặt').\n"
            "- 痔疮/肉球 = hemorrhoids. 葵花凝胶 = Kuihua/Sunflower care gel.\n"
            "- If draft is already good, return it unchanged.\n"
            "- Return JSON: {\"lines\":[{\"i\":1,\"text\":\"...\"}, ...]} same count."
        )
    else:
        system = (
            f"Rewrite dubbing lines into spoken {lang} for short-form video ads.\n"
            "Rules:\n"
            f"- {lang} only. Complete sentences — never cut off mid-thought.\n"
            "- Match Chinese `source` meaning. Fix errors in `draft`.\n"
            "- Natural spoken Vietnamese, not literal word-for-word.\n"
            "- Return JSON: {\"lines\":[{\"i\":1,\"text\":\"...\"}, ...]} same count."
        )

    client = OpenAI(api_key=key)
    try:
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0.15,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM dich loi: %s", e)
        return segments

    lines = _parse_lines(raw, len(segments))
    if not lines:
        log.warning("LLM dich: khong parse duoc JSON — giu ban Google")
        return segments

    out = []
    fixed = 0
    kept = 0
    for seg, text, draft in zip(segments, lines, [s.get("text", "") for s in segments]):
        text = re.sub(r"\s+", " ", text).strip()
        if _has_cjk(text):
            text = _CJK_RE.sub("", text).strip()
        if _looks_broken(text, draft):
            text = draft
            kept += 1
        else:
            fixed += 1
        out.append({**seg, "text": text or draft})
    log.info("LLM dich (%s): %d sua, %d giu draft", mode, fixed, kept)
    return out


def maybe_rewrite(segments: list[dict], cfg_auto: dict) -> list[dict]:
    mode = str(cfg_auto.get("translate_llm") or "polish").strip().lower()
    if mode in ("off", "0", "false", "none", "google"):
        return segments

    target = str(cfg_auto.get("translate_to") or "vi")

    if mode == "polish":
        need = [
            s for s in segments
            if _has_cjk(s.get("text") or "") or _BAD_VI.search(s.get("text") or "")
        ]
        if not need:
            log.info("LLM polish: draft on — bo qua API")
            return segments

    return _rewrite_batch(segments, target=target, mode=mode if mode != "openai" else "openai")


def segment_slot(segments: list[dict], i: int, video_dur: float) -> float:
    """Thoi luong slot thoai (giong synthesize_segments)."""
    start = float(segments[i]["start"])
    next_start = (
        float(segments[i + 1]["start"]) if i + 1 < len(segments) else float(video_dur)
    )
    return max(0.12, next_start - start - 0.04)


def target_chars_for_slot(slot_sec: float, *, chars_per_sec: float | None = None) -> int:
    """Ngân sách ky tu cho 1 slot (proactive, truoc TTS)."""
    slot = max(0.12, float(slot_sec))
    cps = max(6.0, float(chars_per_sec or TARGET_CHARS_PER_SEC))
    return max(8, int(slot * cps * DUB_SHORTEN_CHAR_MARGIN))


def _max_chars_for_slot(slot_sec: float, chars_per_sec: float) -> int:
    return target_chars_for_slot(slot_sec, chars_per_sec=chars_per_sec)


def shorten_for_slot(
    text: str,
    *,
    slot_sec: float,
    source_text: str = "",
    target: str = "vi",
    chars_per_sec: float | None = None,
) -> str | None:
    """Rut gon 1 cau dich de vua slot thoai (LLM). Tra None neu that bai."""
    key = _load_key()
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not key or not text:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    lang = _LANG.get((target or "vi").lower(), target or "Vietnamese")
    max_chars = target_chars_for_slot(slot_sec, chars_per_sec=chars_per_sec)
    system = (
        f"Shorten a {lang} dubbing line for short-form health/product video.\n"
        f"Hard limit: at most {max_chars} characters (including spaces).\n"
        f"Must fit ~{slot_sec:.1f}s at ~{chars_per_sec or TARGET_CHARS_PER_SEC:.0f} chars/sec.\n"
        "Rules:\n"
        "- Keep the same meaning as source Chinese if provided.\n"
        "- Natural spoken Vietnamese — NOT telegraphic, NOT truncated mid-thought.\n"
        "- 痔疮/肉球 = hemorrhoids. 葵花凝胶 = sunflower care gel. 肛周 = anal area.\n"
        "- Complete sentence ending with . ! or ?\n"
        "- Shorter synonyms OK; remove filler only.\n"
        'Return JSON: {"text":"..."}'
    )
    user = {
        "slot_sec": round(float(slot_sec), 2),
        "max_chars": max_chars,
        "source": source_text,
        "current": text,
    }
    client = OpenAI(api_key=key)
    try:
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
        data = json.loads(raw)
        out = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM rut gon loi: %s", e)
        return None

    if not out or _has_cjk(out):
        return None
    if len(out) >= len(text) - 2:
        return None
    log.info(
        "LLM rut gon: %d->%d chars (slot %.1fs, max %d) | %s",
        len(text), len(out), slot_sec, max_chars, out[:70],
    )
    return out


def translate_segments_budgeted(
    segments: list[dict],
    *,
    video_dur: float,
    target: str = "vi",
    chars_per_sec: float | None = None,
    work_dir: Path | str | None = None,
) -> list[dict]:
    """Dich zh -> vi voi ngan sach ky tu theo slot (proactive, truoc TTS)."""
    if not segments:
        return []

    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    lang = _LANG.get((target or "vi").lower(), target or "Vietnamese")
    payload = []
    budgets: list[dict] = []

    for i, seg in enumerate(segments):
        src = str(seg.get("source_text") or seg.get("text") or "").strip()
        slot = segment_slot(segments, i, video_dur)
        max_chars = target_chars_for_slot(slot, chars_per_sec=cps)
        payload.append({
            "i": i + 1,
            "slot_sec": round(slot, 2),
            "max_chars": max_chars,
            "source": src,
        })
        budgets.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "slot_sec": round(slot, 3),
            "target_chars": max_chars,
            "source_text": src,
        })

    key = _load_key()
    if not key:
        log.warning("Thieu OPENAI_API_KEY — fallback Google + shorten theo budget")
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    try:
        from openai import OpenAI
    except ImportError:
        log.warning("Chua cai openai — fallback Google + shorten theo budget")
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    system = (
        f"Translate Chinese dubbing lines into spoken {lang} for short health/product ads.\n"
        "Input: JSON array; each item has source (Chinese), slot_sec, max_chars.\n"
        "Rules:\n"
        f"- {lang} only. Each line MUST be at most max_chars characters (spaces count).\n"
        "- Fit slot_sec at natural pace — shorter is OK, NEVER exceed max_chars.\n"
        "- Complete sentences. Natural spoken style, not word-for-word.\n"
        "- 痔疮/肉球/瘡肉球 = hemorrhoid tissue. 葵花凝胶 = sunflower care gel.\n"
        "- 肛周 = anal/perianal area (NOT pregnancy). 刚周 in source likely means 肛周.\n"
        "- 清潔/厕所 = clean after toilet, NOT wash face.\n"
        "- Return JSON: {\"lines\":[{\"i\":1,\"text\":\"...\"}, ...]} same count as input."
    )

    client = OpenAI(api_key=key)
    try:
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0.15,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM dich budget loi: %s — fallback Google", e)
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    lines = _parse_lines(raw, len(segments))
    if not lines:
        log.warning("LLM dich budget: khong parse duoc JSON — fallback Google")
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    out: list[dict] = []
    for seg, text, bud in zip(segments, lines, budgets):
        text = re.sub(r"\s+", " ", text).strip()
        if _has_cjk(text):
            text = _CJK_RE.sub("", text).strip()
        src = str(seg.get("source_text") or seg.get("text") or "")
        if len(text) > bud["target_chars"] + 2:
            shorter = shorten_for_slot(
                text,
                slot_sec=bud["slot_sec"],
                source_text=src,
                target=target,
                chars_per_sec=cps,
            )
            if shorter:
                text = shorter
        out.append({
            **seg,
            "text": text or seg.get("text", ""),
            "source_text": src,
            "target_chars": bud["target_chars"],
            "slot_sec": bud["slot_sec"],
        })
        log.info(
            "Dich budget #%d: slot %.1fs max %d -> %d chars | %s",
            bud["i"], bud["slot_sec"], bud["target_chars"], len(text), text[:60],
        )

    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "translate_budget.json").write_text(
            json.dumps({"chars_per_sec": cps, "segments": budgets}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    log.info("Dich budget xong: %d doan (TARGET_CHARS_PER_SEC=%.1f)", len(out), cps)
    return out


def _translate_budgeted_fallback(
    segments: list[dict],
    *,
    video_dur: float,
    target: str,
    cps: float,
) -> list[dict]:
    from .transcribe import translate_segments

    zh = [{**s, "text": s.get("source_text") or s.get("text", "")} for s in segments]
    vi = translate_segments(zh, target=target, source="zh-CN", work_dir=None)
    out: list[dict] = []
    for i, seg in enumerate(vi):
        slot = segment_slot(vi, i, video_dur)
        text = str(seg.get("text") or "")
        src = str(seg.get("source_text") or "")
        max_chars = target_chars_for_slot(slot, chars_per_sec=cps)
        shorter = shorten_for_slot(
            text, slot_sec=slot, source_text=src, target=target, chars_per_sec=cps,
        )
        if shorter:
            text = shorter
        out.append({
            **seg,
            "text": text,
            "source_text": src,
            "target_chars": max_chars,
            "slot_sec": round(slot, 3),
        })
    return out


def retranslate_segment_faithful(
    seg: dict,
    *,
    segment_index: int,
    segments: list[dict],
    video_dur: float,
    target: str = "vi",
    chars_per_sec: float | None = None,
    emphasis: str = "",
) -> str | None:
    """Dich lai 1 doan — uu tien dung nghia y te, van trong budget ky tu."""
    key = _load_key()
    src = str(seg.get("source_text") or seg.get("text") or "").strip()
    if not key or not src:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    slot = float(seg.get("slot_sec") or segment_slot(segments, segment_index, video_dur))
    max_chars = int(seg.get("target_chars") or target_chars_for_slot(slot, chars_per_sec=chars_per_sec))
    lang = _LANG.get((target or "vi").lower(), target or "Vietnamese")
    system = (
        f"Translate ONE Chinese dubbing line into spoken {lang} for a hemorrhoid care ad.\n"
        "CRITICAL: preserve the medical meaning of the Chinese source — do NOT invent unrelated topics.\n"
        f"Hard limit: at most {max_chars} characters (spaces count). Slot ~{slot:.1f}s.\n"
        "Terminology:\n"
        "- 吃瘡肉球/肉球/瘡 = prolapsed hemorrhoid tissue, NOT spicy food or acne.\n"
        "- 塞不回去 = cannot push back in / won't stay inside.\n"
        "- 反复 = keeps happening repeatedly.\n"
        "- 清潔/厕所 = after toilet, NOT face wash.\n"
        "- 肛周/刚周 = perianal area.\n"
        "- Natural spoken Vietnamese. Complete sentence.\n"
        f"{emphasis}\n"
        'Return JSON: {"text":"..."}'
    )
    user = {"source": src, "max_chars": max_chars, "slot_sec": round(slot, 2)}
    client = OpenAI(api_key=key)
    try:
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        data = json.loads((r.choices[0].message.content or "").strip())
        out = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("Dich lai doan %d loi: %s", segment_index, e)
        return None
    if not out or _has_cjk(out):
        return None
    log.info("Dich lai #%d: %d chars (max %d) | %s", segment_index, len(out), max_chars, out[:80])
    return out


def review_segments_content(segments: list[dict]) -> list[dict]:
    """LLM reviewer: so sanh zh goc vs vi, cham khop/gan_dung/sai."""
    key = _load_key()
    if not key:
        log.warning("Thieu OPENAI_API_KEY — khong review noi dung")
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    payload = []
    for i, seg in enumerate(segments):
        payload.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 2),
            "end": round(float(seg.get("end", 0)), 2),
            "source_zh": str(seg.get("source_text") or ""),
            "translation_vi": str(seg.get("text") or ""),
        })

    review_model = os.getenv("OPENAI_REVIEW_MODEL") or os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini"
    system = (
        "You are a bilingual medical dubbing QA reviewer (Chinese source -> Vietnamese dub).\n"
        "Video topic: hemorrhoid symptoms and care gel product.\n"
        "For EACH segment, compare source_zh and translation_vi.\n"
        "Score meaning match:\n"
        "- khop: same core meaning, acceptable for dubbing\n"
        "- gan_dung: mostly OK but missing detail, wrong nuance, or vague wording\n"
        "- sai: wrong topic, invented content, or contradicts source\n"
        "Context: 肉球/瘡=hemorrhoid tissue; 塞不回去=won't retract; 刚周/肛周=anal area; "
        "NOT pregnancy, NOT spicy food unless source says so.\n"
        "Return JSON: {\"reviews\":[{\"i\":0,\"score\":\"khop|gan_dung|sai\","
        "\"reason\":\"...\",\"suggested_fix\":\"\"}, ...]} — same count as input."
    )

    client = OpenAI(api_key=key)
    try:
        r = client.chat.completions.create(
            model=review_model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw = json.loads((r.choices[0].message.content or "").strip())
    except Exception as e:  # noqa: BLE001
        log.warning("Content review loi: %s", e)
        return []

    items = raw.get("reviews") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or len(items) != len(segments):
        log.warning("Content review: JSON khong hop le (%s items)", len(items or []))
        return []

    out: list[dict] = []
    for i, (seg, item) in enumerate(zip(segments, items)):
        score = str(item.get("score") or "").strip().lower()
        if score not in ("khop", "gan_dung", "sai"):
            score = "gan_dung"
        out.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "score": score,
            "reason": str(item.get("reason") or "").strip(),
            "suggested_fix": str(item.get("suggested_fix") or "").strip(),
            "source_text": str(seg.get("source_text") or ""),
            "translated_text": str(seg.get("text") or ""),
        })
    log.info(
        "Content review: khop=%d gan_dung=%d sai=%d",
        sum(1 for x in out if x["score"] == "khop"),
        sum(1 for x in out if x["score"] == "gan_dung"),
        sum(1 for x in out if x["score"] == "sai"),
    )
    return out

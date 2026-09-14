"""Chinh sua ban dich cho long tieng (OpenAI, tuy chon).

`polish` (mac dinh): chi sua doan co chu Han / draft loi ro — KHONG viet lai ca clip.
`openai`: viet lai tat ca (can prompt tot, khong cat cau).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .dub_constants import DUB_SHORTEN_CHAR_MARGIN, TARGET_CHARS_PER_SEC
from .utils import log

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_INCOMPLETE = re.compile(r"(?:^|\s)(?:cho việc|sau khi mang|có thể|sáng|gel chăm)\.?\s*$", re.I)
_BAD_VI = re.compile(r"rửa mặt|胳膊|刚周", re.I)
_OPENAI_RETRY_LOGGED = False
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
_VI_CHARS = re.compile(r"[ăâêôơưđĂÂÊÔƠƯĐ]")
_VI_WORDS = re.compile(
    r"(?i)(?:\bkhông\b|\bđược\b|\bcũng\b|\bcủa\b|\bviết\b|\bkịch\b|"
    r"\bcâu chuyện\b|\bngười\b|\bthể\b|\bbiết\b)"
)
_HIRA = re.compile(r"[\u3040-\u309f]")
_KATA = re.compile(r"[\u30a0-\u30ff]")
_DEVAN = re.compile(r"[\u0900-\u097f]")
_LATIN = re.compile(r"[A-Za-z]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def normalize_target_lang(code: str | None) -> str:
    c = (code or "vi").strip().lower().replace("_", "-")
    if c in _LANG:
        return c
    prefix = c.split("-")[0]
    if prefix in _LANG:
        return prefix
    return "vi"


def dub_target_lang(cfg: dict | None) -> str:
    """Doc translate_to tu cfg hoac cfg.auto — khong default ngam vi khi job lang=en."""
    cfg = cfg or {}
    auto = cfg.get("auto") if isinstance(cfg.get("auto"), dict) else {}
    raw = (
        cfg.get("translate_to")
        or auto.get("translate_to")
        or cfg.get("lang")
        or auto.get("lang")
        or "vi"
    )
    return normalize_target_lang(str(raw))


def lang_mismatch_reason(text: str, target: str) -> str | None:
    """None neu text khop target. Heuristic — khong can langdetect."""
    t = (text or "").strip()
    tgt = normalize_target_lang(target)
    if not t:
        return "empty"
    if tgt != "vi" and (_VI_CHARS.search(t) or _VI_WORDS.search(t)):
        return f"expected {tgt}, got Vietnamese"
    if tgt == "en":
        if _has_cjk(t) or _HIRA.search(t) or _KATA.search(t) or _DEVAN.search(t):
            return "expected en, got non-Latin script"
        if not _LATIN.search(t):
            return "expected en, no Latin letters"
        return None
    if tgt == "ja":
        if _VI_CHARS.search(t):
            return "expected ja, got Vietnamese"
        if not (_has_cjk(t) or _HIRA.search(t) or _KATA.search(t)):
            return "expected ja, missing Japanese script"
        return None
    if tgt == "hi":
        if _VI_CHARS.search(t):
            return "expected hi, got Vietnamese"
        if not _DEVAN.search(t):
            return "expected hi, missing Devanagari"
        return None
    if tgt == "vi":
        if _has_cjk(t) or _HIRA.search(t) or _KATA.search(t) or _DEVAN.search(t):
            return "expected vi, got other script"
        return None
    if _has_cjk(t) or _HIRA.search(t) or _KATA.search(t) or _DEVAN.search(t):
        return f"expected {tgt}, got other script"
    return None


def _finalize_shortened(
    out: str,
    original: str,
    target: str,
    *,
    label: str,
) -> str | None:
    """Chap nhan ban rut: dung ngon ngu + ngan hon. Log ly do reject ro."""
    if not (out or "").strip():
        log.warning("%s: parse rong — reject (khong phai JSON None)", label)
        return None
    why = lang_mismatch_reason(out, target)
    if why:
        log.warning("%s: SAI NGON NGU (%s) — reject | %s", label, why, out[:80])
        return None
    if _has_cjk(out):
        log.warning("%s: con chu Han — reject | %s", label, out[:80])
        return None
    if out == original or len(out) >= len(original):
        log.warning(
            "%s: khong rut ngan (%d->%d) — reject (khong phai JSON None) | %s",
            label, len(original), len(out), out[:80],
        )
        return None
    return out


def _load_key() -> str:
    from .preflight import hydrate_openai_key
    return hydrate_openai_key()


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
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            data = json.loads(text[a : b + 1])
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


def _parse_line_rows(raw: str, n: int) -> tuple[list[dict], list[dict]] | None:
    """Tra (rows, cast). rows[i].text bat buoc."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            data = json.loads(text[a : b + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    lines = data.get("lines")
    if not isinstance(lines, list) or len(lines) != n:
        return None
    rows: list[dict] = []
    for item in lines:
        if isinstance(item, str):
            t = item.strip()
            extra = {}
        elif isinstance(item, dict):
            t = str(item.get("text") or item.get("vi") or item.get("translation") or "").strip()
            extra = {
                "speaker_relation": str(item.get("speaker_relation") or "").strip(),
                "pronoun_pair_used": str(item.get("pronoun_pair_used") or "").strip(),
            }
        else:
            return None
        if not t:
            return None
        rows.append({"text": t, **extra})
    cast_raw = data.get("cast")
    cast: list[dict] = []
    if isinstance(cast_raw, list):
        for c in cast_raw:
            if not isinstance(c, dict):
                continue
            cast.append({
                "speaker_id": str(c.get("speaker_id") or "").strip(),
                "speaker_relation": str(c.get("speaker_relation") or "").strip(),
                "pronoun_pair_used": str(c.get("pronoun_pair_used") or "").strip(),
            })
    return rows, cast


def _rewrite_batch(
    segments: list[dict],
    *,
    target: str,
    mode: str,
) -> list[dict]:
    from .preflight import allow_degraded, alert_ops, require_openai

    key = require_openai(kind="draft_mode")
    if not key:
        return segments
    try:
        from openai import OpenAI
    except ImportError:
        msg = "Chua cai openai — khong polish duoc ban dich"
        if allow_degraded():
            alert_ops("draft_mode", msg)
            log.warning(msg)
            return segments
        alert_ops("preflight_fail", msg)
        raise RuntimeError(msg) from None

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
        from .preflight import alert_ops
        alert_ops("llm_translate_fail", str(e))
        leftover = [s for s in segments if _has_cjk(s.get("text") or "")]
        if leftover:
            msg = (
                f"LLM dich loi va con {len(leftover)} doan chu Trung/Han — khong giao clip sot ngon ngu goc."
            )
            alert_ops("translate_fail", msg)
            raise RuntimeError(msg) from e
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
    leftover = [s for s in out if _has_cjk(s.get("text") or "")]
    if leftover:
        from .preflight import alert_ops
        msg = (
            f"LLM polish xong van con {len(leftover)} doan chu Trung/Han — khong giao clip sot ngon ngu goc."
        )
        alert_ops("translate_fail", msg)
        raise RuntimeError(msg)
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
            log.info("LLM polish: khong co chu Han/loi draft — bo qua API")
            return segments

    return _rewrite_batch(segments, target=target, mode=mode if mode != "openai" else "openai")


def segment_slot(segments: list[dict], i: int, video_dur: float) -> float:
    """Ngan sach noi: cua thoai + spill nhe vao pause, khong gom doan nhac dai."""
    from .dub_timing import budget_slot

    return budget_slot(segments, i, video_dur)


def target_chars_for_slot(slot_sec: float, *, chars_per_sec: float | None = None) -> int:
    """Ngân sách ky tu cho 1 slot (proactive, truoc TTS)."""
    slot = max(0.12, float(slot_sec))
    cps = max(6.0, float(chars_per_sec or TARGET_CHARS_PER_SEC))
    return max(32, int(slot * cps * DUB_SHORTEN_CHAR_MARGIN))


def _max_chars_for_slot(slot_sec: float, chars_per_sec: float) -> int:
    return target_chars_for_slot(slot_sec, chars_per_sec=chars_per_sec)


def hard_shorten_text(text: str, max_chars: int) -> str:
    """Cat cau ve max_chars. Khong cat duoi DUB_MIN_SHORTEN_CHARS (tranh manh vo nghia)."""
    from .dub_constants import DUB_MIN_SHORTEN_CHARS

    text = re.sub(r"\s+", " ", (text or "")).strip()
    limit = max(int(DUB_MIN_SHORTEN_CHARS), int(max_chars))
    if not text or len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    end_at = -1
    for i, ch in enumerate(cut):
        if ch in ".!?…":
            end_at = i
    if end_at >= max(16, limit // 3):
        out = cut[: end_at + 1].strip()
    else:
        sp = cut.rfind(" ")
        out = cut[:sp].strip() if sp >= 16 else cut
    if out and out[-1] not in ".!?…":
        out = out.rstrip(",;: ") + "."
    if len(out) < DUB_MIN_SHORTEN_CHARS and len(text) > len(out):
        return text
    return out or text[:limit]


def _source_lang_name(code: str) -> str:
    return {
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "zh-CN": "Chinese",
        "vi": "Vietnamese",
        "en": "English",
    }.get((code or "zh").strip(), code or "Chinese")


def _pronoun_rules(target_lang: str) -> str:
    # Nhom D: xung ho. Nhom C: trap 还差/才/没/怎么又 nam o _dub_translate_prompt.
    tgt = (target_lang or "vi").lower()
    if tgt != "vi":
        return (
            "BUOC 0 (TRUOC khi dich): xac dinh nguoi noi / nguoi nghe tu context "
            "va tu danh xung trong goc. Dung cap xung ho tu nhien cua target_lang. "
            "KHONG mac dinh I/you neu la nguoi nha. "
            "Neu locked_cast / locked_relation duoc cung cap, BAT BUOC dung dung cap do.\n"
        )
    return (
        "BUOC 0 (TRUOC khi dich): dua vao prev_context, next_context, noi dung cau, "
        "tu danh xung ZH (妈/爸/儿子/女儿/老公/老婆/哥/姐...) xac dinh:\n"
        "- Nguoi noi la ai, dang noi voi ai (me-con, vo chong, anh em...).\n"
        "- Thieu du lieu: suy luan hop ly tu ngu canh gan nhat, "
        "KHONG mac dinh toi/ban trung tinh.\n"
        "Xung ho tieng Viet BAT BUOC theo quan he da xac dinh:\n"
        "- Me noi voi con: xung me, goi con.\n"
        "- Con noi voi me: xung con, goi me.\n"
        "- Cha noi voi con: xung ba/bo, goi con.\n"
        "- Vo chong: anh/em theo gioi, hoac vo/chong.\n"
        "- Anh chi em ruot: anh/chi/em theo tuoi neu xac dinh duoc.\n"
        "- Toi/ban CHI khi nguoi la hoac dong nghiep ngang vai khong than.\n"
        "Nhat quan cap xung ho cho CUNG mot nhan vat xuyen clip — "
        "khong doi me-con o doan 3 roi toi-ban o doan 8.\n"
        "Cung 1 cau ZH o 2 vi tri (cung speaker) PHAI cung 1 cap xung ho.\n"
        "Neu locked_cast / locked_relation duoc cung cap, BAT BUOC dung dung cap do "
        "— KHONG doan lai theo tung doan.\n"
    )


def _dub_translate_prompt(
    *,
    source_lang: str,
    target_lang: str,
    source_text: str,
    prev_context: str,
    next_context: str,
    slot_duration: float,
    chars_per_second: float,
    target_chars: int,
    locked_relation: str = "",
    locked_pronoun: str = "",
    locked_cast: list | None = None,
    speaker_id: str = "",
) -> tuple[str, dict]:
    src_n = _source_lang_name(source_lang)
    tgt_code = normalize_target_lang(target_lang)
    tgt_n = _LANG.get(tgt_code, target_lang or "Vietnamese")
    system = (
        f"Ban la bien dich vien chuyen long tieng (dubbing translator) cho video ngan.\n"
        f"Nhiem vu: dich doan thoai tu {src_n} sang {tgt_n}, sao cho ban dich khi "
        f"doc thanh tieng vua khop khung thoi gian goc — KHONG dich tu nhien roi cat sau.\n"
        f"JSON.translation BAT BUOC bang {tgt_n}. "
        f"KHONG dich sang Vietnamese hay ngon ngu khac neu target la {tgt_n}.\n"
        f"{_pronoun_rules(tgt_code)}"
        "Quy tac dich:\n"
        "1. Van noi Trung khau ngu thuong luoc chu ngu/lien tu — DUNG them day du "
        "chu ngu/lien tu ma ban goc khong co, tru khi thieu se toi nghia. "
        "Uu tien cau ngan, dung van phong noi.\n"
        "2. Cau truc de sai chieu nghia:\n"
        '   - "还差X" = "con THIEU X" (KHONG PHAI "con CO X")\n'
        '   - "没有/没" = phu dinh hoan toan, khong phai "chua" neu khong co y cho doi\n'
        '   - "才" = "moi vua/chi moi" (nhan manh muon/it), de bi dich thanh "chi"\n'
        '   - "怎么又" = "sao lai...nua" (buc/ngac nhien lap lai)\n'
        "   Kiem tra phu dinh/so luong truoc khi chot.\n"
        "3. Neu cau goc hoi-dap hoac nhan-qua voi context truoc/sau, PHAI giu du "
        "thong tin de cau nay van co nghia khi dung canh context — khong luoc toi "
        "muc dung mot minh se lo lung.\n"
        "4. Neu dich tu nhien chac chan vuot target_chars, KHONG dich day du roi "
        "nho buoc sau cat — chon tu ngan hon, bo tinh tu/trang tu khong cot loi, "
        "gop y trung lap NGAY trong ban dich, mien giu toan bo menh de thong tin "
        "chinh (ai, cai gi, so lieu, cau hoi/cau tra loi).\n"
        'Tra JSON: {"translation":"...","estimated_chars":<int>,"fits_target":true/false,'
        '"notes":"...","speaker_relation":"vd: me noi voi con trai",'
        '"pronoun_pair_used":"vd: me/con"}'
    )
    user = {
        "source_lang": src_n,
        "target_lang": tgt_n,
        "target_lang_code": tgt_code,
        "source_text": source_text or "",
        "prev_context": prev_context or "",
        "next_context": next_context or "",
        "slot_duration": round(float(slot_duration), 3),
        "chars_per_second": round(float(chars_per_second), 2),
        "target_chars": int(target_chars),
        "speaker_id": speaker_id or "",
        "locked_relation": locked_relation or "",
        "locked_pronoun": locked_pronoun or "",
        "locked_cast": locked_cast or [],
    }
    return system, user


def _dub_reslot_prompt(
    *,
    source_lang: str,
    target_lang: str,
    source_text: str,
    current_translation: str,
    prev_context: str,
    next_context: str,
    target_chars: int,
    locked_relation: str = "",
    locked_pronoun: str = "",
    locked_cast: list | None = None,
    speaker_id: str = "",
    aggressive: bool = False,
    ineffective_hint: str = "",
) -> tuple[str, dict]:
    src_n = _source_lang_name(source_lang)
    tgt_code = normalize_target_lang(target_lang)
    tgt_n = _LANG.get(tgt_code, target_lang or "Vietnamese")
    current = re.sub(r"\s+", " ", current_translation or "").strip()
    deficit = max(0, len(current) - int(target_chars))
    severe = (
        "LAN CAT MANH (severe overflow / lan rut truoc khong dat):\n"
        "- KHONG chi bo 1 trang tu (vi du 'that su', 'that', 'hoan toan') — TTS se khong giam du.\n"
        "- BAT BUOC cat bot 1 menh de phu (ve phu, ly do, vi du, nhan xet).\n"
        "- Chap nhan mat 1 menh de phu KHONG phai: cau hoi, cau tra loi, so lieu, ten rieng.\n"
        "- Giu dung cap xung ho da khoa.\n"
        if aggressive else ""
    )
    system = (
        f"Ban dich sang {tgt_n} van dai hon khung thoi gian. Rut ngan them ma "
        f"KHONG lam mat mach lac. Goc {src_n} la neo nghia.\n"
        f"JSON.shortened BAT BUOC bang {tgt_n} — giu dung ngon ngu cua current_translation. "
        f"KHONG dich sang Vietnamese hay ngon ngu khac neu target la {tgt_n}.\n"
        f"{severe}"
        "Thu tu uu tien khi cat (lan luot, KHONG nhay coc):\n"
        "1. Bo trang tu/tinh tu bo nghia khong cot loi "
        '(vi du: "that su", "hoan toan", "mot cach...").\n'
        "2. Thay cum dai bang tu dong nghia ngan hon, giu nguyen nghia.\n"
        "3. Gop 2 menh de cung y thanh 1 cau ngan hon (khong xoa menh de, chi nen chu).\n"
        "4. CHI khi 3 buoc tren van khong du: duoc phep bo bot 1 menh de — nhung PHAI:\n"
        "   - Menh de do co phai cau tra loi cho cau hoi trong context? Neu co, KHONG bo "
        "(tha giu, chap nhan vuot nhe).\n"
        "   - Co so lieu/ten rieng/thong tin khong lap lai? Neu co, KHONG bo.\n"
        "   - Sau khi bo, cau con lai doc doc lap con ro nghia? Neu khong, chon menh de khac.\n"
        f"{_pronoun_rules(tgt_code)}"
        'Giu DUNG cap xung ho da khoa (locked_pronoun / locked_relation). '
        "Khi rut gon KHONG doi me/con thanh toi/ban.\n"
        'Tra JSON: {"shortened":"...","chars":<int>,"clauses_removed":[...],'
        '"coherence_risk":"none|low|high — giai thich neu phai bo",'
        '"speaker_relation":"...","pronoun_pair_used":"..."}'
    )
    user = {
        "current_translation": current,
        "source_lang": src_n,
        "target_lang": tgt_n,
        "target_lang_code": tgt_code,
        "source_text": source_text or "",
        "prev_context": prev_context or "",
        "next_context": next_context or "",
        "target_chars": int(target_chars),
        "current_chars": len(current),
        "deficit_chars": deficit,
        "speaker_id": speaker_id or "",
        "locked_relation": locked_relation or "",
        "locked_pronoun": locked_pronoun or "",
        "locked_cast": locked_cast or [],
        "aggressive": bool(aggressive),
        "ineffective_hint": ineffective_hint or "",
    }
    return system, user


def _llm_json(
    system: str,
    user: dict,
    *,
    temperature: float = 0.2,
    kind: str = "json",
    i: int | None = None,
    attempt: int | None = None,
    n: int | None = None,
) -> dict | None:
    key = _load_key()
    if not key:
        log.warning("LLM dub JSON: thieu OPENAI_API_KEY")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("LLM dub JSON: chua cai openai")
        return None
    t_llm = time.perf_counter()
    try:
        client = OpenAI(api_key=key)
        global _OPENAI_RETRY_LOGGED
        if not _OPENAI_RETRY_LOGGED:
            _OPENAI_RETRY_LOGGED = True
            log.info(
                "LLM OpenAI SDK max_retries=%s — retry/backoff nam trong TIMING LLM "
                "(khong co asyncio.sleep rieng o translate_llm)",
                getattr(client, "max_retries", "?"),
            )
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            log.warning("LLM dub JSON: khong phai object (type=%s)", type(data).__name__)
            return None
        return data
    except Exception:  # noqa: BLE001
        log.exception("LLM dub JSON loi")
        return None
    finally:
        from .job_timing import add_llm
        add_llm(kind, time.perf_counter() - t_llm, i=i, attempt=attempt, n=n)


def _pick_line(data: dict | None, *keys: str) -> str:
    if not data:
        return ""
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v).strip()
    return ""


def shorten_for_slot(
    text: str,
    *,
    slot_sec: float,
    source_text: str = "",
    target: str = "vi",
    chars_per_sec: float | None = None,
    max_chars: int | None = None,
    prev_context: str = "",
    next_context: str = "",
    source_lang: str = "zh",
    locked_relation: str = "",
    locked_pronoun: str = "",
    locked_cast: list | None = None,
    speaker_id: str = "",
    i: int | None = None,
) -> str | None:
    """Rut gon 1 cau dich de vua slot — prompt reslot (khong cat ky tu mu)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return None
    target = normalize_target_lang(target)
    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    if max_chars is None:
        max_chars = max(1, int(round(max(0.12, float(slot_sec)) * cps)))
    else:
        max_chars = max(1, int(max_chars))
    system, user = _dub_reslot_prompt(
        source_lang=source_lang,
        target_lang=target,
        source_text=source_text,
        current_translation=text,
        prev_context=prev_context,
        next_context=next_context,
        target_chars=max_chars,
        locked_relation=locked_relation,
        locked_pronoun=locked_pronoun,
        locked_cast=locked_cast,
        speaker_id=speaker_id,
    )
    data = _llm_json(system, user, temperature=0.2, kind="shorten_slot", i=i)
    if data is None:
        log.warning("LLM reslot: JSON None (xem traceback LLM dub JSON o tren)")
        return None
    raw_out = _pick_line(data, "shortened", "translation", "text")
    log.info("LLM reslot nhan: %s", (raw_out or "")[:80])
    out = _finalize_shortened(raw_out, text, target, label="LLM reslot")
    if not out:
        return None
    risk = (data or {}).get("coherence_risk")
    removed = (data or {}).get("clauses_removed")
    log.info(
        "LLM reslot: %d->%d chars (slot %.1fs, max %d, risk=%s removed=%s) | %s",
        len(text), len(out), slot_sec, max_chars, risk, removed, out[:80],
    )
    return out


def shorten_semantic_overflow(
    text: str,
    *,
    source_text: str = "",
    target: str = "vi",
    target_chars: int,
    target_dur: float,
    deficit_sec: float = 0.0,
    attempt: int = 1,
    chars_per_sec: float | None = None,
    prev_context: str = "",
    next_context: str = "",
    source_lang: str = "zh",
    locked_relation: str = "",
    locked_pronoun: str = "",
    locked_cast: list | None = None,
    speaker_id: str = "",
    aggressive: bool = False,
    ineffective_hint: str = "",
    i: int | None = None,
) -> str | None:
    """Lan 1: dich lai vua khung. Lan 2+: reslot. Lan 3/aggressive: cat menh de phu."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    limit = max(1, int(target_chars))
    if not text:
        return None
    target = normalize_target_lang(target)
    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    force_cut = bool(aggressive) or int(attempt) >= 3
    label = f"LLM dub-translate lan {int(attempt)}"
    if int(attempt) <= 1 and (source_text or "").strip() and not force_cut:
        system, user = _dub_translate_prompt(
            source_lang=source_lang,
            target_lang=target,
            source_text=source_text,
            prev_context=prev_context,
            next_context=next_context,
            slot_duration=target_dur,
            chars_per_second=cps,
            target_chars=limit,
            locked_relation=locked_relation,
            locked_pronoun=locked_pronoun,
            locked_cast=locked_cast,
            speaker_id=speaker_id,
        )
        data = _llm_json(
            system, user, temperature=0.2,
            kind="shorten", i=i, attempt=int(attempt),
        )
        if data is None:
            log.warning(
                "LLM dub-translate lan %d: JSON None (xem traceback LLM dub JSON o tren)",
                attempt,
            )
            return None
        raw_out = _pick_line(data, "translation", "shortened", "text")
        notes = (data or {}).get("notes")
        fits = (data or {}).get("fits_target")
        log.info(
            "LLM dub-translate lan 1 nhan: %d chars (budget %d, slot %.2fs, "
            "fits=%s relation=%s pronoun=%s) notes=%s | %s",
            len(raw_out or ""), limit, target_dur, fits,
            (data or {}).get("speaker_relation"),
            (data or {}).get("pronoun_pair_used"),
            notes,
            (raw_out or "")[:80],
        )
        out = _finalize_shortened(raw_out, text, target, label=label)
    else:
        system, user = _dub_reslot_prompt(
            source_lang=source_lang,
            target_lang=target,
            source_text=source_text,
            current_translation=text,
            prev_context=prev_context,
            next_context=next_context,
            target_chars=limit,
            locked_relation=locked_relation,
            locked_pronoun=locked_pronoun,
            locked_cast=locked_cast,
            speaker_id=speaker_id,
            aggressive=force_cut,
            ineffective_hint=ineffective_hint,
        )
        data = _llm_json(
            system, user, temperature=0.15,
            kind="shorten", i=i, attempt=int(attempt),
        )
        if data is None:
            log.warning(
                "LLM reslot lan %d: JSON None (xem traceback LLM dub JSON o tren)",
                attempt,
            )
            return None
        raw_out = _pick_line(data, "shortened", "translation", "text")
        risk = (data or {}).get("coherence_risk")
        removed = (data or {}).get("clauses_removed")
        log.info(
            "LLM reslot lan %d nhan: %d chars (budget %d, deficit %.2fs, "
            "risk=%s removed=%s relation=%s pronoun=%s aggressive=%s) | %s",
            attempt, len(raw_out or ""), limit, deficit_sec, risk, removed,
            (data or {}).get("speaker_relation"),
            (data or {}).get("pronoun_pair_used"),
            force_cut,
            (raw_out or "")[:80],
        )
        out = _finalize_shortened(
            raw_out, text, target, label=f"LLM reslot lan {int(attempt)}",
        )
    if not out:
        return None
    log.info("%s chap nhan: %d->%d chars | %s", label, len(text), len(out), out[:80])
    return out


_VOICE_LOCK_KEYS = (
    "voice_profile",
    "tts_voice_name",
    "voice_id",
    "tts_voice",
    "openai_voice",
    "speaker_rate",
    "speaker_pitch",
    "speaker_volume",
    "median_f0_hz",
    "gender",
    "age",
)


def locked_cast_payload(segments: list[dict]) -> list[dict]:
    """Mot dong / speaker_id. Khong loc het khi relation con trong — overflow van can sid."""
    by: dict[str, dict] = {}
    for seg in segments:
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "").strip()
        if not sid:
            continue
        rel = str(seg.get("speaker_relation") or "").strip()
        pair = str(seg.get("pronoun_pair_used") or "").strip()
        prev = by.get(sid) or {}
        extra_tts = str(seg.get("tts_voice") or "")
        row = {
            "speaker_id": sid,
            "speaker_relation": rel or str(prev.get("speaker_relation") or ""),
            "pronoun_pair_used": pair or str(prev.get("pronoun_pair_used") or ""),
            "voice_profile": str(
                seg.get("voice_profile") or prev.get("voice_profile") or ""
            ).strip(),
            "tts_voice_name": str(
                seg.get("tts_voice_name") or extra_tts
                or prev.get("tts_voice_name") or ""
            ).strip(),
            "voice_id": str(seg.get("voice_id") or prev.get("voice_id") or "").strip(),
        }
        f0 = seg.get("median_f0_hz")
        if f0 is None:
            f0 = prev.get("median_f0_hz")
        if f0 is not None:
            row["median_f0_hz"] = f0
        for k in _VOICE_LOCK_KEYS:
            if k in row:
                continue
            val = seg.get(k)
            if val in (None, ""):
                val = prev.get(k)
            if val not in (None, ""):
                row[k] = val
        by[sid] = row
    return list(by.values())


def apply_cast_lock(segments: list[dict], cast_by_sid: dict) -> list[dict]:
    """Gan cung speaker_relation / pronoun_pair_used cho MOI doan cung speaker_id.

    Cung 1 cau ZH o 2 vi tri (cung speaker) ra cung 1 cap — khong doan lai theo i.
    Cung stamp voice_profile / tts_voice_name neu locked_cast co.
    """
    by_zh: dict[tuple[str, str], dict] = {}
    out: list[dict] = []
    for seg in segments:
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "").strip()
        zh = str(seg.get("source_text") or seg.get("text") or "").strip()
        c = dict(cast_by_sid.get(sid) or {})
        rel = str(c.get("speaker_relation") or seg.get("speaker_relation") or "").strip()
        pair = str(c.get("pronoun_pair_used") or seg.get("pronoun_pair_used") or "").strip()
        key = (sid, zh)
        if key in by_zh:
            rel = str(by_zh[key].get("speaker_relation") or rel)
            pair = str(by_zh[key].get("pronoun_pair_used") or pair)
        elif sid and zh and (rel or pair):
            by_zh[key] = {"speaker_relation": rel, "pronoun_pair_used": pair}
        merged = {
            **seg,
            "speaker_relation": rel,
            "pronoun_pair_used": pair,
        }
        for k in _VOICE_LOCK_KEYS:
            val = c.get(k)
            if val not in (None, ""):
                merged[k] = val
        out.append(merged)
    return out


def _heuristic_cast_lock(segments: list[dict]) -> dict[str, dict]:
    """Fallback khi LLM khoa that bai: suy tu speaker_id + tu than toc ZH."""
    lines_by: dict[str, list[str]] = {}
    roles: dict[str, str] = {}
    for seg in segments:
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "").strip()
        if not sid:
            continue
        zh = str(seg.get("source_text") or seg.get("text") or "")
        lines_by.setdefault(sid, []).append(zh)
        roles.setdefault(sid, str(seg.get("speaker_role") or sid))
    corpus = " ".join(" ".join(v) for v in lines_by.values())
    family = any(k in corpus for k in ("妈", "爸", "娘", "儿子", "女儿", "老公", "老婆", "阿姨", "姑姑"))
    out: dict[str, dict] = {}
    for sid, zhs in lines_by.items():
        blob = " ".join(zhs)
        slug = sid.lower()
        rel, pair = "", ""
        if slug in ("boy", "kid", "child", "son", "daughter") or "child" in slug:
            if "妈" in blob or "妈" in corpus:
                rel, pair = "con noi voi me", "con/me"
            elif "爸" in blob or "爸" in corpus:
                rel, pair = "con noi voi ba", "con/ba"
            elif family:
                rel, pair = "con noi voi nguoi lon", "con/me"
        elif slug in ("dad", "father", "ba", "bo", "papa"):
            rel, pair = "ba noi voi con", "ba/con"
        elif slug in ("mom", "mother", "mum", "me"):
            rel, pair = "me noi voi con", "me/con"
        elif slug in ("aunt", "co", "di", "gu"):
            rel, pair = "co/di noi voi chau", "co/chau"
        elif family and slug in ("narrator", "voice"):
            rel, pair = "nguoi dan", "toi/ban"
        elif not family:
            rel, pair = "nguoi la / ngang vai", "toi/ban"
        out[sid] = {
            "speaker_id": sid,
            "speaker_relation": rel,
            "pronoun_pair_used": pair,
            "role": roles.get(sid, sid),
        }
    return out


def lock_cast_relations(
    segments: list[dict],
    *,
    target: str = "vi",
    work_dir: Path | str | None = None,
) -> list[dict]:
    """Nhom D: khoa xung ho 1 LAN cho ca clip (toan bo transcript + speaker_id).

    Ghi speaker_relation / pronoun_pair_used len moi doan, roi moi dich/rut.
    """
    if not segments:
        return segments
    speakers = locked_cast_payload(segments)
    payload = []
    for i, seg in enumerate(segments):
        payload.append({
            "i": i,
            "speaker_id": str(seg.get("speaker_id") or seg.get("voice_id") or ""),
            "role": str(seg.get("speaker_role") or ""),
            "source_zh": str(seg.get("source_text") or seg.get("text") or "")[:220],
        })
    system = (
        f"Khoa xung ho long tieng 1 LAN cho CA clip (target={target}).\n"
        "Input: moi dong co speaker_id (boy/dad/aunt...) + cau Trung.\n"
        "LAM 1 bang cast: moi speaker_id dung MOT speaker_relation va MOT "
        "pronoun_pair_used cho MOI dong cua nguoi do.\n"
        "Cung 1 cau ZH xuat hien 2 vi tri (cung speaker) PHAI cung 1 cap.\n"
        "Khong mac dinh toi/ban neu la nguoi nha (me/con, ba/con, anh/em, co/chau).\n"
        "Toi/ban CHI khi nguoi la hoac ngang vai.\n"
        'Tra JSON: {"cast":[{"speaker_id":"boy","speaker_relation":"con noi voi me",'
        '"pronoun_pair_used":"con/me"}, ...]} — du moi speaker_id trong input.'
    )
    data = _llm_json(
        system, {"speakers": speakers, "lines": payload},
        temperature=0.1, kind="lock_cast", n=len(payload),
    )
    cast_by_sid: dict[str, dict] = {}
    if isinstance(data, dict) and isinstance(data.get("cast"), list):
        for c in data["cast"]:
            if not isinstance(c, dict):
                continue
            sid = str(c.get("speaker_id") or c.get("id") or "").strip()
            if not sid:
                continue
            cast_by_sid[sid] = {
                "speaker_id": sid,
                "speaker_relation": str(c.get("speaker_relation") or "").strip(),
                "pronoun_pair_used": str(c.get("pronoun_pair_used") or "").strip(),
            }
    if not cast_by_sid or not any(
        c.get("pronoun_pair_used") or c.get("speaker_relation") for c in cast_by_sid.values()
    ):
        log.warning("Khoa xung ho LLM trong/that bai — fallback heuristic tu transcript")
        cast_by_sid = _heuristic_cast_lock(segments)
    out = apply_cast_lock(segments, cast_by_sid)
    filled = sum(1 for s in out if s.get("pronoun_pair_used") or s.get("speaker_relation"))
    log.info(
        "Khoa xung ho: %d speaker, %d/%d doan co relation/pronoun | %s",
        len(cast_by_sid), filled, len(out),
        ", ".join(
            f"{c.get('speaker_id')}={c.get('pronoun_pair_used') or '-'}"
            for c in cast_by_sid.values()
        ),
    )
    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "cast_lock.json").write_text(
            json.dumps({"cast": list(cast_by_sid.values())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            cast_path = wp / "cast.json"
            if cast_path.exists():
                prev = json.loads(cast_path.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and isinstance(prev.get("speakers"), list):
                    for row in prev["speakers"]:
                        sid = str(row.get("speaker_id") or "")
                        c = cast_by_sid.get(sid) or {}
                        row["speaker_relation"] = c.get("speaker_relation") or ""
                        row["pronoun_pair_used"] = c.get("pronoun_pair_used") or ""
                    prev["locked_cast"] = list(cast_by_sid.values())
                    cast_path.write_text(
                        json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8",
                    )
        except Exception:  # noqa: BLE001
            log.exception("Khong cap nhat cast.json sau khi khoa xung ho")
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
    from .job_timing import where

    if not segments:
        return []

    with where("translate_budgeted"):
        return _translate_segments_budgeted_inner(
            segments, video_dur=video_dur, target=target,
            chars_per_sec=chars_per_sec, work_dir=work_dir,
        )


def _translate_segments_budgeted_inner(
    segments: list[dict],
    *,
    video_dur: float,
    target: str = "vi",
    chars_per_sec: float | None = None,
    work_dir: Path | str | None = None,
) -> list[dict]:
    from .job_timing import add_bucket, phase

    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    lang = _LANG.get((target or "vi").lower(), target or "Vietnamese")
    payload = []
    budgets: list[dict] = []

    for i, seg in enumerate(segments):
        src = str(seg.get("source_text") or seg.get("text") or "").strip()
        slot = segment_slot(segments, i, video_dur)
        max_chars = target_chars_for_slot(slot, chars_per_sec=cps)
        prev_src = ""
        next_src = ""
        if i > 0:
            prev_src = str(segments[i - 1].get("source_text") or segments[i - 1].get("text") or "")
        if i + 1 < len(segments):
            next_src = str(segments[i + 1].get("source_text") or segments[i + 1].get("text") or "")
        payload.append({
            "i": i + 1,
            "slot_sec": round(slot, 2),
            "max_chars": max_chars,
            "source": src,
            "prev_context": prev_src,
            "next_context": next_src,
            "speaker_id": str(seg.get("speaker_id") or seg.get("voice_id") or ""),
            "locked_relation": str(seg.get("speaker_relation") or ""),
            "locked_pronoun": str(seg.get("pronoun_pair_used") or ""),
        })
        budgets.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "slot_sec": round(slot, 3),
            "target_chars": max_chars,
            "source_text": src,
        })

    from .preflight import allow_degraded, alert_ops, require_openai

    key = require_openai(kind="draft_mode")
    if not key:
        log.warning("DYFLOW_ENV=dev — fallback Google + shorten theo budget")
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    try:
        from openai import OpenAI
    except ImportError:
        msg = "Chua cai openai — khong dich budgeted duoc"
        if allow_degraded():
            alert_ops("draft_mode", msg)
            return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)
        alert_ops("preflight_fail", msg)
        raise RuntimeError(msg) from None

    pre_lock = locked_cast_payload(segments)
    lock_note = ""
    if any(c.get("pronoun_pair_used") or c.get("speaker_relation") for c in pre_lock):
        lock_note = (
            "LOCKED CAST da khoa 1 lan cho ca clip — BAT BUOC dung dung cap, "
            "KHONG doan lai theo tung dong. Cung speaker_id / cung cau ZH "
            "phai cung pronoun_pair_used.\n"
            + json.dumps(pre_lock, ensure_ascii=False)
            + "\n"
        )

    system = (
        f"Ban la bien dich vien long tieng. Dich tung dong Trung sang {lang} "
        "de DOC THANH TIENG vua slot — KHONG dich dai roi cat sau.\n"
        f"{_pronoun_rules('vi' if (target or 'vi').lower() == 'vi' else target)}"
        f"{lock_note}"
        "LAM BUOC 0 CHO CA CLIP truoc khi dich: dung locked_cast neu co; "
        "neu chua khoa thi lap cast (speaker_id + "
        "speaker_relation + pronoun_pair_used), roi dich moi dong dung dung cap do.\n"
        "Input JSON: {\"locked_cast\":[...],\"lines\":[...]} moi line: source, slot_sec, "
        "max_chars, prev_context, next_context, speaker_id, locked_relation, locked_pronoun.\n"
        "Quy tac:\n"
        f"- Chi {lang}. Moi dong <= max_chars (tinh ca khoang trang).\n"
        "- Van noi Trung luoc chu ngu/lien tu — dung them tru khi toi nghia.\n"
        '- "还差X" = con THIEU X. "没有/没" = phu dinh. "才" = moi vua. '
        '"怎么又" = sao lai...nua.\n'
        "- Giu mach hoi-dap/nhan-qua voi prev_context/next_context.\n"
        "- Vuot max_chars: chon tu ngan, bo trang tu, gop y trung — giu so lieu/"
        "cau hoi/cau tra loi.\n"
        "- 痔疮/肉球 = hemorrhoid. 葵花凝胶 = sunflower care gel. "
        "肛周 = perianal (NOT pregnancy).\n"
        "- Return JSON: {\"cast\":[{\"speaker_id\":\"...\",\"speaker_relation\":\"...\","
        "\"pronoun_pair_used\":\"...\"}],"
        "\"lines\":[{\"i\":1,\"text\":\"...\",\"speaker_relation\":\"...\","
        "\"pronoun_pair_used\":\"...\"}, ...]} same line count."
    )

    client = OpenAI(api_key=key)
    t_llm = time.perf_counter()
    try:
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0.15,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(
                    {"locked_cast": pre_lock, "lines": payload},
                    ensure_ascii=False,
                )},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        from .job_timing import add_llm
        add_llm("translate", time.perf_counter() - t_llm, n=len(segments))
        log.exception("LLM dich budget loi — fallback Google")
        from .preflight import alert_ops
        alert_ops("llm_translate_fail", f"budgeted: {e}")
        with phase("translate_fallback_google", n=len(segments), why="api_error"):
            return _translate_budgeted_fallback(
                segments, video_dur=video_dur, target=target, cps=cps,
            )
    from .job_timing import add_llm
    add_llm("translate", time.perf_counter() - t_llm, n=len(segments))

    t_parse = time.perf_counter()
    parsed = _parse_line_rows(raw, len(segments))
    add_bucket(
        "translate_parse",
        time.perf_counter() - t_parse,
        ok=bool(parsed),
        raw_chars=len(raw or ""),
    )
    if not parsed:
        log.warning(
            "LLM dich budget: khong parse duoc JSON (raw %d chars) — fallback Google "
            "(day la khoang thoi gian hay 'mat tich' ~10 phut tren clip dai)",
            len(raw or ""),
        )
        from .preflight import alert_ops
        alert_ops("llm_translate_fail", "budgeted: khong parse duoc JSON")
        with phase("translate_fallback_google", n=len(segments)):
            return _translate_budgeted_fallback(
                segments, video_dur=video_dur, target=target, cps=cps,
            )

    rows, cast = parsed
    cast_by_sid = {
        str(c.get("speaker_id") or ""): c
        for c in cast
        if c.get("speaker_id")
    }
    for c in pre_lock:
        sid = str(c.get("speaker_id") or "")
        if not sid:
            continue
        if c.get("speaker_relation") or c.get("pronoun_pair_used"):
            cast_by_sid[sid] = {
                "speaker_id": sid,
                "speaker_relation": c.get("speaker_relation") or (cast_by_sid.get(sid) or {}).get("speaker_relation") or "",
                "pronoun_pair_used": c.get("pronoun_pair_used") or (cast_by_sid.get(sid) or {}).get("pronoun_pair_used") or "",
            }

    out: list[dict] = []
    for seg, row, bud in zip(segments, rows, budgets):
        text = re.sub(r"\s+", " ", row.get("text") or "").strip()
        if _has_cjk(text):
            text = _CJK_RE.sub("", text).strip()
        src = str(seg.get("source_text") or seg.get("text") or "")
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "")
        pre_rel = str(seg.get("speaker_relation") or "").strip()
        pre_pair = str(seg.get("pronoun_pair_used") or "").strip()
        rel = (
            pre_rel
            or row.get("speaker_relation")
            or (cast_by_sid.get(sid) or {}).get("speaker_relation")
            or ""
        )
        pair = (
            pre_pair
            or row.get("pronoun_pair_used")
            or (cast_by_sid.get(sid) or {}).get("pronoun_pair_used")
            or ""
        )
        if sid and sid not in cast_by_sid and (rel or pair):
            cast_by_sid[sid] = {
                "speaker_id": sid,
                "speaker_relation": rel,
                "pronoun_pair_used": pair,
            }
        if len(text) > bud["target_chars"] + 2:
            shorter = shorten_for_slot(
                text,
                slot_sec=bud["slot_sec"],
                source_text=src,
                target=target,
                chars_per_sec=cps,
                prev_context=str((payload[bud["i"]] or {}).get("prev_context") or ""),
                next_context=str((payload[bud["i"]] or {}).get("next_context") or ""),
                locked_relation=rel,
                locked_pronoun=pair,
                locked_cast=list(cast_by_sid.values()),
                speaker_id=sid,
                i=bud["i"],
            )
            if shorter:
                text = shorter
        out.append({
            **seg,
            "text": text or seg.get("text", ""),
            "source_text": src,
            "target_chars": bud["target_chars"],
            "slot_sec": bud["slot_sec"],
            "speaker_relation": rel,
            "pronoun_pair_used": pair,
        })
        log.info(
            "Dich budget #%d: slot %.1fs max %d -> %d chars relation=%s pronoun=%s | %s",
            bud["i"], bud["slot_sec"], bud["target_chars"], len(text), rel, pair, text[:60],
        )

    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "translate_budget.json").write_text(
            json.dumps(
                {
                    "chars_per_sec": cps,
                    "cast": list(cast_by_sid.values()),
                    "segments": budgets,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    out = apply_cast_lock(out, cast_by_sid)
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
            locked_relation=str(seg.get("speaker_relation") or ""),
            locked_pronoun=str(seg.get("pronoun_pair_used") or ""),
            locked_cast=locked_cast_payload(segments),
            speaker_id=str(seg.get("speaker_id") or seg.get("voice_id") or ""),
            i=i,
        )
        if shorter:
            text = shorter
        out.append({
            **seg,
            "text": text,
            "source_text": src,
            "target_chars": max_chars,
            "slot_sec": round(slot, 3),
            "speaker_relation": str(seg.get("speaker_relation") or ""),
            "pronoun_pair_used": str(seg.get("pronoun_pair_used") or ""),
        })
    by_sid = {
        c["speaker_id"]: c
        for c in locked_cast_payload(segments)
        if c.get("speaker_id")
    }
    return apply_cast_lock(out, by_sid)


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
        from .preflight import allow_degraded, require_openai
        require_openai(kind="review_skip")
        if allow_degraded():
            log.warning("Thieu OPENAI_API_KEY — khong review noi dung (dev)")
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

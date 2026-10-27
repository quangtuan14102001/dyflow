"""Hau xu ly ban dich Google — khong train duoc Google free.

Lam "thong minh" hon bang:
1) Glossary ten rieng ZH / pinyin → Viet
2) Sua calque / cu truong Google thuong gap (CN→VI)

Khong thay the LLM. Chi giam loi hien nhien.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .utils import log

# Ten rieng / dia danh hay gap (co the mo rong trong file user sau).
_NAME_ZH: dict[str, str] = {
    "许爱玲": "Hứa Ái Lệ",
    "許愛玲": "Hứa Ái Lệ",
    "爱玲": "Ái Lệ",
    "愛玲": "Ái Lệ",
    "秦川": "Tần Xuyên",
    "程放": "Trình Phóng",
    "林优娜": "Lâm Ưu Na",
    "林優娜": "Lâm Ưu Na",
    "优娜": "Ưu Na",
    "優娜": "Ưu Na",
    "洛希": "Lạc Hy",
    "阿杰": "A Kiệt",
    "太阳组": "nhóm Thái Dương",
    "太陽組": "nhóm Thái Dương",
}

# Pinyin / tieng Anh Google hay de sot → Viet
_NAME_LATIN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bXu\s*Aili(?:ng)?\b", re.I), "Hứa Ái Lệ"),
    (re.compile(r"\bXu\s*Allie\b", re.I), "Hứa Ái Lệ"),
    (re.compile(r"\bXu\s*Ailin\b", re.I), "Hứa Ái Lệ"),
    (re.compile(r"\bEli\s*Xu\b", re.I), "Hứa Ái Lệ"),
    (re.compile(r"\bElin\b", re.I), "Ái Lệ"),
    (re.compile(r"\bXingchuan\b", re.I), "Tần Xuyên"),
    (re.compile(r"\bQin\s*Chuan\b", re.I), "Tần Xuyên"),
    (re.compile(r"\bQingchuan\b", re.I), "Tần Xuyên"),
    (re.compile(r"\bHarukawa\b", re.I), "Tần Xuyên"),
    (re.compile(r"\bCheng\s*Fang\b", re.I), "Trình Phóng"),
    (re.compile(r"\bYuna\b", re.I), "Ưu Na"),
    (re.compile(r"\bHei\s*Zhongjian\b", re.I), "môi giới đen"),
]

# Cum Google calque → Viet tu nhien hon (ap dung tren ban dich)
_PHRASE_FIX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"cô gái điếm", re.I), "người thuê phòng"),
    (re.compile(r"\bneo người\b", re.I), "streamer"),
    (re.compile(r"quán bar sành ăn Taro", re.I), "ẩm thực"),
    (re.compile(r"Sun Group Big V", re.I), "KOL lớn của nhóm Thái Dương"),
    (re.compile(r"Equation Guitarist", re.I), "nghề guitarist"),
    (re.compile(r"Aigasa", re.I), "Ái Lệ"),
    (re.compile(r"phương pháp làm sạch bốn tay", re.I), "bốn bài, thanh toán trong ngày"),
    (re.compile(r"nhảy múa\?", re.I), "khuấy động không khí?"),
    (re.compile(r"nhóm tâm trạng", re.I), "người khuấy động không khí"),
    (re.compile(r"họ đang 1\.000 một đêm", re.I), "họ được 1.000 một đêm"),
    (re.compile(r"Nếu nó không thích hợp cho bạn để cung cấp cho họ", re.I), "Nếu bạn chỉ trả họ"),
    (re.compile(r"Thích hát hay không", re.I), "Hát thì hát, không hát thì thôi"),
    (re.compile(r"Bạn có trả tiền thuê nhà cho tôi không\?", re.I), "Anh trả tiền thuê nhà cho tôi à?"),
    (re.compile(r"Đó không phải là bạn gái của tôi, vì vậy đừng đưa ra quyết định thay tôi", re.I),
     "Tôi cũng đâu phải bạn gái anh, vậy đừng quyết định thay tôi"),
]


def polish_google_vi(text: str, *, source_zh: str = "") -> str:
    """Sua nhe ban dich Google VI. Giu nguyen neu khong match."""
    t = (text or "").strip()
    if not t:
        return t
    src = source_zh or ""
    # 1) Chen ten tu source ZH neu Google de pinyin / sai
    for zh, vi in _NAME_ZH.items():
        if zh in src:
            # Thay pinyin thô neu co; neu chua co ten Viet thi khong ep chen
            pass
    for pat, vi in _NAME_LATIN:
        t = pat.sub(vi, t)
    for pat, repl in _PHRASE_FIX:
        t = pat.sub(repl, t)
    # 2) Neu source co ten ZH ma ban dich van con pinyin — da xu ly o tren
    t = re.sub(r"\s+", " ", t).strip()
    return t


def polish_segments_google(segments: list[dict], *, target: str = "vi") -> list[dict]:
    if (target or "vi").lower() not in ("vi", "vietnamese"):
        return segments
    out: list[dict] = []
    n_fix = 0
    for seg in segments:
        src = str(seg.get("source_text") or "")
        old = str(seg.get("text") or "")
        new = polish_google_vi(old, source_zh=src)
        if new != old:
            n_fix += 1
        out.append({**seg, "text": new})
    if n_fix:
        log.info("Google polish: sua %d/%d doan (glossary + calque)", n_fix, len(segments))
    return out

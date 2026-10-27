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

try:
    from .job_timing import add_llm
except Exception:
    def add_llm(*args, **kwargs):
        pass


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Cau dich co the bi cat cuoi: ket bang lien tu / gioi tu treo (tong quat, khong gan chu de).
_HANGING_TAIL = re.compile(
    r"(?:^|\s)(?:và|hoặc|nhưng|thì|của|để|từ|với|về|trong|trên|dưới|"
    r"khi|vì|nếu|dù|nên|bằng|như|là|mà|rằng|này|đó)\.?\s*$",
    re.I,
)
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


def _translate_model() -> str:
    """Model dich theo provider hien tai (OpenAI / DeepSeek)."""
    from .llm_provider import llm_model

    return llm_model()


def _load_key() -> str:
    from .llm_provider import llm_api_key

    return llm_api_key()


def _openai_client(**kwargs):
    from .llm_provider import make_llm_client

    return make_llm_client(**kwargs)

def _archaic_address_rules(lang: str) -> str:
    """Tu xung/goi co trang — giu Han-Viet, khong dich nghia den."""
    # Chi can thiet cho ban dich Viet (quy uoc phim co trang VN).
    if (lang or "").lower() not in ("vietnamese", "vi"):
        return (
            "If the scene is historical / wuxia / xianxia, keep classical "
            "first-person self-address terms as established loan forms for "
            f"{lang} — do NOT paraphrase them as third-person nouns "
            "(e.g. 老夫 is NOT 'old man').\n"
        )
    return (
        "Neu ngu canh la co trang / tien hiep / tu tien, GIU NGUYEN dang "
        "Han-Viet (VIET CO DAU) cho cac tu TU XUNG / GOI sau — KHONG dich "
        "nghia den sang tieng Viet thuong. Day la quy uoc dich phim co trang chuan.\n"
        "- 老夫 -> lão phu (KHONG dich 'ông già'; KHONG viet 'lao phu' khong dau)\n"
        "- 朕 -> trẫm\n"
        "- 本座 -> bổn tọa\n"
        "- 哀家 -> ai gia\n"
        "- 本尊 -> bổn tôn\n"
        "- 贫道 / 貧道 -> bần đạo\n"
        "- 老衲 -> lão nạp\n"
        "- 在下 -> tại hạ\n"
        "- 阁下 / 閣下 -> các hạ\n"
        "- 姑娘 -> cô nương\n"
        "- 公子 -> công tử\n"
        "- 前辈 / 前輩 -> tiền bối\n"
        "- 晚辈 / 晚輩 -> vãn bối\n"
        "- 本王 -> bổn vương\n"
        "- 微臣 -> vi thần\n"
        "- 陛下 -> bệ hạ\n"
        "Luu y: day la DAI TU nhan xung (ngoi 1 / goi nguoi), KHONG phai "
        "danh tu mo ta ngoi 3. Khi rut gon cung PHAI giu dung tu Han-Viet do "
        "(co dau), KHONG doi thanh tu khac.\n"
    )


def _proper_noun_rules(lang: str) -> str:
    """Than thoai Han-Viet + ten to chuc/doi hinh dich Viet (khong de tieng Anh)."""
    if (lang or "").lower() not in ("vietnamese", "vi"):
        return (
            "Mythology / classical Chinese proper names: use the target-language "
            "established forms, NOT English pinyin (e.g. 白泽 ≠ Bai Ze). "
            "Fictional military formations / orgs / tech: translate fully into "
            f"{lang}, do not leave half-English calques (XX Formation).\n"
        )
    return (
        "TEN RIENG / DIA DANH / THUAT NGU (bat buoc):\n"
        "A) Than thoai / dien tich Trung Hoa: dung dung Han-Viet chuan, "
        "KHONG phien am kieu Anh/Pinyin latinh hoa.\n"
        "   vd: 白泽=Bạch Trạch (KHONG 'Bai Ze'); 夸父=Khoa Phụ (KHONG 'Kwafu'; "
        "chu y 夸父 trong than thoai la NAM — duoi mat troi, KHONG mac dinh "
        "'hoang hau'); 精卫=Tinh Vệ; 女娲=Nữ Oa; 盘古=Bàn Cổ; 后羿=Hậu Nghệ.\n"
        "B) Dia danh / thanh pho / to chuc: 1 ban dich on dinh (vd 北京=Bắc Kinh; "
        "蜀山=Thục Sơn) — KHONG doi ten giua cac doan trong cung clip.\n"
        "C) Ten to chuc / doi hinh quan su / ky thuat hu cau "
        "(vd: XX Formation, XX Bow, XX Palace, Containment…): PHAI dich HAN "
        "sang tieng Viet co nghia, KHONG giu nguyen tieng Anh nua-Viet-nua-Anh. "
        "vd: Azure Dragon Formation→Đội Hình Thanh Long; "
        "Jingwei Formation→Đội Hình Tinh Vệ; "
        "Wide Containment Bow→Cung Vây Rộng (hoac ten Viet hoa tuong duong). "
        "Neu khong chac nghia: dich thoang theo am huong (hung trang, co kinh) "
        "con hon de nguyen tieng Anh.\n"
        "D) Neu co LOCKED TERMS / term_lock: BAT BUOC dung dung locked_vi da khoa "
        "cho ca clip — KHONG doi ten giua cac doan.\n"
    )


def _drama_dub_rules(lang: str) -> str:
    """Quy tac dich thoai phim ngan / Douyin — tranh Google-calque."""
    is_vi = (lang or "").lower() in ("vietnamese", "vi", "tiếng việt", "tieng viet")
    lang_name = "Tiếng Việt (Vietnamese)" if is_vi else lang
    en_ban = (
        "TUYET DOI 100% dung Tieng Viet. CAM dich sang tieng Anh hoac de lai bat ky cau tieng Anh nao."
        if is_vi
        else f"TUYET DOI 100% dung {lang_name}. KHONG de lai tieng Viet hay pha tron ngon ngu khac."
    )
    if is_vi:
        rules_2_3 = (
            "2. Ten rieng Trung → phien am Viet dung (vd: 许爱玲/許愛玲 = Hứa Ái Lệ; "
            "秦川 = Tần Xuyên; 程放 = Trình Phóng; 林优娜 = Lâm Ưu Na). "
            "KHONG de pinyin thô (Xu Ailing, Qin Chuan, Cheng Fang, Xu Ai Li).\n"
            "3. Giu dung quan he xung ho (me/con, anh/em, toi/ban) theo locked_cast — "
            "KHONG mac dinh toi/ban khi la nguoi nha; KHONG doi cap xung ho khi rut gon.\n"
        )
    else:
        rules_2_3 = (
            f"2. Chinese proper names: use standard localized forms or Pinyin in {lang_name} (vd: Xu Ailing, Qin Chuan). "
            "KHONG dung phien am Han-Viet hay tieng Viet.\n"
            f"3. Address & character relationships: maintain natural spoken register in {lang_name} consistently.\n"
        )
    return (
        f"Ban la bien dich vien LONG TIENG chuyen thoai phim ngan / Douyin sang {lang_name}.\n"
        "Dich DE DOC THANH TIENG tren man hinh, khong phai dich van ban may.\n"
        "Thu tu uu tien (KHONG dao nguoc):\n"
        "  (A) Dung nghia source + dung vai/xung ho da khoa\n"
        "  (B) Ten rieng / thuat ngu nhat quan (Han-Viet hoac Viet hoa)\n"
        "  (C) Tu nhien, de hieu trong ngu canh prev/next\n"
        "  (D) Do dai — chi rut khi van giu duoc A–C; tha vuot nhe hon sai vai/sai nghia\n"
        "Quy tac BAT BUOC:\n"
        f"1. Chi {lang_name} tu nhien, khau ngu. {en_ban}\n"
        f"{rules_2_3}"
        "4. Giu so tien, thoi gian, y chinh; cau de noi; dung dung nghia "
        "(还差X=con thieu X; 没有=phu dinh; 才=moi vua; 怎么又=sao lai...nua).\n"
        "5. Neu draft Google vo nghia / don tu / giong Anh dich thuoc — VIET LAI "
        "tu source Trung, KHONG sua qua loa draft do.\n"
        "6. Mot cau = mot y thoai ro; khong cat ngang giua cau.\n"
        f"{_archaic_address_rules(lang)}"
        f"{_proper_noun_rules(lang)}"
    )


def _topic_context_block(topic_context: str, target: str = "vi") -> str:
    """Chen GENRE / topic_context GIUA van phong va phan format JSON.

    Noi dung chi la van phong — moi chi dan dinh dang trong do bi vo hieu
    vi phan Tra JSON / Return JSON luon nam SAU block nay.
    """
    from .genre_profiles import sanitize_genre_prompt

    topic = sanitize_genre_prompt(topic_context or "")
    if not topic:
        return ""
    is_vi = (target or "vi").lower() in ("vi", "vietnamese", "tiếng việt", "tieng viet")
    tgt_name = "Tiếng Việt" if is_vi else _LANG.get((target or "vi").lower(), target or "target language")
    lang_warning = ""
    if not is_vi:
        lang_warning = (
            f"CHU Y: Ngon ngu dich cua video nay la {tgt_name} (KHONG PHAI tieng Viet). "
            "Moi vi du van phong trong phan GENRE duoi day CHI mang tinh tham khao — "
            f"BAT BUOC 100% ban dich phai bang {tgt_name}.\n"
        )
    return (
        "\n=== GENRE STYLE (van phong the loai — CHI chon tu/xung ho/giong dieu) ===\n"
        "Phan nay KHONG duoc doi dinh dang dau ra hay ngon ngu dich.\n"
        f"{lang_warning}"
        f"{topic}\n"
        "=== HET GENRE STYLE — tiep theo la quy tac dich + FORMAT JSON bat buoc ===\n"
    )


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


def _openai_fail_message(exc: BaseException | None = None, *, empty: bool = False) -> str:
    """Thong bao ro khi user chon OpenAI/DeepSeek — khong am tham doi Google."""
    if empty:
        from .llm_provider import current_llm_provider

        if current_llm_provider() == "deepseek":
            return "Key DeepSeek khong hop le, vui long kiem tra lai."
        if current_llm_provider() == "qwen":
            return "Key Qwen khong hop le, vui long kiem tra lai."
        return "Key OpenAI khong hop le, vui long kiem tra lai."
    raw = str(exc or "").lower()
    authish = (
        "authentication",
        "unauthorized",
        "invalid_api_key",
        "incorrect api key",
        "401",
        "permission",
        "api key",
    )
    if any(t in raw for t in authish):
        return "Key OpenAI/DeepSeek khong hop le, vui long kiem tra lai."
    if exc is None:
        return "Dich LLM that bai — khong chuyen sang Google. Kiem tra key hoac thu lai."
    return (
        "Dich LLM that bai — khong chuyen sang Google. "
        f"Chi tiet: {exc}"
    )


# Cap xung ho gia dinh — dung de bat ban dich loai "toi/ban" khi da khoa me/con...
_FAMILY_PRONOUN = {
    "me", "mẹ", "má", "ba", "bố", "bo", "con", "anh", "chi", "chị", "em",
    "co", "cô", "chu", "chú", "bac", "bác", "di", "dì", "cau", "cậu",
    "chau", "cháu", "vo", "vợ", "chong", "chồng", "ong", "ông", "bà",
}
_PINYIN_NAME = re.compile(
    r"\b(?:Xu|Qin|Cheng|Lin|Wang|Zhang|Liu|Zhao|Huang|Zhou|Wu|Xu)\s+"
    r"(?:Ai|Li|Na|Fang|Chuan|Ling|You|Ning|Hua|Jun|Wei|Ming)\b"
    r"|\bXu\s+A(?:i)?\s+L[iíị]\b",
    re.I,
)


def _looks_pinyin_name(text: str) -> bool:
    """True neu ban dich con ten rieng dang pinyin thô (Xu Ai Ling...)."""
    return bool(_PINYIN_NAME.search(text or ""))


def _norm_vi_token(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _pronoun_lock_ok(text: str, pair: str) -> bool:
    """True neu ban dich khong manh xung ho khoa (gia dinh)."""
    t = _norm_vi_token(text)
    pair = _norm_vi_token(pair)
    if not pair or "/" not in pair or len(t) < 10:
        return True
    a, b = [p.strip() for p in pair.split("/", 1)]
    sides = {a, b}
    if not (sides & _FAMILY_PRONOUN):
        return True

    def _has(term: str) -> bool:
        if not term or len(term) < 2:
            return False
        alts = {term}
        # me <-> mẹ (khi mat dau)
        if term in ("me", "mẹ"):
            alts.update({"me", "mẹ", "má"})
        if term in ("ba", "bố", "bo"):
            alts.update({"ba", "bố", "bo", "cha"})
        if term in ("co", "cô"):
            alts.update({"co", "cô"})
        if term in ("chau", "cháu"):
            alts.update({"chau", "cháu"})
        return any(re.search(rf"(?<!\w){re.escape(x)}(?!\w)", t) for x in alts)

    if _has(a) or _has(b):
        return True
    # Dai ma chi con toi/ban → sai vai
    if re.search(r"(?<!\w)(tôi|toi|bạn|ban)(?!\w)", t):
        return False
    return len(t) < 24


def _english_term_leak(text: str) -> bool:
    """True neu ban dich con ten rieng/doi hinh de tieng Anh (loi the loai)."""
    t = text or ""
    if not t:
        return False
    if re.search(
        r"\b(?:Formation|Containment|Palace|Bow)\b|"
        r"\b(?:Bai\s*Ze|Kwafu|Jingwei|Azure\s+Dragon|Wide\s+Containment)\b",
        t,
        re.I,
    ):
        return True
    # Pinyin myth names without Viet diacritics form
    if re.search(r"\b(?:BaiZe|JingWei)\b", t):
        return True
    return False


def _is_unwanted_english(text: str, is_target_vi: bool = True) -> bool:
    """True neu ban dich bi ra tieng Anh trong khi target la Tieng Viet."""
    if not is_target_vi or not text:
        return False
    words = re.findall(r"\b[a-zA-Z']+\b", text)
    if len(words) < 2:
        return False
    en_stop = {
        "i", "you", "he", "she", "it", "we", "they", "the", "a", "an", "is", "are", "was", "were",
        "can", "cant", "can't", "dont", "don't", "get", "lost", "just", "want", "to", "go", "on",
        "baby", "fast", "give", "me", "right", "and", "one", "more", "thing", "who", "taught",
        "that", "if", "your", "where", "what", "how", "why", "not", "have", "has", "do", "did",
        "this", "there", "my", "our", "him", "her", "them", "fourth", "master", "loser",
    }
    en_count = sum(1 for w in words if w.lower() in en_stop)
    return en_count >= 2 or (len(words) >= 3 and (en_count / len(words)) >= 0.35)


def _parse_term_lock(raw_terms: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw_terms, list):
        return out
    seen: set[str] = set()
    for item in raw_terms:
        if not isinstance(item, dict):
            continue
        src = str(
            item.get("source")
            or item.get("source_zh")
            or item.get("zh")
            or item.get("term")
            or ""
        ).strip()
        vi = str(
            item.get("locked_vi")
            or item.get("vi")
            or item.get("translation")
            or item.get("text")
            or ""
        ).strip()
        if not src or not vi:
            continue
        key = src.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": src,
            "locked_vi": vi,
            "kind": str(item.get("kind") or item.get("type") or "").strip(),
        })
    return out


def load_term_lock(
    segments: list[dict] | None = None,
    work_dir: Path | str | None = None,
) -> list[dict]:
    """Doc term_lock tu work_dir hoac stamp tren segment."""
    if work_dir:
        wp = Path(work_dir)
        for name in ("term_lock.json", "cast_lock.json"):
            path = wp / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    terms = data.get("terms") or data.get("locked_terms")
                else:
                    terms = data
                parsed = _parse_term_lock(terms)
                if parsed:
                    return parsed
            except Exception:  # noqa: BLE001
                log.debug("Doc %s loi", name, exc_info=True)
    for seg in segments or []:
        raw = seg.get("term_lock") or seg.get("locked_terms")
        parsed = _parse_term_lock(raw)
        if parsed:
            return parsed
    return []


def apply_term_lock(segments: list[dict], terms: list[dict]) -> list[dict]:
    """Stamp cung term_lock len moi doan (tham chieu khi dich/rut)."""
    if not terms:
        return segments
    locked = _parse_term_lock(terms)
    return [{**seg, "term_lock": locked} for seg in segments]


def _term_lock_note(terms: list[dict]) -> str:
    """Ghi chu LOCKED TERMS cho prompt dich/rut."""
    locked = _parse_term_lock(terms)
    if not locked:
        return ""
    return (
        "LOCKED TERMS da khoa 1 lan cho ca clip — BAT BUOC dung dung locked_vi. "
        "KHONG doi ten giua cac doan; KHONG thay bang tieng Anh / Pinyin.\n"
        + json.dumps(locked, ensure_ascii=False)
        + "\n"
    )


def _archaic_literal_bad(source_zh: str, text_vi: str) -> bool:
    """True neu dich nghia den sai tu xung co trang thong dung."""
    zh = source_zh or ""
    vi = text_vi or ""
    if not zh or not vi:
        return False
    # 老夫 -> ong gia (sai the loai)
    if "老夫" in zh and re.search(r"ông\s*già|ong\s*gia", vi, re.I):
        return True
    # 本座 -> mat 'bon toa' ma doi thanh toi/ta
    if "本座" in zh and not re.search(r"b[oôố]n\s*t[oọ]a|bon\s*toa", vi, re.I):
        if re.search(r"\btôi\b|\bta\b", vi, re.I):
            return True
    if "朕" in zh and not re.search(r"trẫm|tram", vi, re.I):
        if re.search(r"ông\s*vua|nhà\s*vua|\btôi\b", vi, re.I):
            return True
    return False


def _looks_broken(text: str, draft: str) -> bool:
    """Phat hien ban dich loi cau truc (chu Han sot, cau cut) — khong gan chu de clip."""
    t = (text or "").strip()
    d = (draft or "").strip()
    if not t or len(t) < 4:
        return True
    if _has_cjk(t):
        return True
    # Cat cuoi: ket bang lien tu/gioi tu treo, hoac ngan bat thuong so voi draft
    if _HANGING_TAIL.search(t):
        return True
    if not re.search(r"[.!?…]$", t) and len(d) > len(t) + 12:
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


def _clean_json_loads(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text, flags=re.I).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text, flags=re.I).strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    a_brace = text.find("{")
    b_brace = text.rfind("}")
    a_bracket = text.find("[")
    b_bracket = text.rfind("]")
    candidate = text
    if a_brace >= 0 and b_brace > a_brace:
        if a_bracket >= 0 and a_bracket < a_brace and b_bracket > b_brace:
            candidate = text[a_bracket : b_bracket + 1]
        else:
            candidate = text[a_brace : b_brace + 1]
    elif a_bracket >= 0 and b_bracket > a_bracket:
        candidate = text[a_bracket : b_bracket + 1]
    candidate_clean = re.sub(r",\s*([\]\}])", r"\1", candidate)
    try:
        return json.loads(candidate_clean)
    except Exception:
        pass
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _parse_line_rows(raw: str, n: int) -> tuple[list[dict], list[dict]] | None:
    """Tra (rows, cast). rows[i].text bat buoc. Resilient voi moi LLM (DeepSeek, Qwen...)."""
    data = _clean_json_loads(raw)
    if data is None:
        return None
    lines = None
    cast_raw = None
    if isinstance(data, list):
        lines = data
    elif isinstance(data, dict):
        cast_raw = data.get("cast")
        for k in ("lines", "translations", "result", "rows", "data"):
            if isinstance(data.get(k), list):
                lines = data[k]
                break
        if lines is None and any(str(k).isdigit() for k in data.keys()):
            indexed_keys = [k for k in data.keys() if str(k).isdigit()]
            lines = [data[k] for k in sorted(indexed_keys, key=lambda x: int(x))]

    if not isinstance(lines, list) or len(lines) == 0:
        return None

    parsed_items: list[tuple[int | None, dict]] = []
    for item in lines:
        if isinstance(item, str):
            t = item.strip()
            extra = {}
            idx = None
        elif isinstance(item, dict):
            t = str(item.get("text") or item.get("vi") or item.get("translation") or item.get("t") or "").strip()
            extra = {
                "speaker_relation": str(item.get("speaker_relation") or "").strip(),
                "pronoun_pair_used": str(item.get("pronoun_pair_used") or "").strip(),
            }
            raw_idx = item.get("i") or item.get("id") or item.get("index")
            try:
                idx = int(raw_idx) if raw_idx is not None else None
            except (ValueError, TypeError):
                idx = None
        else:
            continue
        if t:
            parsed_items.append((idx, {"text": t, **extra}))

    if not parsed_items:
        return None

    cast: list[dict] = []
    if isinstance(cast_raw, list):
        for c in cast_raw:
            if isinstance(c, dict):
                cast.append({
                    "speaker_id": str(c.get("speaker_id") or "").strip(),
                    "speaker_relation": str(c.get("speaker_relation") or "").strip(),
                    "pronoun_pair_used": str(c.get("pronoun_pair_used") or "").strip(),
                })

    if len(parsed_items) == n:
        return [item[1] for item in parsed_items], cast

    indexed: dict[int, dict] = {}
    for idx, row in parsed_items:
        if idx is not None and 1 <= idx <= n and idx not in indexed:
            indexed[idx] = row

    if len(indexed) == n:
        return [indexed[i] for i in range(1, n + 1)], cast

    if len(parsed_items) > n:
        return [item[1] for item in parsed_items[:n]], cast

    if len(indexed) >= max(1, int(n * 0.8)):
        rows = []
        for i in range(1, n + 1):
            if i in indexed:
                rows.append(indexed[i])
            elif i - 1 < len(parsed_items):
                rows.append(parsed_items[i - 1][1])
            else:
                rows.append({"text": ""})
        return rows, cast

    return None


def _rewrite_batch(
    segments: list[dict],
    *,
    target: str,
    mode: str,
) -> list[dict]:
    from .preflight import allow_degraded, alert_ops

    key = _load_key()
    if not key:
        log.info("LLM polish/rewrite: thieu key LLM — giu ban dich Google")
        return segments
    try:
        import openai  # noqa: F401
    except ImportError:
        msg = "Chua cai openai — khong polish duoc ban dich"
        if allow_degraded():
            alert_ops("draft_mode", msg)
            log.warning(msg)
            return segments
        alert_ops("translate_fallback", msg)
        log.warning(msg)
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
            f"{_drama_dub_rules(lang)}"
            "Input: JSON array with source (Chinese) and draft (may be bad Google VI).\n"
            "Fix EVERY broken/awkward line into natural spoken dialogue. "
            "If draft is already natural, keep it.\n"
            "Return JSON: {\"lines\":[{\"i\":1,\"text\":\"...\"}, ...]} same count."
        )
    else:
        system = (
            f"{_drama_dub_rules(lang)}"
            "Rewrite EVERY line from Chinese `source` (use `draft` only as hint).\n"
            "Natural spoken dubbing — not literal word-for-word.\n"
            "Return JSON: {\"lines\":[{\"i\":1,\"text\":\"...\"}, ...]} same count."
        )

    client = _openai_client()
    try:
        r = client.chat.completions.create(
            model=_translate_model(),
            temperature=0.2,
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
        need = [s for s in segments if _has_cjk(s.get("text") or "")]
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
    locked_terms: list | None = None,
    topic_context: str = "",
    speaker_id: str = "",
) -> tuple[str, dict]:
    src_n = _source_lang_name(source_lang)
    tgt_code = normalize_target_lang(target_lang)
    tgt_n = _LANG.get(tgt_code, target_lang or "Vietnamese")
    terms = _parse_term_lock(locked_terms)
    system = (
        f"Ban la bien dich vien chuyen long tieng (dubbing translator) cho video ngan / Douyin.\n"
        f"Nhiem vu: dich doan thoai tu {src_n} sang {tgt_n}, sao cho ban dich khi "
        f"doc thanh tieng vua khop khung thoi gian goc — KHONG dich tu nhien roi cat sau.\n"
        f"JSON.translation BAT BUOC bang {tgt_n}. "
        f"KHONG dich sang ngon ngu khac neu target la {tgt_n}.\n"
        f"{_drama_dub_rules(tgt_n)}"
        f"{_pronoun_rules(tgt_code)}"
        f"{_term_lock_note(terms)}"
        f"{_topic_context_block(topic_context, target=tgt_code)}"
        "Quy tac them:\n"
        "1. Van noi Trung khau ngu thuong luoc chu ngu/lien tu — DUNG them day du "
        "chu ngu/lien tu ma ban goc khong co, tru khi thieu se toi nghia. "
        "Uu tien cau ngan, dung van phong noi.\n"
        "2. Neu cau goc hoi-dap hoac nhan-qua voi context truoc/sau, PHAI giu du "
        "thong tin de cau nay van co nghia khi dung canh context.\n"
        "3. Neu dich tu nhien chac chan vuot target_chars, KHONG dich day du roi "
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
        "locked_terms": terms,
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
    locked_terms: list | None = None,
    topic_context: str = "",
    speaker_id: str = "",
    aggressive: bool = False,
    ineffective_hint: str = "",
    tier4: bool = False,
) -> tuple[str, dict]:
    src_n = _source_lang_name(source_lang)
    tgt_code = normalize_target_lang(target_lang)
    tgt_n = _LANG.get(tgt_code, target_lang or "Vietnamese")
    terms = _parse_term_lock(locked_terms)
    current = re.sub(r"\s+", " ", current_translation or "").strip()
    deficit = max(0, len(current) - int(target_chars))
    tier4_block = (
        "TANG 4 — RUT MANH (bo menh de phu):\n"
        "- BAT BUOC bo HAN 1 menh de phu khong mang thong tin cot loi "
        "(tinh tu mo ta, vi du minh hoa phu, lien tu giai thich them).\n"
        "- GIU DUNG: cau hoi / cau tra loi / so lieu / thuat ngu chuyen mon cot loi.\n"
        "- KHONG cat cau hoi dang co cau tra loi lien ke trong context.\n"
        "- Muc tieu: giam thoi luong TTS ro ret, khong chi bo 1-2 trang tu.\n"
        if tier4 else ""
    )
    severe = (
        "LAN CAT MANH (severe overflow / lan rut truoc khong dat):\n"
        "- KHONG chi bo 1 trang tu (vi du 'that su', 'that', 'hoan toan') — TTS se khong giam du.\n"
        "- BAT BUOC cat bot 1 menh de phu (ve phu, ly do, vi du, nhan xet).\n"
        "- Chap nhan mat 1 menh de phu KHONG phai: cau hoi, cau tra loi, so lieu, ten rieng.\n"
        "- Giu dung cap xung ho da khoa.\n"
        if (aggressive or tier4) else ""
    )
    system = (
        f"Ban dich sang {tgt_n} van dai hon khung thoi gian. Rut ngan them ma "
        f"KHONG lam mat mach lac. Goc {src_n} la neo nghia.\n"
        f"JSON.shortened BAT BUOC bang {tgt_n} — giu dung ngon ngu cua current_translation. "
        f"KHONG dich sang Vietnamese hay ngon ngu khac neu target la {tgt_n}.\n"
        f"{tier4_block}{severe}"
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
        f"{_archaic_address_rules(tgt_n)}"
        f"{_proper_noun_rules(tgt_n)}"
        f"{_term_lock_note(terms)}"
        f"{_topic_context_block(topic_context, target=tgt_code)}"
        'Giu DUNG cap xung ho da khoa (locked_pronoun / locked_relation). '
        "Khi rut gon KHONG doi me/con thanh toi/ban; "
        "KHONG doi 'lão phu'/'trẫm'/'bổn tọa'... thanh tu Viet thuong; "
        "KHONG doi ten rieng/doi hinh da khoa thanh tieng Anh.\n"
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
        "locked_terms": terms,
        "aggressive": bool(aggressive or tier4),
        "tier4": bool(tier4),
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
        log.warning("LLM dub JSON: thieu key LLM")
        return None
    try:
        import openai  # noqa: F401
    except ImportError:
        log.warning("LLM dub JSON: chua cai openai")
        return None
    t_llm = time.perf_counter()
    try:
        client = _openai_client()
        global _OPENAI_RETRY_LOGGED
        if not _OPENAI_RETRY_LOGGED:
            _OPENAI_RETRY_LOGGED = True
            log.info(
                "LLM OpenAI SDK max_retries=%s — retry/backoff nam trong TIMING LLM "
                "(khong co asyncio.sleep rieng o translate_llm)",
                getattr(client, "max_retries", "?"),
            )
        r = client.chat.completions.create(
            model=_translate_model(),
            temperature=temperature,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        msg = r.choices[0].message
        raw = (msg.content or "").strip()
        if not raw:
            raw = str(getattr(msg, "reasoning_content", "") or "").strip()
        data = _clean_json_loads(raw)
        if not isinstance(data, dict):
            log.warning("LLM dub JSON: khong phai object (type=%s)", type(data).__name__ if data is not None else "None")
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
    locked_terms: list | None = None,
    topic_context: str = "",
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
        locked_terms=locked_terms,
        topic_context=topic_context,
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
    locked_terms: list | None = None,
    topic_context: str = "",
    speaker_id: str = "",
    aggressive: bool = False,
    ineffective_hint: str = "",
    tier4: bool = False,
    i: int | None = None,
) -> str | None:
    """Lan 1: dich lai vua khung. Lan 2+: reslot. Lan 3/aggressive/tier4: cat menh de phu."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    limit = max(1, int(target_chars))
    if not text:
        return None
    target = normalize_target_lang(target)
    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    force_cut = bool(aggressive) or bool(tier4) or int(attempt) >= 3
    label = f"LLM dub-translate lan {int(attempt)}"
    if tier4:
        label = f"LLM tier4-aggressive lan {int(attempt)}"
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
            locked_terms=locked_terms,
            topic_context=topic_context,
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
            locked_terms=locked_terms,
            topic_context=topic_context,
            speaker_id=speaker_id,
            aggressive=force_cut,
            ineffective_hint=ineffective_hint,
            tier4=bool(tier4) or int(attempt) >= 4,
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
            "risk=%s removed=%s relation=%s pronoun=%s aggressive=%s tier4=%s) | %s",
            attempt, len(raw_out or ""), limit, deficit_sec, risk, removed,
            (data or {}).get("speaker_relation"),
            (data or {}).get("pronoun_pair_used"),
            force_cut,
            bool(tier4) or int(attempt) >= 4,
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
            "display_name": str(
                seg.get("display_name") or prev.get("display_name") or ""
            ).strip(),
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
        dname = str(c.get("display_name") or seg.get("display_name") or "").strip()
        if dname:
            merged["display_name"] = dname
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
    aunt_word = any(k in corpus for k in ("阿姨", "姑姑", "姨妈", "婶婶", "舅妈"))
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
            # Chi khoa co/chau khi co bang chung than toc ZH — slug aunt hay bi gan nham
            if aunt_word or any(k in blob for k in ("阿姨", "姑姑", "姨妈", "婶")):
                rel, pair = "co/di noi voi chau", "co/chau"
            else:
                rel, pair = "ngang vai / ban be", "toi/ban"
        elif family and slug in ("narrator", "voice"):
            rel, pair = "nguoi dan", "toi/ban"
        elif not family:
            rel, pair = "nguoi la / ngang vai", "toi/ban"
        else:
            rel, pair = "ngang vai / ban be", "toi/ban"
        out[sid] = {
            "speaker_id": sid,
            "speaker_relation": rel,
            "pronoun_pair_used": pair,
            "role": roles.get(sid, sid),
        }
    return out


_KINSHIP_ZH = (
    "妈", "爸", "娘", "儿子", "女儿", "老公", "老婆", "丈夫", "妻子",
    "阿姨", "姑姑", "姨妈", "婶婶", "舅舅", "叔叔", "爷爷", "奶奶",
    "外公", "外婆", "哥哥", "姐姐", "弟弟", "妹妹", "嫂子", "姐夫",
)
_PEER_FAMILY_PAIRS = {
    "co/chau", "cô/cháu", "bac/chau", "bác/cháu",
    "di/chau", "dì/cháu", "cau/chau", "cậu/cháu",
}


def _zh_has_kinship(text: str) -> bool:
    t = text or ""
    return any(k in t for k in _KINSHIP_ZH)


def _sanitize_cast_pronouns(
    segments: list[dict],
    cast_by_sid: dict[str, dict],
) -> dict[str, dict]:
    """Ha cap co/chau (va tuong tu) neu transcript KHONG co tu than toc ZH.

    Job do thi hay bi gan aunt + co/chau du goc chi co 你/您/我 ngang vai.
    """
    lines_by: dict[str, list[str]] = {}
    for seg in segments:
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "").strip()
        if not sid:
            continue
        lines_by.setdefault(sid, []).append(
            str(seg.get("source_text") or seg.get("text") or ""),
        )
    corpus = " ".join(" ".join(v) for v in lines_by.values())
    corpus_kin = _zh_has_kinship(corpus)
    out: dict[str, dict] = {}
    fixed = 0
    for sid, c in cast_by_sid.items():
        row = dict(c)
        pair = _norm_vi_token(str(row.get("pronoun_pair_used") or ""))
        if pair in _PEER_FAMILY_PAIRS and not corpus_kin:
            row["pronoun_pair_used"] = "toi/ban"
            row["speaker_relation"] = "ngang vai / ban be (khong phai co-chau)"
            fixed += 1
        out[sid] = row
    if fixed:
        log.info(
            "Cast pronoun sanitize: %d speaker ha cap gia dinh → toi/ban (thieu tu than toc ZH)",
            fixed,
        )
    return out


def lock_cast_relations(
    segments: list[dict],
    *,
    target: str = "vi",
    work_dir: Path | str | None = None,
) -> list[dict]:
    """Khoa 1 LAN cho ca clip: xung ho nhan vat + bang ten rieng/thuat ngu.

    Ghi speaker_relation / pronoun_pair_used / display_name + term_lock,
    roi moi dich/rut.
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
        f"Khoa LONG TIENG 1 LAN cho CA clip (target={target}).\n"
        "Input: moi dong co speaker_id + cau Trung + role neu co.\n"
        "LAM HAI bang ON DINH cho ca clip:\n"
        "1) cast — moi speaker_id: MOT speaker_relation, MOT pronoun_pair_used, "
        "va display_name (ten goi Viet neu co; vd 许爱玲→Hứa Ái Lệ; de trong neu khong ro).\n"
        "   - Cung 1 cau ZH xuat hien 2 vi tri (cung speaker) PHAI cung 1 cap.\n"
        "   - Doc CA transcript de suy quan he. CHI dung me/con, ba/con, co/chau "
        "khi co TU THAN TOC trong ZH (妈/爸/儿子/女儿/阿姨/姑姑...). "
        "Neu chi thay 你/您/我 giua ban be, ban cung phong, dong nghiep, "
        "ngang tuoi → pronoun_pair_used PHAI la toi/ban (hoac anh/em neu ro). "
        "CAM bia co/chau hay bac/chau khi khong co bang chung than toc.\n"
        "   - speaker_id 'aunt' co the SAI (LLM danh nham) — neu loi thoai chi co "
        "你/您 ngang vai: van khoa toi/ban, KHONG khoa co/chau.\n"
        "   - Khong mac dinh toi/ban neu la nguoi nha (co bang chung ZH).\n"
        "2) terms — quet CA transcript, liet ke TEN RIENG / DIA DANH / THUAT NGU "
        "lap lai (thanh pho, dia danh, to chuc, doi hinh quan su, than thoai, "
        "cong nghe/san pham hu cau, ten thuong hieu). "
        "Moi term: source (goc ZH hoac cum goc) + locked_vi (1 ban dich CO DINH) + kind.\n"
        "   Quy tac locked_vi:\n"
        "   - Dia danh/thanh pho: 1 ten Viet on dinh (vd 北京→Bắc Kinh; 蜀山→Thục Sơn).\n"
        "   - Than thoai/dien tich: Han-Viet chuan (白泽→Bạch Trạch, 夸父→Khoa Phụ, "
        "精卫→Tinh Vệ) — CAM Bai Ze/Kwafu/Jingwei.\n"
        "   - Doi hinh/to chuc/ky thuat hu cau: dich HAN sang Viet co nghia "
        "(青龙阵→Đội Hình Thanh Long; 精卫阵→Đội Hình Tinh Vệ; "
        "宽域围困弓/Wide Containment Bow→Cung Vây Rộng) — CAM de tieng Anh.\n"
        "   - Chi liet ke term xuat hien / quan trong; khong spam tu thuong.\n"
        'Tra JSON: {"cast":[{"speaker_id":"boy","speaker_relation":"con noi voi me",'
        '"pronoun_pair_used":"con/me","display_name":"Hứa Ái Lệ"}, ...], '
        '"terms":[{"source":"白泽","locked_vi":"Bạch Trạch","kind":"myth"}, '
        '{"source":"青龙阵","locked_vi":"Đội Hình Thanh Long","kind":"formation"}, ...]} '
        "— cast du moi speaker_id; terms co the rong [] neu khong co."
    )
    data = _llm_json(
        system, {"speakers": speakers, "lines": payload},
        temperature=0.1, kind="lock_cast", n=len(payload),
    )
    cast_by_sid: dict[str, dict] = {}
    terms: list[dict] = []
    if isinstance(data, dict):
        if isinstance(data.get("cast"), list):
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
                    "display_name": str(c.get("display_name") or "").strip(),
                }
        terms = _parse_term_lock(data.get("terms") or data.get("glossary") or data.get("locked_terms"))
    if not cast_by_sid or not any(
        c.get("pronoun_pair_used") or c.get("speaker_relation") for c in cast_by_sid.values()
    ):
        log.warning("Khoa xung ho LLM trong/that bai — fallback heuristic tu transcript")
        cast_by_sid = _heuristic_cast_lock(segments)
    cast_by_sid = _sanitize_cast_pronouns(segments, cast_by_sid)
    out = apply_cast_lock(segments, cast_by_sid)
    if terms:
        out = apply_term_lock(out, terms)
    filled = sum(1 for s in out if s.get("pronoun_pair_used") or s.get("speaker_relation"))
    log.info(
        "Khoa xung ho: %d speaker, %d/%d doan co relation/pronoun | %s",
        len(cast_by_sid), filled, len(out),
        ", ".join(
            f"{c.get('speaker_id')}={c.get('pronoun_pair_used') or '-'}"
            for c in cast_by_sid.values()
        ),
    )
    if terms:
        log.info(
            "Khoa term: %d ten | %s",
            len(terms),
            "; ".join(f"{t['source']}→{t['locked_vi']}" for t in terms[:12]),
        )
    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "cast_lock.json").write_text(
            json.dumps(
                {"cast": list(cast_by_sid.values()), "terms": terms},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (wp / "term_lock.json").write_text(
            json.dumps({"terms": terms}, ensure_ascii=False, indent=2),
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
                    prev["locked_terms"] = terms
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
    require_llm: bool = False,
    topic_context: str = "",
) -> list[dict]:
    """Dich zh -> vi voi ngan sach ky tu theo slot (proactive, truoc TTS).

    require_llm=True: user chon OpenAI — loi API/parse thi FAIL, khong am tham Google.
    topic_context: van phong the loai (genre) — chen truoc phan format JSON.
    """
    from .job_timing import where

    if not segments:
        return []

    with where("translate_budgeted"):
        return _translate_segments_budgeted_inner(
            segments, video_dur=video_dur, target=target,
            chars_per_sec=chars_per_sec, work_dir=work_dir,
            require_llm=require_llm, topic_context=topic_context,
        )


def _translate_segments_budgeted_inner(
    segments: list[dict],
    *,
    video_dur: float,
    target: str = "vi",
    chars_per_sec: float | None = None,
    work_dir: Path | str | None = None,
    require_llm: bool = False,
    topic_context: str = "",
) -> list[dict]:
    from .job_timing import add_bucket, phase

    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    is_vi = (target or "vi").lower() in ("vietnamese", "vi")
    lang = "Vietnamese" if is_vi else _LANG.get((target or "vi").lower(), target or "Vietnamese")
    lang_name = "Tiếng Việt (Vietnamese)" if is_vi else lang
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
            "display_name": str(seg.get("display_name") or ""),
        })
        budgets.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "slot_sec": round(slot, 3),
            "target_chars": max_chars,
            "source_text": src,
        })

    from .preflight import allow_degraded, alert_ops

    key = _load_key()
    if not key:
        msg = (
            "Khong co key OpenAI/DeepSeek — dich Google (chat luong kem). "
            "Dan key trong app de dich LLM."
        )
        log.warning(msg)
        if require_llm:
            raise RuntimeError(_openai_fail_message(empty=True))
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    try:
        import openai  # noqa: F401
    except ImportError:
        msg = "Chua cai openai — fallback Google budgeted"
        if require_llm:
            raise RuntimeError(_openai_fail_message(RuntimeError("thieu thu vien openai"))) from None
        if allow_degraded():
            alert_ops("draft_mode", msg)
        else:
            alert_ops("translate_fallback", msg)
        return _translate_budgeted_fallback(segments, video_dur=video_dur, target=target, cps=cps)

    pre_lock = locked_cast_payload(segments)
    # Bo sung display_name tu segment neu locked_cast_payload chua co
    for c in pre_lock:
        sid = str(c.get("speaker_id") or "")
        if c.get("display_name"):
            continue
        for seg in segments:
            if str(seg.get("speaker_id") or seg.get("voice_id") or "") == sid:
                dn = str(seg.get("display_name") or "").strip()
                if dn:
                    c["display_name"] = dn
                    break
    pre_terms = load_term_lock(segments, work_dir)
    lock_note = ""
    if any(c.get("pronoun_pair_used") or c.get("speaker_relation") for c in pre_lock):
        lock_note = (
            "LOCKED CAST da khoa 1 lan cho ca clip — BAT BUOC dung dung cap xung ho "
            "va display_name (neu co). KHONG doan lai theo tung dong. "
            "Cung speaker_id / cung cau ZH phai cung pronoun_pair_used.\n"
            + json.dumps(pre_lock, ensure_ascii=False)
            + "\n"
        )
    lock_note += _term_lock_note(pre_terms)
    genre_note = _topic_context_block(topic_context, target=target)

    system = (
        f"{_drama_dub_rules(lang)}"
        f"{_pronoun_rules('vi' if (target or 'vi').lower() == 'vi' else target)}"
        f"{lock_note}"
        f"{genre_note}"
        "LAM BUOC 0 CHO CA CLIP truoc khi dich: dung locked_cast / locked_terms neu co; "
        "neu chua khoa thi lap cast (speaker_id + "
        "speaker_relation + pronoun_pair_used + display_name) va bang terms "
        "(source→locked_vi), roi dich moi dong dung dung cap/ten do.\n"
        "Input JSON: {\"locked_cast\":[...],\"locked_terms\":[...],\"lines\":[...]} "
        "moi line: source, slot_sec, max_chars, prev_context, next_context, speaker_id, "
        "locked_relation, locked_pronoun, display_name.\n"
        "Quy tac them:\n"
        f"- BAT BUOC 100% dung {lang_name}. Tuyet doi khong dung tieng Anh neu target la Tieng Viet.\n"
        "- Muc tieu <= max_chars — nhung NEU phai chon: "
        "giu dung vai/xung ho + nghia + ten rieng da khoa > ep dung do dai.\n"
        "- Dich TU source Trung; draft Google neu co chi la goi y — neu draft vo nghia thi bo.\n"
        "- Van noi Trung luoc chu ngu/lien tu — dung them tru khi toi nghia.\n"
        "- Giu mach hoi-dap/nhan-qua voi prev_context/next_context.\n"
        "- Ten rieng: dung locked_terms / display_name / Han-Viet — CAM pinyin thô "
        "va CAM de Formation/Bow/Bai Ze/Kwafu bang tieng Anh.\n"
        "- Vuot max_chars: chon tu ngan, bo trang tu, gop y trung — giu so lieu/"
        "cau hoi/cau tra loi / ten rieng / cap xung ho da khoa.\n"
        "- Return JSON: {\"cast\":[{\"speaker_id\":\"...\",\"speaker_relation\":\"...\","
        "\"pronoun_pair_used\":\"...\",\"display_name\":\"...\"}],"
        "\"lines\":[{\"i\":1,\"text\":\"...\",\"speaker_relation\":\"...\","
        "\"pronoun_pair_used\":\"...\"}, ...]} same line count."
    )

    client = _openai_client()

    # Batch size 20 (DeepSeek/Qwen) de JSON on dinh, khong bi nuot dong hoac tran token
    try:
        batch_size = max(5, int(os.getenv("DYFLOW_TRANSLATE_BATCH") or "20"))
    except ValueError:
        batch_size = 20

    def _translate_subchunk_resilient(sub_payload: list[dict], batch_label: str) -> tuple[list[dict], list[dict]]:
        """Dich sub-chunk voi divide-and-conquer chong loi JSON."""
        sub_n = len(sub_payload)
        t_sub = time.perf_counter()

        # Lan 1: temp 0.2
        try:
            r = client.chat.completions.create(
                model=_translate_model(),
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": system
                        + f"\nBAT BUOC: Tra ve JSON co key 'lines' dung {sub_n} phan tu [\"i\": 1, \"text\": \"...\"]. Khong bo sot.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "locked_cast": pre_lock,
                                "locked_terms": pre_terms,
                                "lines": sub_payload,
                                "batch": batch_label,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            msg = r.choices[0].message
            raw = (msg.content or "").strip()
            if not raw:
                raw = str(getattr(msg, "reasoning_content", "") or "").strip()
            parsed = _parse_line_rows(raw, sub_n)
            if parsed and len(parsed[0]) == sub_n and all(item.get("text") for item in parsed[0]):
                add_llm("translate", time.perf_counter() - t_sub, n=sub_n)
                return parsed
        except Exception as e:
            log.warning("LLM dich subchunk (%d doan) lan 1 loi: %s", sub_n, e)

        # Lan 2: retry temp 0.0
        try:
            r2 = client.chat.completions.create(
                model=_translate_model(),
                temperature=0.0,
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": system
                        + f"\nCHI TRA JSON hop le: {{\"lines\": [{{\"i\": 1, \"text\": \"...\"}}]}} dung {sub_n} dong.\n",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "locked_cast": pre_lock,
                                "locked_terms": pre_terms,
                                "lines": sub_payload,
                                "batch": batch_label,
                                "retry": True,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            msg2 = r2.choices[0].message
            raw2 = (msg2.content or "").strip()
            if not raw2:
                raw2 = str(getattr(msg2, "reasoning_content", "") or "").strip()
            parsed2 = _parse_line_rows(raw2, sub_n)
            if parsed2 and len(parsed2[0]) == sub_n and all(item.get("text") for item in parsed2[0]):
                add_llm("translate", time.perf_counter() - t_sub, n=sub_n)
                return parsed2
        except Exception as e:
            log.warning("LLM dich subchunk (%d doan) retry loi: %s", sub_n, e)

        # Lan 3: Divide and Conquer — chia doi chunk neu sub_n > 1
        if sub_n > 1:
            mid = sub_n // 2
            log.info("LLM divide-and-conquer: chia %d doan -> %d + %d tranh loi JSON", sub_n, mid, sub_n - mid)
            p1 = [dict(p, i=j + 1) for j, p in enumerate(sub_payload[:mid])]
            p2 = [dict(p, i=j + 1) for j, p in enumerate(sub_payload[mid:])]
            r1, c1 = _translate_subchunk_resilient(p1, batch_label)
            r2, c2 = _translate_subchunk_resilient(p2, batch_label)
            add_llm("translate", time.perf_counter() - t_sub, n=sub_n)
            return (r1 + r2, c1 + c2)

        # Fallback 1 cau cuoi cung: dich truc tiep khong can format JSON
        single_item = sub_payload[0]
        src_text = str(single_item.get("source") or single_item.get("text") or single_item.get("source_text") or "")
        log.info("LLM dich 1 cau truc tiep (khong JSON): %s", src_text[:60])
        try:
            r_single = client.chat.completions.create(
                model=_translate_model(),
                temperature=0.1,
                max_tokens=600,
                messages=[
                    {
                        "role": "system",
                        "content": f"Ban la bien dich vien long tieng chuyen nghiep sang {lang_name}. "
                        f"Chi tra ve duy nhat ban dich {lang_name} cua cau sau, khong giai thich.",
                    },
                    {"role": "user", "content": src_text},
                ],
            )
            stext = (r_single.choices[0].message.content or "").strip()
            stext = re.sub(r"^[\"']|[\"']$", "", stext).strip()
            if stext:
                add_llm("translate", time.perf_counter() - t_sub, n=1)
                return ([{"text": stext}], [])
        except Exception as e:
            log.warning("Dich 1 cau truc tiep loi: %s", e)

        add_llm("translate", time.perf_counter() - t_sub, n=1)
        return ([{"text": src_text}], [])

    rows: list[dict] = []
    cast: list[dict] = []
    n_total = len(segments)
    n_batches = (n_total + batch_size - 1) // batch_size
    if n_batches > 1:
        log.info(
            "Dich LLM chia %d doan thanh %d batch (size=%d)",
            n_total, n_batches, batch_size,
        )

    for b_idx, start in enumerate(range(0, n_total, batch_size)):
        end = min(start + batch_size, n_total)
        chunk_payload = []
        for j, p in enumerate(payload[start:end]):
            q = dict(p)
            q["i"] = j + 1
            chunk_payload.append(q)
        chunk_rows, chunk_cast = _translate_subchunk_resilient(
            chunk_payload, f"{b_idx + 1}/{n_batches}"
        )
        rows.extend(chunk_rows)
        cast.extend(chunk_cast)

    # Dam bao du so dong voi n_total — khong bao gio de crash ca video
    if len(rows) < n_total:
        log.warning("Can bang so dong dich thieu: %d -> %d", len(rows), n_total)
        for k in range(len(rows), n_total):
            missing_src = str(payload[k].get("source") or payload[k].get("text") or payload[k].get("source_text") or "")
            rows.append({"text": missing_src})
    elif len(rows) > n_total:
        rows = rows[:n_total]

    cast_by_sid = {
        str(c.get("speaker_id") or ""): c
        for c in cast
        if c.get("speaker_id")
    }
    for c in pre_lock:
        sid = str(c.get("speaker_id") or "")
        if not sid:
            continue
        if c.get("speaker_relation") or c.get("pronoun_pair_used") or c.get("display_name"):
            cast_by_sid[sid] = {
                "speaker_id": sid,
                "speaker_relation": c.get("speaker_relation") or (cast_by_sid.get(sid) or {}).get("speaker_relation") or "",
                "pronoun_pair_used": c.get("pronoun_pair_used") or (cast_by_sid.get(sid) or {}).get("pronoun_pair_used") or "",
                "display_name": c.get("display_name") or (cast_by_sid.get(sid) or {}).get("display_name") or "",
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
        dname = str(
            seg.get("display_name")
            or (cast_by_sid.get(sid) or {}).get("display_name")
            or ""
        ).strip()
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
        if sid and sid not in cast_by_sid and (rel or pair or dname):
            cast_by_sid[sid] = {
                "speaker_id": sid,
                "speaker_relation": rel,
                "pronoun_pair_used": pair,
                "display_name": dname,
            }
        # Chi rut khi vuot nhieu — tha dai nhe hon sai vai / vo nghia
        overflow = len(text) - int(bud["target_chars"])
        need_short = overflow > max(8, int(bud["target_chars"] * 0.12))
        if need_short:
            shorter = shorten_for_slot(
                text,
                slot_sec=bud["slot_sec"],
                source_text=src,
                target=target,
                chars_per_sec=cps,
                max_chars=bud["target_chars"],
                prev_context=str((payload[bud["i"]] or {}).get("prev_context") or ""),
                next_context=str((payload[bud["i"]] or {}).get("next_context") or ""),
                locked_relation=rel,
                locked_pronoun=pair,
                locked_cast=list(cast_by_sid.values()),
                locked_terms=pre_terms,
                topic_context=topic_context,
                speaker_id=sid,
                i=bud["i"],
            )
            if (
                shorter
                and _pronoun_lock_ok(shorter, pair)
                and not _looks_pinyin_name(shorter)
                and not _archaic_literal_bad(src, shorter)
                and not _english_term_leak(shorter)
            ):
                text = shorter
            elif shorter:
                log.info(
                    "Giu ban dai #%d (rut gon pha vai/ten/co trang) %d chars",
                    bud["i"], len(text),
                )
        out.append({
            **seg,
            "text": text or seg.get("text", ""),
            "source_text": src,
            "target_chars": bud["target_chars"],
            "slot_sec": bud["slot_sec"],
            "speaker_relation": rel,
            "pronoun_pair_used": pair,
            **({"display_name": dname} if dname else {}),
            **({"term_lock": pre_terms} if pre_terms else {}),
        })
        log.info(
            "Dich budget #%d: slot %.1fs max %d -> %d chars relation=%s pronoun=%s | %s",
            bud["i"], bud["slot_sec"], bud["target_chars"], len(text), rel, pair, text[:60],
        )

    # Sua lai doan sai vai / pinyin / tu xung co trang / ten rieng tieng Anh
    repaired = 0
    for i, seg in enumerate(out):
        text = str(seg.get("text") or "")
        src = str(seg.get("source_text") or "")
        pair = str(seg.get("pronoun_pair_used") or "")
        bad = (
            (not _pronoun_lock_ok(text, pair))
            or _looks_pinyin_name(text)
            or _archaic_literal_bad(src, text)
            or _english_term_leak(text)
        )
        if not bad:
            continue
        term_hint = ""
        if pre_terms:
            term_hint = (
                "LOCKED TERMS: "
                + "; ".join(f"{t['source']}→{t['locked_vi']}" for t in pre_terms[:20])
                + ". "
            )
        emphasis = (
            f"BAT BUOC dung cap xung ho '{pair}' "
            f"({seg.get('speaker_relation') or ''}). "
            "Ten rieng phien am Viet / Han-Viet, CAM pinyin thô. "
            "CAM de Formation/Bow/Palace/Bai Ze/Kwafu/Jingwei bang tieng Anh — "
            "dich HAN sang Viet (vd Đội Hình Thanh Long, Cung Vây Rộng, Bạch Trạch, Khoa Phụ). "
            f"{term_hint}"
            "Neu co trang/tien hiep: GIU Han-Viet (lao phu/tram/bon toa...), "
            "CAM dich 'ong gia' cho 老夫. "
            "Uu tien dung vai + dung nghia + ten da khoa hon ep do dai."
        )
        fixed = retranslate_segment_faithful(
            seg,
            segment_index=i,
            segments=out,
            video_dur=video_dur,
            target=target,
            chars_per_sec=cps,
            emphasis=emphasis,
            topic_context="\n".join(
                x for x in (
                    (topic_context or "").strip(),
                    _term_lock_note(pre_terms).strip(),
                ) if x
            ),
        )
        if (
            fixed
            and _pronoun_lock_ok(fixed, pair)
            and not _archaic_literal_bad(src, fixed)
            and not _english_term_leak(fixed)
        ):
            out[i] = {**seg, "text": fixed}
            repaired += 1
            log.info("Sua vai/ten/co trang #%d: %s", i, fixed[:80])
    if repaired:
        log.info("Da sua %d doan sai vai/ten/co trang sau dich budget", repaired)

    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "translate_budget.json").write_text(
            json.dumps(
                {
                    "chars_per_sec": cps,
                    "cast": list(cast_by_sid.values()),
                    "terms": pre_terms,
                    "segments": budgets,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    out = apply_cast_lock(out, cast_by_sid)
    if pre_terms:
        out = apply_term_lock(out, pre_terms)
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
    from .google_polish import polish_segments_google

    vi = polish_segments_google(vi, target=target)
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
    topic_context: str = "",
) -> str | None:
    """Dich lai 1 doan — uu tien dung nghia source, van trong budget ky tu.

    topic_context: ghi chu chu de/thuat ngu theo job (vd. glossary y te). Mac dinh rong.
    """
    key = _load_key()
    src = str(seg.get("source_text") or seg.get("text") or "").strip()
    if not key or not src:
        return None
    try:
        import openai  # noqa: F401
    except ImportError:
        return None

    slot = float(seg.get("slot_sec") or segment_slot(segments, segment_index, video_dur))
    max_chars = int(seg.get("target_chars") or target_chars_for_slot(slot, chars_per_sec=chars_per_sec))
    is_vi = (target or "vi").lower() in ("vietnamese", "vi")
    lang = "Vietnamese" if is_vi else _LANG.get((target or "vi").lower(), target or "Vietnamese")
    lang_name = "Tiếng Việt (Vietnamese)" if is_vi else lang
    topic_block = _topic_context_block(topic_context, target=target)
    extra = (emphasis or "").strip()
    extra_line = f"{extra}\n" if extra else ""
    system = (
        f"{_drama_dub_rules(lang)}"
        f"{topic_block}"
        f"Dich MOT cau thoai Trung sang {lang_name} (long tieng).\n"
        f"BAT BUOC: 100% ban dich phai la {lang_name}. Tuyet doi khong dung ngon ngu khac.\n"
        f"Gioi han: toi da {max_chars} ky tu (tinh ca khoang trang). Slot ~{slot:.1f}s.\n"
        "Uu tien dung nghia source; khong gia dinh chu de neu khong duoc cung cap.\n"
        f"{extra_line}"
        'Return JSON: {"text":"..."}'
    )
    user = {"source": src, "max_chars": max_chars, "slot_sec": round(slot, 2)}
    client = _openai_client()
    try:
        r = client.chat.completions.create(
            model=_translate_model(),
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


def review_segments_content(
    segments: list[dict],
    *,
    topic_context: str = "",
    target: str = "vi",
) -> list[dict]:
    """LLM reviewer: so sanh zh goc vs ban dich, cham khop/gan_dung/sai.

    topic_context: chu de/glossary theo job (tuy chon). Mac dinh tong quat — khong gia dinh chu de.
    """
    key = _load_key()
    if not key:
        log.warning("Thieu key LLM — bo qua review noi dung")
        return []

    try:
        import openai  # noqa: F401
    except ImportError:
        return []

    payload = []
    for i, seg in enumerate(segments):
        payload.append({
            "i": i,
            "start": round(float(seg.get("start", 0)), 2),
            "end": round(float(seg.get("end", 0)), 2),
            "source_zh": str(seg.get("source_text") or ""),
            "translation": str(seg.get("text") or ""),
            "source_suspect": bool(seg.get("source_suspect")),
        })

    review_model = os.getenv("LLM_REVIEW_MODEL") or os.getenv("OPENAI_REVIEW_MODEL") or _translate_model()
    from .genre_profiles import sanitize_genre_prompt

    topic = sanitize_genre_prompt(topic_context or "")
    topic_block = (
        f"Genre / style notes for this job (register, pronouns, jargon — NOT output format):\n"
        f"{topic}\n"
        "Judge meaning in that genre. Do NOT mark classical self-address "
        "(e.g. lão phu, bổn tọa, trẫm) as wrong when source is historical/xianxia.\n"
        if topic
        else "Do NOT assume any prior topic; judge only from source_zh vs translation.\n"
    )
    is_vi = (target or "vi").lower() in ("vietnamese", "vi")
    tgt_name = "tiếng Việt (Vietnamese)" if is_vi else _LANG.get((target or "vi").lower(), target or "Vietnamese")
    system = (
        f"You are a bilingual dubbing QA reviewer (Chinese source -> {tgt_name} dub).\n"
        "For EACH segment, compare source_zh and translation by meaning only.\n"
        f"{topic_block}"
        "Score meaning match:\n"
        "- khop: same core meaning, acceptable for dubbing\n"
        "- gan_dung: mostly OK but missing detail, wrong nuance, vague wording, "
        "OR disputed Han-Viet name spelling / pronoun gender when ASR is ambiguous\n"
        "- sai: ONLY invented content not in source, wrong topic, contradicts source, "
        "OR (Vietnamese target) leaves English/pinyin proper nouns that must be "
        "Vietnamese/Han-Viet (Bai Ze, Kwafu, Azure Dragon Formation, Wide Containment Bow)\n"
        "Do NOT mark sai for:\n"
        "- Alternate Han-Viet readings of the same Chinese name "
        "(e.g. 宝鸭 → Bảo Áp / Bảo Ngọc — prefer gan_dung)\n"
        "- 他/她/它 gender when Whisper may be wrong — prefer gan_dung\n"
        "- Locked cast pronouns (cô/cháu, tôi/bạn…) that differ from a literal gloss "
        "but still match dialogue meaning\n"
        "If source_suspect is true, be STRICTER only on invention/padding, "
        "not on name romanization.\n"
        "Return JSON: {\"reviews\":[{\"i\":0,\"score\":\"khop|gan_dung|sai\","
        "\"reason\":\"...\",\"suggested_fix\":\"\"}, ...]} — same count as input."
    )

    client = _openai_client()
    try:
        r = client.chat.completions.create(
            model=review_model,
            temperature=0.0,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw = _clean_json_loads((r.choices[0].message.content or "").strip())
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


def resolve_translate_engine(requested: str | None = None) -> str:
    """Mac dinh DeepSeek/OpenAI/Qwen khi co key; Google khi khong (hoac user chon google)."""
    te = str(requested or "").strip().lower()
    if te in ("google", "openai", "deepseek", "qwen"):
        return te
    from .llm_provider import has_llm_key

    if has_llm_key("deepseek"):
        return "deepseek"
    if has_llm_key("openai"):
        return "openai"
    if has_llm_key("qwen"):
        return "qwen"
    return "google"


def flag_suspicious_sources(segments: list[dict]) -> list[dict]:
    """Danh dau doan Whisper nghi rac (slot dai ma transcript qua thua/dac) TRUOC dich."""
    from .dub_constants import (
        SOURCE_SUSPECT_MAX_CPS,
        SOURCE_SUSPECT_MIN_CPS,
        SOURCE_SUSPECT_MIN_SLOT_SEC,
    )

    out: list[dict] = []
    n_flag = 0
    for seg in segments:
        row = dict(seg)
        src = str(row.get("source_text") or row.get("text") or "")
        src_compact = re.sub(r"\s+", "", src)
        # Chi dem chu Han / chu cai — bo dau cau
        src_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", src_compact))
        slot = max(
            0.05,
            float(row.get("end", 0) or 0) - float(row.get("start", 0) or 0),
        )
        cps = src_chars / slot
        reasons: list[str] = []
        if slot >= SOURCE_SUSPECT_MIN_SLOT_SEC and cps < SOURCE_SUSPECT_MIN_CPS:
            reasons.append(
                f"transcript qua thua ({src_chars} ky tu / {slot:.1f}s = {cps:.2f} cps)"
            )
        if slot >= SOURCE_SUSPECT_MIN_SLOT_SEC and cps > SOURCE_SUSPECT_MAX_CPS:
            reasons.append(
                f"transcript qua dac ({src_chars} ky tu / {slot:.1f}s = {cps:.2f} cps)"
            )
        if reasons:
            row["source_suspect"] = True
            row["source_suspect_reason"] = "; ".join(reasons)
            n_flag += 1
        out.append(row)
    if n_flag:
        log.warning(
            "Nguon Whisper nghi ngo: %d/%d doan — review ky hon sau dich",
            n_flag, len(out),
        )
    return out


def enforce_content_qa_gate(
    segments: list[dict],
    *,
    video_dur: float,
    target: str = "vi",
    chars_per_sec: float | None = None,
    work_dir: Path | str | None = None,
    fail_closed: bool | None = None,
    topic_context: str = "",
) -> list[dict]:
    """QA noi dung BAT BUOC sau dich, truoc TTS.

    - sai → dich lai kem reason; review lai; van sai → content_mismatch_unresolved
      (+ fail-closed neu bat).
    - gan_dung → canh bao, khong chan giao.
    - source_suspect → them 1 lan retry.
    """
    import time as _time

    from .dub_constants import (
        CONTENT_QA_FAIL_CLOSED,
        CONTENT_QA_RETRY_MAX,
        CONTENT_QA_SUSPECT_EXTRA_RETRY,
        TARGET_CHARS_PER_SEC,
    )
    from .dub_report import write_content_review_full
    from .preflight import allow_degraded
    from .llm_provider import has_llm_key, preferred_chat_provider, set_llm_provider

    if not segments:
        return segments

    # Mac dinh KHONG chan giao clip (100% job di tiep). Bat chat: DYFLOW_CONTENT_QA_STRICT=1
    strict_env = os.getenv("DYFLOW_CONTENT_QA_STRICT", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if fail_closed is None:
        fail_closed = bool(CONTENT_QA_FAIL_CLOSED) and strict_env
    if not strict_env:
        fail_closed = False

    desktop = False
    try:
        from .desktop_mode import enabled as desktop_on

        desktop = bool(desktop_on())
    except Exception:  # noqa: BLE001
        desktop = False

    cps = float(chars_per_sec or TARGET_CHARS_PER_SEC)
    out = [dict(s) for s in segments]
    # Dam bao da danh dau suspect (an toan neu goi lai)
    out = flag_suspicious_sources(out)

    # QA dung provider chat dang duoc chon (DeepSeek / Qwen / OpenAI)
    from .llm_provider import current_llm_provider

    qa_provider = current_llm_provider()
    if not (qa_provider in ("deepseek", "qwen", "openai") and has_llm_key(qa_provider)):
        qa_provider = preferred_chat_provider()
    if not has_llm_key(qa_provider):
        msg = (
            "Thieu key DeepSeek/OpenAI/Qwen — khong chay content QA. "
            "Them key de chan ban dich bia/sai truoc TTS."
        )
        if fail_closed and not allow_degraded():
            raise RuntimeError(msg)
        log.warning(msg)
        return out

    set_llm_provider(qa_provider)
    t0 = _time.perf_counter()
    reviews = review_segments_content(out, topic_context=topic_context, target=target)
    if not reviews:
        msg = "Content QA that bai (API/JSON) — khong giao clip chua xac minh nghia."
        if fail_closed:
            raise RuntimeError(msg)
        log.warning(msg)
        return out

    # Bo sung: ten rieng de tieng Anh hoac bi dich nham sang tieng Anh = sai (deterministic)
    is_target_vi = (target or "vi").lower() in ("vietnamese", "vi")
    for rev in reviews:
        i = int(rev.get("i") or 0)
        if not (0 <= i < len(out)):
            continue
        text = str(out[i].get("text") or "")
        if rev.get("score") != "sai":
            if is_target_vi:
                if _english_term_leak(text):
                    rev["score"] = "sai"
                    prev = str(rev.get("reason") or "").strip()
                    leak_note = "Ban dich con ten rieng/doi hinh tieng Anh (Formation/Bai Ze/Kwafu...)"
                    rev["reason"] = f"{leak_note}. {prev}".strip() if prev else leak_note
                elif _is_unwanted_english(text, is_target_vi):
                    rev["score"] = "sai"
                    prev = str(rev.get("reason") or "").strip()
                    leak_note = "Ban dich bi nham sang tieng Anh thay vi tieng Viet (BAT BUOC 100% tieng Viet)"
                    rev["reason"] = f"{leak_note}. {prev}".strip() if prev else leak_note
            else:
                mismatch = lang_mismatch_reason(text, target)
                if mismatch:
                    rev["score"] = "sai"
                    prev = str(rev.get("reason") or "").strip()
                    leak_note = f"Ban dich bi nham ngon ngu ({mismatch}), BAT BUOC phai la {target}"
                    rev["reason"] = f"{leak_note}. {prev}".strip() if prev else leak_note

    retried: list[dict] = []
    unresolved: list[dict] = []
    gan_dung_flags: list[dict] = []

    for rev in reviews:
        score = str(rev.get("score") or "")
        i = int(rev.get("i") or 0)
        if not (0 <= i < len(out)):
            continue
        if score == "gan_dung":
            flag = {
                "i": i,
                "score": "gan_dung",
                "reason": rev.get("reason") or "",
                "source_text": rev.get("source_text") or "",
                "translated_text": rev.get("translated_text") or "",
            }
            gan_dung_flags.append(flag)
            flags = list(out[i].get("content_flags") or [])
            if "content_gan_dung" not in flags:
                flags.append("content_gan_dung")
            out[i]["content_flags"] = flags
            out[i]["content_review_score"] = "gan_dung"
            out[i]["content_review_reason"] = str(rev.get("reason") or "")
            continue
        if score != "sai":
            out[i]["content_review_score"] = score
            continue

        reason = str(rev.get("reason") or "").strip()
        suspect = bool(out[i].get("source_suspect"))
        max_tries = CONTENT_QA_RETRY_MAX + (
            CONTENT_QA_SUSPECT_EXTRA_RETRY if suspect else 0
        )
        max_tries = max(1, int(max_tries))
        fixed_ok = False
        last_reason = reason
        for attempt in range(1, max_tries + 1):
            emphasis = (
                f"CONTENT QA cham SAI (lan {attempt}/{max_tries}). "
                f"Ly do reviewer: {last_reason or 'ban dich khong khop / bia noi dung'}. "
                "Dich LAI dung dung source_zh — CAM bia them y khong co trong cau goc. "
                "Uu tien dung nghia hon ep do dai."
            )
            if suspect:
                emphasis += (
                    " Nguon Whisper nghi ngo — dich that than, "
                    "KHONG bia them de lap cho trong."
                )
            fixed = retranslate_segment_faithful(
                out[i],
                segment_index=i,
                segments=out,
                video_dur=video_dur,
                target=target,
                chars_per_sec=cps,
                emphasis=emphasis,
                topic_context=topic_context or _term_lock_note(
                    load_term_lock(out, work_dir)
                ).strip(),
            )
            if not fixed:
                log.warning("Content QA #%d: dich lai lan %d that bai", i, attempt)
                continue
            out[i] = {**out[i], "text": fixed}
            # Review lai dung 1 doan (+ deterministic EN leak)
            recheck = review_segments_content(
                [out[i]],
                topic_context=topic_context,
                target=target,
            )
            new_score = (recheck[0].get("score") if recheck else "") or "sai"
            last_reason = (recheck[0].get("reason") if recheck else "") or last_reason
            if is_target_vi:
                if _english_term_leak(fixed):
                    new_score = "sai"
                    last_reason = (
                        "Van con ten rieng/doi hinh tieng Anh sau khi dich lai. "
                        + (last_reason or "")
                    ).strip()
                elif _is_unwanted_english(fixed, is_target_vi):
                    new_score = "sai"
                    last_reason = (
                        "Van con cau tieng Anh sau khi dich lai (BAT BUOC 100% tieng Viet). "
                        + (last_reason or "")
                    ).strip()
            else:
                mismatch = lang_mismatch_reason(fixed, target)
                if mismatch:
                    new_score = "sai"
                    last_reason = (
                        f"Van chua dung ngon ngu target {target} ({mismatch}). "
                        + (last_reason or "")
                    ).strip()
            retried.append({
                "i": i,
                "attempt": attempt,
                "score_before": "sai",
                "score_after": new_score,
                "reason_before": reason,
                "reason_after": last_reason,
                "source_suspect": suspect,
                "text": fixed,
            })
            if new_score != "sai":
                out[i]["content_review_score"] = new_score
                out[i]["content_review_reason"] = last_reason
                flags = list(out[i].get("content_flags") or [])
                if "content_retried" not in flags:
                    flags.append("content_retried")
                if new_score == "gan_dung":
                    if "content_gan_dung" not in flags:
                        flags.append("content_gan_dung")
                    gan_dung_flags.append({
                        "i": i,
                        "score": "gan_dung",
                        "reason": last_reason,
                        "after_retry": True,
                    })
                out[i]["content_flags"] = flags
                fixed_ok = True
                log.info(
                    "Content QA #%d: sai → %s sau lan %d | %s",
                    i, new_score, attempt, fixed[:60],
                )
                break
            log.warning(
                "Content QA #%d: van sai sau lan %d — %s",
                i, attempt, (last_reason or "")[:80],
            )

        if not fixed_ok:
            flags = list(out[i].get("content_flags") or [])
            if "content_mismatch_unresolved" not in flags:
                flags.append("content_mismatch_unresolved")
            out[i]["content_flags"] = flags
            out[i]["content_review_score"] = "sai"
            out[i]["content_review_reason"] = last_reason
            unresolved.append({
                "i": i,
                "reason": last_reason,
                "source_text": out[i].get("source_text") or "",
                "translated_text": out[i].get("text") or "",
                "source_suspect": suspect,
            })

    elapsed = _time.perf_counter() - t0
    # Cap nhat bang review: ghi de score doan da retry / unresolved
    by_i = {int(r.get("i") or 0): dict(r) for r in reviews}
    for item in retried:
        i = int(item["i"])
        if i in by_i:
            by_i[i]["score"] = item.get("score_after") or by_i[i].get("score")
            by_i[i]["reason"] = item.get("reason_after") or by_i[i].get("reason")
            by_i[i]["translated_text"] = item.get("text") or by_i[i].get("translated_text")
            by_i[i]["retried"] = True
    for u in unresolved:
        i = int(u["i"])
        if i in by_i:
            by_i[i]["score"] = "sai"
            by_i[i]["reason"] = u.get("reason") or by_i[i].get("reason")
            by_i[i]["content_mismatch_unresolved"] = True
            by_i[i]["translated_text"] = u.get("translated_text") or by_i[i].get("translated_text")
    final_reviews = [by_i[i] for i in sorted(by_i.keys())]

    summary = {
        "khop": sum(1 for r in final_reviews if r.get("score") == "khop"),
        "gan_dung": sum(1 for r in final_reviews if r.get("score") == "gan_dung"),
        "sai": sum(1 for r in final_reviews if r.get("score") == "sai"),
        "retried": len(retried),
        "unresolved": len(unresolved),
        "gan_dung_warn": len(gan_dung_flags),
        "source_suspect": sum(1 for s in out if s.get("source_suspect")),
        "elapsed_sec": round(elapsed, 2),
    }
    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        write_content_review_full(wp, final_reviews)
        (wp / "content_qa_gate.json").write_text(
            json.dumps(
                {
                    "summary": summary,
                    "retried": retried,
                    "unresolved": unresolved,
                    "gan_dung_flags": gan_dung_flags,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    log.info(
        "Content QA gate: khop=%d gan_dung=%d sai=%d retried=%d unresolved=%d "
        "suspect=%d (%.1fs)",
        summary["khop"], summary["gan_dung"], summary["sai"],
        summary["retried"], summary["unresolved"],
        summary["source_suspect"], elapsed,
    )

    if unresolved and fail_closed:
        from .dub_constants import (
            CONTENT_QA_DESKTOP_SOFT_MAX_ABS,
            CONTENT_QA_DESKTOP_SOFT_MAX_RATIO,
        )

        soft_ok = False
        if desktop and not strict_env:
            lim = max(
                int(CONTENT_QA_DESKTOP_SOFT_MAX_ABS),
                int(len(out) * float(CONTENT_QA_DESKTOP_SOFT_MAX_RATIO) + 0.999),
            )
            if len(unresolved) <= lim:
                soft_ok = True
                log.warning(
                    "Content QA soft (desktop): %d/%d doan unresolved ≤ lim %d — "
                    "van giao clip, flag content_mismatch_unresolved",
                    len(unresolved), len(out), lim,
                )
        if not soft_ok:
            sample = "; ".join(
                f"#{u['i']}:{(u.get('reason') or '')[:40]}" for u in unresolved[:4]
            )
            raise RuntimeError(
                f"Content QA fail — {len(unresolved)} doan content_mismatch_unresolved "
                f"(bia/sai nghia sau khi dich lai). Khong giao clip. "
                f"(job web: hoan credit tu dong; desktop: khong tru credit) | {sample}"
            )
    return out

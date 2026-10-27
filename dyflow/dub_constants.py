"""Nguong dub — chinh o day, khong hardcode rai rac."""

# --- Bước 1: Transcribe ---
WHISPER_WORD_TIMESTAMPS = True
WHISPER_VAD_DEFAULT = False
WHISPER_VAD_MIN_SILENCE_MS = 500
WHISPER_VAD_SPEECH_PAD_MS = 200
WHISPER_DURATION_WARN_RATIO = 0.05

# --- Bước 2: Dịch / rút gọn ---
TRANSLATE_MAX_RETRIES = 2
TRANSLATE_MIN_CHAR_RATIO = {
    "vi": 0.30,
    "en": 0.35,
    "ja": 0.25,
    "pt": 0.35,
    "hi": 0.30,
    "de": 0.35,
    "id": 0.35,
    "tl": 0.35,
    "default": 0.30,
}
TRANSLATE_RETRY_SLEEP_SEC = 0.6
DUB_CHARS_PER_SEC = 14.5
# Ngân sách ký tự khi dịch/rút gọn (tiếng Việt nói tự nhiên ~14–15 ký tự/s)
TARGET_CHARS_PER_SEC = 14.5
DUB_SHORTEN_MAX_RETRIES = 0
DUB_SHORTEN_CHAR_MARGIN = 0.95
DUB_MIN_SHORTEN_CHARS = 32  # khong cat thanh manh "Ban nghi."
DUB_TINY_SLOT_SEC = 0.95
DUB_TINY_GAP_SEC = 0.45
# Gop manh Whisper thanh cau/hoi thoai truoc khi dich + TTS
DUB_GROUP_MAX_GAP_SEC = 0.55
DUB_GROUP_MAX_DUR_SEC = 8.0
DUB_GROUP_MAX_CHARS = 220
DUB_GROUP_TINY_SLOT_SEC = 1.20
# Viet dai hon Trung: cho phep noi nhe vao khoang lang sau cau, khong gom ca doan nhac
DUB_SPEECH_SPILL_RATIO = 0.40
DUB_SPEECH_SPILL_MIN = 0.35
# Khi tua 1.15x van de: gop toi da N cau sau (cung nhan vat). Khong rut gon xuong 16 ky tu.
DUB_OVERFLOW_MAX_MERGES = 3
# Sau khi gop het: rut gon NGU NGHIA (LLM), toi da N lan. Khong cat ky tu mu.
# Lan 3 CHI khi severe (cov > DUB_OVERFLOW_SEVERE_COV sau 2 lan).
# Sau lan 3: atempo dong = tts/slot, tran TTS_ATEMPO_SEVERE_MAX.
DUB_OVERFLOW_SEMANTIC_MAX_TRIES = 2
DUB_OVERFLOW_SEVERE_COV = 1.20
DUB_OVERFLOW_SEVERE_EXTRA_TRIES = 1
# Tang 4 (zero-fail): them 1 lan rut manh (bo menh de phu) sau tang 1-3.
DUB_OVERFLOW_TIER4_EXTRA_TRIES = 1
# Lan rut dat neu TTS giam >= max(abs, deficit * ratio). Hoi hot ("that su") = khong dat.
DUB_SHORTEN_MIN_DEFICIT_RATIO = 0.25
DUB_SHORTEN_MIN_ABS_SEC = 0.15
DUB_SHORTEN_TINY_DEFICIT_SEC = 0.35
DUB_SHORTEN_TINY_RATIO = 0.30
DUB_SHORTEN_TINY_ABS_SEC = 0.04

# --- Bước 3: TTS rate-matching ---
TTS_RATE_MATCH_TOLERANCE = 0.08
TTS_RATE_MATCH_FAIL_RATIO = 0.15
TTS_RATE_MAX_ITERS = 3
TTS_EDGE_RATE_MIN_PCT = -15
TTS_EDGE_RATE_MAX_PCT = 22
# OpenAI API 0.25–4.0 — KHONG dung. Speed chi lech nhe theo cam xuc goc (~1.0x).
# Khop khung bang rut gon loi + im lang dung cho, KHONG tua 2x/keo 0.5x.
TTS_OPENAI_API_SPEED_MIN = 0.25
TTS_OPENAI_API_SPEED_MAX = 4.0
TTS_NATURAL_SPEED_MIN = 0.95
TTS_NATURAL_SPEED_MAX = 1.08
TTS_OPENAI_RATE_MIN_PCT = -5
TTS_OPENAI_RATE_MAX_PCT = 8
TTS_OPENAI_SPEED_MIN = TTS_NATURAL_SPEED_MIN
TTS_OPENAI_SPEED_MAX = TTS_NATURAL_SPEED_MAX
TTS_ATEMPO_MIN = 0.92
TTS_ATEMPO_MAX = 1.08
TTS_ATEMPO_SAFE_MAX = 1.15  # lan 1-2 / near-miss; khong cat chu
# Tran CUNG sau lan rut 3 (severe). Chot 1.20x sau khi nghe mau 1.00/1.15/1.18/1.20
# (khong vo ly). Khong tua hon muc can (atempo = min(tts/slot, cap)). Vuot cap -> fail-closed.
TTS_ATEMPO_SEVERE_MAX = 1.20
# MP3/loudnorm hay them 40-100ms pad — cat pad nay khong phai cat loi noi.
DUB_ENCODER_PAD_SEC = 0.12
# Sau rut + atempo max: chap nhan de len cau sau toi da bay, co crossfade.
# Tuyet doi khong cat bot audio TTS de ep khung (giu phu de dai + file ngan = loi).
DUB_MICRO_OVERLAP_SEC = 0.25
# Tang 5 (zero-fail): overlap lon khong tran; crossfade = min(overlap, max nay).
DUB_LARGE_OVERLAP_CROSSFADE_MAX = 0.80
# tts_dur < tts_dur_raw/atempo - slack => audio bi cat vat ly (QA audio_truncated).
QA_AUDIO_TRUNC_SLACK_SEC = 0.5
TTS_ATEMPO_ONLY_IF_DRIFT_RATIO = 0.08
TTS_ATEMPO_FLAG_MIN = 0.03  # chi gan co atempo khi |tempo-1| nghe duoc
TTS_SLOT_OVERFLOW_GRACE = 1.12  # chi dung khi khong con khoang lang sau cau

# --- Bước 4: Loudnorm ---
LOUDNORM_TARGET_I = -16.0
LOUDNORM_TRUE_PEAK = -1.5
LOUDNORM_LRA = 8.0
LOUDNORM_SAMPLE_RATE = 24000
LOUDNORM_MIN_DUR = 0.40  # file ngan/cam: 2-pass crash measured_I=-inf

# --- Bước 5: Ghép + QA report ---
TIMING_WARN_MS = 100
VOLUME_WARN_DB = 2.0
CUMULATIVE_WARN_MS = 500
QA_MIN_SEGMENT_COVERAGE = 0.55  # chi canh bao im_lang, KHONG fail-closed
QA_MIN_TOTAL_COVERAGE = 0.75    # chi canh bao; clip goc co nghi thi cov <75% la binh thuong
# Phuong an A zero-fail: chong_doan_severe KHONG fail; chi canh bao review.
QA_MAX_QUA_DAI_RATIO = 1.01   # khong fail theo ty le qua_dai (tang 5 chap nhan)
QA_MAX_DRIFT_PCT = 999.0      # khong fail drift khi da chap nhan overlap lon

# --- Content QA (source_zh vs ban dich) ---
# Sau dich, truoc TTS. "sai" → dich lai; van sai → CHI flag, KHONG chan giao clip.
# User yeu cau: 100% clip phai dich xong / giao duoc — fail-closed TAT.
CONTENT_QA_FAIL_CLOSED = False
CONTENT_QA_RETRY_MAX = 1  # so lan dich lai sau cham "sai" (them 1 neu source_suspect)
CONTENT_QA_SUSPECT_EXTRA_RETRY = 1
# (Giu hang soft neu bat lai FAIL_CLOSED=True tren desktop)
CONTENT_QA_DESKTOP_SOFT_MAX_ABS = 9999
CONTENT_QA_DESKTOP_SOFT_MAX_RATIO = 1.0
# Nguon Whisper nghi ngo: slot dai ma transcript qua thua / qua dac
SOURCE_SUSPECT_MIN_SLOT_SEC = 1.5
SOURCE_SUSPECT_MIN_CPS = 1.0   # < 1 ky tu ZH / giay cho slot dai → nghi rac/trong
SOURCE_SUSPECT_MAX_CPS = 28.0  # > 28 ky tu ZH / giay → nghi dump rac
SEGMENT_CROSSFADE_MS = 15

"""Kiem tra cau hinh TRUOC khi job ton compute (tai video, Whisper, TTS).

OPENAI_API_KEY rong (copy tu .env.example) KHONG duoc che key that trong .env.
Chi fail khi da tim het cac file .env ma van khong co key.
Chi DYFLOW_ENV=dev moi duoc chay che do suy giam (test cuc bo).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .utils import log

_DEV_ENVS = {"dev", "development", "local"}

MISSING_OPENAI = (
    "Thieu OPENAI_API_KEY. Job da dung o buoc kiem tra cau hinh — "
    "chua tai video, chua ton compute. Dat key trong .env "
    "(thu muc source hoac VideosCreator). "
    "Chi dat DYFLOW_ENV=dev khi test cuc bo (moi duoc chay draft/F0)."
)


def is_dev() -> bool:
    return (os.getenv("DYFLOW_ENV") or "").strip().lower() in _DEV_ENVS


def allow_degraded() -> bool:
    """Draft / skip-LLM / F0 chi khi DYFLOW_ENV=dev — khong bao gio tu fallback o production."""
    return is_dev()


def _desktop_env_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "VideosCreator" / ".env"


def _env_files() -> list[Path]:
    """Moi file .env co the chua key. Chuoi rong khong tinh."""
    if (os.getenv("DYFLOW_IGNORE_DOTENV") or "").strip().lower() in ("1", "true", "yes"):
        return []
    out: list[Path] = []
    extra = (os.getenv("DYFLOW_ENV_FILE") or "").strip()
    if extra:
        out.append(Path(extra))
    pkg_root = Path(__file__).resolve().parents[1]
    out.append(pkg_root / ".env")
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).resolve().parent / ".env")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / ".env")
    out.append(Path.cwd() / ".env")
    out.append(_desktop_env_path())
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _read_dotenv_key(path: Path, name: str = "OPENAI_API_KEY") -> str:
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values

        return (dotenv_values(path).get(name) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def hydrate_openai_key() -> str:
    """Nap key that vao os.environ. OPENAI_API_KEY="" khong che file .env co sk-..."""
    current = (os.getenv("OPENAI_API_KEY") or "").strip()
    if current:
        return current
    for path in _env_files():
        key = _read_dotenv_key(path)
        if key:
            os.environ["OPENAI_API_KEY"] = key
            log.info("Nap OPENAI_API_KEY tu %s", path)
            return key
    return ""


def openai_key() -> str:
    return hydrate_openai_key()


def alert_ops(kind: str, message: str, *, job_id: str = "") -> None:
    """Log [OPS] + Telegram admin. Khong hien thi cho end-user."""
    prefix = f"[OPS] {kind}"
    if job_id:
        prefix += f" job={job_id}"
    log.error("%s %s", prefix, message)
    try:
        from .web.notify import alert_ops as _tg

        _tg(kind, message, job_id=job_id)
    except Exception as e:  # noqa: BLE001
        log.debug("OPS telegram bo qua: %s", e)


def require_openai(*, kind: str = "draft_mode", job_id: str = "") -> str:
    """Tra OPENAI_API_KEY. Production: raise. Dev: alert va tra chuoi rong."""
    key = openai_key()
    if key:
        return key
    if allow_degraded():
        alert_ops(kind, MISSING_OPENAI, job_id=job_id)
        log.warning("DYFLOW_ENV=dev — tiep tuc o che do suy giam (%s)", kind)
        return ""
    alert_ops("preflight_fail" if kind == "preflight" else kind, MISSING_OPENAI, job_id=job_id)
    raise RuntimeError(MISSING_OPENAI)


def _required_keys(cfg: dict | None, job: dict | None) -> list[tuple[str, str]]:
    """(env_name, human_msg) cac key pipeline can cho job nay."""
    keys = [("OPENAI_API_KEY", MISSING_OPENAI)]
    vo = {}
    if cfg:
        vo.update(cfg.get("voiceover") or {})
    if job:
        vo.update(job.get("voiceover") or {})
    engine = str(vo.get("tts_engine") or "edge").strip().lower()
    if engine in ("eleven", "elevenlabs"):
        keys.append((
            "ELEVENLABS_API_KEY",
            "Thieu ELEVENLABS_API_KEY (tts_engine=elevenlabs). Job dung o pre-flight.",
        ))
    return keys


def missing_keys(cfg: dict | None = None, job: dict | None = None) -> list[str]:
    hydrate_openai_key()
    out: list[str] = []
    env_alias = {"ELEVENLABS_API_KEY": ("ELEVENLABS_API_KEY", "ELEVEN_API_KEY")}
    for name, _msg in _required_keys(cfg, job):
        if name == "OPENAI_API_KEY":
            if not openai_key():
                out.append(name)
            continue
        aliases = env_alias.get(name, (name,))
        if not any((os.getenv(a) or "").strip() for a in aliases):
            out.append(name)
    return out


def assert_job_ready(cfg: dict | None = None, job: dict | None = None) -> None:
    """Goi TRUOC download. Thieu key that (sau khi tim .env) -> RuntimeError ngay."""
    job_id = str((job or {}).get("id") or "")
    missing = missing_keys(cfg, job)
    if not missing:
        return
    if "OPENAI_API_KEY" in missing:
        msg = MISSING_OPENAI
    else:
        msg = (
            f"Thieu {', '.join(missing)}. Job da dung o buoc kiem tra cau hinh — "
            "chua tai video. Dat key trong .env."
        )
    if allow_degraded():
        alert_ops("draft_mode", msg, job_id=job_id)
        log.warning("DYFLOW_ENV=dev — cho phep chay job thieu key: %s", ", ".join(missing))
        return
    alert_ops("preflight_fail", msg, job_id=job_id)
    raise RuntimeError(msg)

"""Loi tai Douyin co ma — phan biet not_found / need_login / network."""
from __future__ import annotations


class DyDownloadError(Exception):
    """Loi tai / liet ke Douyin (guest mode)."""

    NOT_FOUND = "not_found"
    NEED_LOGIN = "need_login"
    NETWORK = "network"
    OTHER = "other"
    INVALID_URL = "invalid_url"
    EMPTY_CHANNEL = "empty_channel"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        offer_chrome: bool = False,
        detail: str = "",
    ):
        self.code = code or self.OTHER
        # Chi offer Chrome khi goi vien bat ro — KHONG tu bat theo need_login
        # (may moi khong cookie se bi doi Chrome oan).
        self.offer_chrome = bool(offer_chrome)
        self.detail = (detail or "").strip()
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "detail": str(self),
            "offer_chrome": self.offer_chrome,
            "message": str(self),
        }


_NOT_FOUND_HINTS = (
    "not found",
    "không tìm thấy",
    "khong tim thay",
    "不存在",
    "已删除",
    "已刪除",
    "deleted",
    "找不到",
    "视频不存在",
    "作品不存在",
    "status_code\": 2",
    "status_code': 2",
    "请求失败",
    "aweme_id khong hop le",
    "khong doc duoc sec_uid",
    "kenh khong co video",
    "khong liet ke duoc kenh",
)

# Chi tin hieu VIDEO RIENG TU that. KHONG gom "cookie"/"fresh cookies" cua yt-dlp
# (cookieless app: yt-dlp luon bao can cookie → neu map can_login se doi Chrome oan).
_NEED_LOGIN_HINTS = (
    "need login",
    "需要登录",
    "请登录",
    "private video",
    "private aweme",
    "私密",
    "仅作者",
    "仅粉丝",
    "好友可见",
    "visibility",
)
_YTDLP_COOKIE_NOISE = (
    "fresh cookies",
    "cookies are required",
    "cookies for the authentication",
    "sign in to",
)

_NETWORK_HINTS = (
    "timeout",
    "timed out",
    "time out",
    "connection",
    "network",
    "dns",
    "ssl",
    "proxy",
    "temporarily unavailable",
    "502",
    "503",
    "504",
    "cdn http",
    "reset by peer",
    "unreachable",
    "name or service not known",
)


def classify_download_error(err: Exception | str, *, default_message: str = "") -> DyDownloadError:
    """Map exception/text -> DyDownloadError voi 3 nhom chinh."""
    if isinstance(err, DyDownloadError):
        return err
    raw = str(err or "").strip()
    low = raw.lower()
    msg = default_message or raw or "Loi tai video Douyin"

    if any(h in low for h in _NETWORK_HINTS):
        return DyDownloadError(
            DyDownloadError.NETWORK,
            "Loi mang / timeout. Thu lai sau.",
            detail=raw,
        )
    # yt-dlp cookie boilerplate — khong bao gio offer Chrome (app mac dinh guest).
    if any(h in low for h in _YTDLP_COOKIE_NOISE):
        return DyDownloadError(
            DyDownloadError.OTHER,
            "Khong tai duoc video o che do khach. Thu lai sau hoac doi link cong khai.",
            detail=raw,
        )
    if any(h in low for h in _NEED_LOGIN_HINTS):
        return DyDownloadError(
            DyDownloadError.NEED_LOGIN,
            "Video nay rieng tu / gioi han — khong tai duoc o che do khach.",
            offer_chrome=False,
            detail=raw,
        )
    if any(h in low for h in _NOT_FOUND_HINTS):
        return DyDownloadError(
            DyDownloadError.NOT_FOUND,
            "Khong tim thay video/kenh (da xoa hoac ID sai).",
            detail=raw,
        )
    return DyDownloadError(DyDownloadError.OTHER, msg[:400], detail=raw)


def invalid_url_error(detail: str = "") -> DyDownloadError:
    return DyDownloadError(
        DyDownloadError.INVALID_URL,
        "Link khong hop le. Dan link Douyin (video hoac kenh).",
        detail=detail,
    )


def empty_channel_error(detail: str = "") -> DyDownloadError:
    return DyDownloadError(
        DyDownloadError.EMPTY_CHANNEL,
        "Kenh nay chua co video cong khai.",
        detail=detail,
    )

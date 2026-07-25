"""Buoc 5 (TUY CHON): Dang len TikTok.

Minh KHONG wire san auto-poster vi 2 ly do thuc te:
  1. TikTok khong co API upload chinh thuc cho noi dung kieu nay; cac tool
     auto deu dung session cookie / API khong chinh thuc -> hong lien tuc,
     dE bi ban acc.
  2. Dang hang loat noi dung "lam lai" tu Douyin la dung cai TikTok dang
     siet (reused/inauthentic). Auto-post cang lam tang rui ro ban kenh.

Cach an toan hon: dung ket qua trong ./output, review tay, dang qua app.
Neu van muon tu dong: ghep repo `social-auto-upload` (GitHub) vao hook duoi.
"""
from __future__ import annotations
from pathlib import Path

from .utils import log


def upload(video_path: str | Path, cfg: dict) -> None:
    if not cfg.get("enabled"):
        log.info("Upload tat. File san sang o output (dang tay).")
        return
    raise NotImplementedError(
        "Auto-upload chua wire. Xem README muc 'Upload' truoc khi bat. "
        "Goi y: tich hop social-auto-upload va goi tu day."
    )

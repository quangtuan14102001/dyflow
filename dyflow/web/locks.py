"""Khoa tai nguyen dung chung cho worker da luong.

Khi chay nhieu job song song, cac tai nguyen sau KHONG the dung dong thoi:
- Chrome profile (Playwright persistent context): 1 job/lan.
- Whisper model (RAM lon): 1 job/lan de tranh nap trung + OOM.
- Demucs (CPU/GPU nang): 1 job/lan.

Cac lock la threading.Lock cap module -> voi CLI/Telegram (1 luong) chung vo hai.
"""
from __future__ import annotations

import threading

CHROME_LOCK = threading.Lock()
WHISPER_LOCK = threading.Lock()
DEMUCS_LOCK = threading.Lock()

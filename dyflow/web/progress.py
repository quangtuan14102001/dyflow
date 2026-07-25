"""Cac buoc (stage) pipeline + nhan hien thi cho UI."""
from __future__ import annotations

# Thu tu + nhan tieng Viet cho tung stage phat ra tu process_job()
STAGES: list[tuple[str, str]] = [
    ("queued", "Dang cho"),
    ("starting", "Chuan bi"),
    ("downloading", "Tai video"),
    ("trimming", "Cat clip"),
    ("transforming", "Bien doi"),
    ("transcribing", "Nhan dang + dich"),
    ("synthesizing", "Long tieng"),
    ("composing", "Ghep + xuat"),
    ("completed", "Hoan tat"),
]

STAGE_LABELS: dict[str, str] = dict(STAGES)
STAGE_ORDER: list[str] = [s for s, _ in STAGES]


def stage_index(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 0


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)

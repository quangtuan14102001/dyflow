"""dyflow - orchestrator.

Chay: python -m dyflow.main --config config.yaml --jobs jobs.json
"""
from __future__ import annotations
import argparse
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .utils import (
    setup_logging, load_config, load_jobs, deep_merge,
    ensure_dirs, pick_music, log, probe_duration, probe_has_audio, run,
)
from . import downloader, transform, voiceover, compose, upload, broll

# progress(stage: str, percent: int, message: str) -> None
ProgressFn = Callable[[str, int, str], None]


def _emit(progress: Optional[ProgressFn], stage: str, percent: int, message: str) -> None:
    """Goi callback tien do neu co; khong bao gio raise."""
    if progress is None:
        return
    try:
        progress(stage, percent, message)
    except Exception:  # noqa: BLE001
        log.debug("progress callback loi (bo qua)", exc_info=True)


def process_job(job: dict, cfg: dict, progress: Optional[ProgressFn] = None) -> list[Path]:
    job_id = job.get("id") or "job"
    log.info("=== JOB: %s ===", job_id)
    _emit(progress, "starting", 2, "Chuan bi job...")

    # merge override cua job len config
    jcfg = dict(cfg)
    for key in ("transform", "voiceover", "caption", "music", "export"):
        if key in job:
            jcfg[key] = deep_merge(cfg.get(key, {}), job[key])
    if "formats" in job and "export" not in job:
        jcfg["export"] = deep_merge(jcfg.get("export", {}), {"formats": job["formats"]})

    paths = cfg["paths"]
    work = Path(paths["work_dir"]) / job_id
    ensure_dirs(work, paths["output_dir"])

    # 1. DOWNLOAD (hoac dung file local)
    src = job["source"]
    raw_dir = work / "raw"
    ensure_dirs(raw_dir)
    src_path = Path(str(src))
    if src_path.exists() and src_path.is_file():
        _emit(progress, "downloading", 5, "Dung file local...")
        raw = raw_dir / src_path.name
        if raw.resolve() != src_path.resolve():
            import shutil
            shutil.copy2(src_path, raw)
        log.info("Dung file local: %s", raw.name)
    else:
        _emit(progress, "downloading", 5, "Dang tai video Douyin...")
        dl_cfg = dict(cfg.get("download") or {})
        # web/bot: gioi han do dai tai som — tranh clip 40+ phut
        max_dur = float(job.get("max_duration_sec") or cfg.get("max_duration_sec") or 0)
        if max_dur > 0:
            dl_cfg["max_duration_sec"] = max_dur
        raw = downloader.download_one(src, raw_dir, dl_cfg)
    if not probe_has_audio(raw):
        raise RuntimeError(
            "Video tai ve khong co am thanh (Chrome chi bat duoc video stream). "
            "Hay thu tao lai job, hoac mo config chrome_headless: false roi thu lai."
        )
    video_dur = probe_duration(raw)

    # Cat ngan neu qua dai (bot: ~2 phut de test / gui Telegram)
    max_dur = float(job.get("max_duration_sec") or cfg.get("max_duration_sec") or 0)
    auto_trim = bool(job.get("auto_trim", cfg.get("auto_trim", True)))
    if max_dur > 0 and video_dur > max_dur + 0.5:
        if not auto_trim:
            raise RuntimeError(
                f"Video qua dai (~{video_dur/60:.0f} phut). Gioi han {max_dur/60:.0f} phut.\n"
                "Hay gui clip ngan, hoac bat auto_trim."
            )
        trimmed = raw_dir / f"{raw.stem}_trim{max_dur:.0f}s.mp4"
        _emit(progress, "trimming", 15, f"Cat {max_dur:.0f} giay dau...")
        log.info(
            "Video %.0fs > gioi han %.0fs -> cat %.0f giay dau...",
            video_dur, max_dur, max_dur,
        )
        trim_cmd = [
            "ffmpeg", "-y", "-ss", "0", "-t", f"{max_dur:.3f}",
            "-i", str(raw),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(trimmed),
        ]
        run(trim_cmd)
        raw = trimmed
        video_dur = probe_duration(raw)
        log.info("Da cat: %s (%.1fs)", raw.name, video_dur)

    # 2. TRANSFORM (bien doi sau)
    if jcfg.get("transform", {}).get("enabled", True):
        _emit(progress, "transforming", 20, "Bien doi video...")
        edited = work / "edited.mp4"
        transform.transform(raw, edited, jcfg["transform"])
        # 2b. B-ROLL (tuy chon)
        br = jcfg.get("transform", {}).get("broll", {})
        if br.get("enabled"):
            wrapped = work / "wrapped.mp4"
            edited = broll.wrap(
                edited, wrapped,
                intro=br.get("intro") or None,
                outro=br.get("outro") or None,
                W=jcfg["transform"].get("target_width", 1080),
                H=jcfg["transform"].get("target_height", 1920),
            )
    else:
        edited = raw

    # 3. VOICEOVER / SCRIPT
    voice_file = None
    word_timings = None
    segment_cues = None
    sync_to_video = False
    script = job.get("script", "")
    vo_cfg = jcfg.get("voiceover", {})
    audio_mode = str(vo_cfg.get("audio_mode", "subtitle")).lower()

    if script == "AUTO" or (vo_cfg.get("enabled", True) and script):
        if script == "AUTO":
            _emit(progress, "transcribing", 35, "Nhan dang + dich thoai...")
            script, segments = voiceover.auto_script(
                raw, work, vo_cfg.get("auto", {}),
            )
            segment_cues = segments
            sync_to_video = True
            log.info("Audio mode: %s (%d doan thoai)", audio_mode, len(segments))

            if audio_mode != "subtitle" and bool(
                (vo_cfg.get("auto") or {}).get("cast_speakers", True)
            ):
                from . import speakers as speakers_mod
                _emit(progress, "transcribing", 55, "Nhan dien nhan vat, gan giong...")
                segments = speakers_mod.assign_cast(
                    segments,
                    source_video=raw,
                    work_dir=work,
                    lang=str(job.get("lang") or "vi"),
                    fallback_voice=str(vo_cfg.get("voice_id") or ""),
                    fallback_cfg=vo_cfg,
                )
                segment_cues = segments

            if audio_mode == "subtitle":
                voice_file = None
                jcfg["voiceover"] = {**vo_cfg, "mute_original": False}
            elif audio_mode == "hybrid":
                _emit(progress, "synthesizing", 65, "Tao giong long tieng...")
                voice_file = work / "voice.mp3"
                voice_file, segment_cues = voiceover.synthesize_segments(
                    segments, work / "seg_tts", voice_file, vo_cfg,
                    video_dur=video_dur, source_video=raw,
                )
                jcfg["voiceover"] = {
                    **vo_cfg,
                    "mute_original": False,
                    "original_volume": float(vo_cfg.get("original_volume", 0.25)),
                    "preserve_fx": True,
                }
            else:  # dub
                _emit(progress, "synthesizing", 65, "Tao giong long tieng...")
                voice_file = work / "voice.mp3"
                voice_file, segment_cues = voiceover.synthesize_segments(
                    segments, work / "seg_tts", voice_file, vo_cfg,
                    video_dur=video_dur, source_video=raw,
                )
                jcfg["voiceover"] = {
                    **vo_cfg,
                    "mute_original": False,
                    "mute_original_speech": True,
                    "strip_original_vocals": bool(vo_cfg.get("strip_original_vocals", True)),
                    "ambient_volume": float(vo_cfg.get("ambient_volume", 0.38)),
                    "speech_gate_volume": float(vo_cfg.get("speech_gate_volume", 0.0)),
                    "speech_gate_pad_before": float(vo_cfg.get("speech_gate_pad_before", 0.40)),
                    "speech_gate_pad_after": float(vo_cfg.get("speech_gate_pad_after", 0.18)),
                    "dub_volume": float(vo_cfg.get("dub_volume", 5.0)),
                    "preserve_fx": True,
                }
        elif vo_cfg.get("enabled", True):
            _emit(progress, "synthesizing", 65, "Tao giong long tieng...")
            voice_file = work / "voice.mp3"
            voice_file, word_timings = voiceover.synthesize(script, voice_file, vo_cfg)

    # 4. COMPOSE + EXPORT
    music_cfg = dict(jcfg.get("music", {}))
    music_cfg["_music_dir"] = paths.get("music_dir", "./assets/music")
    if job.get("music"):
        music_cfg["file"] = job["music"]
    chosen_music = pick_music(music_cfg)

    _emit(progress, "composing", 88, "Ghep caption + xuat video...")
    outputs = compose.compose(
        video=edited,
        voice=voice_file,
        script=script,
        out_dir=paths["output_dir"],
        base_name=job_id,
        cfg={**jcfg, "paths": paths},
        music=chosen_music,
        word_timings=word_timings,
        segment_cues=segment_cues,
        sync_to_video=sync_to_video,
    )

    # 5. UPLOAD (mac dinh tat)
    for out in outputs:
        upload.upload(out, cfg.get("upload", {}))

    # Dub QA — moi job (web/batch/CLI), khong chi clip mau
    vo_cfg = jcfg.get("voiceover") or {}
    if (
        segment_cues
        and str(vo_cfg.get("audio_mode", "dub")).lower() == "dub"
        and vo_cfg.get("dub_qa_fail_closed", True)
    ):
        from .dub_audit import audit_script_han, audit_work_dir
        qa_work = Path(paths["work_dir"]) / job_id
        if (qa_work / "segments_qa.json").exists():
            engine = str(vo_cfg.get("tts_engine") or "edge")
            code, summary, lines = audit_work_dir(qa_work, tts_engine=engine)
            if audit_script_han(qa_work):
                code = 1
                summary += " | FAIL: con chu Han"
            if code != 0:
                for ln in lines[:6]:
                    log.warning("  dub QA: %s", ln)
                raise RuntimeError(f"Dub QA fail — khong giao clip loi. {summary}")
            log.info("Dub QA pass: %s", summary)

    _emit(progress, "completed", 100, "Hoan tat!")
    log.info("JOB %s xong: %d file", job_id, len(outputs))
    return outputs


def selftest(cfg: dict) -> int:
    """Tao clip mau + chay transform/compose/caption/export (khong tai, khong TTS).
    Dung de kiem tra ffmpeg + filter chay dung tren may ban."""
    from .utils import run, check_tool
    if not check_tool("ffmpeg"):
        log.error("Thieu ffmpeg. Cai: apt install ffmpeg / brew install ffmpeg")
        return 2
    work = Path(cfg["paths"]["work_dir"]) / "_selftest"
    ensure_dirs(work, cfg["paths"]["output_dir"])
    src = work / "sample.mp4"
    voice = work / "voice_dummy.mp3"
    log.info("Tao clip mau...")
    run(["ffmpeg", "-y", "-f", "lavfi", "-i",
         "testsrc=size=720x1280:rate=30:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)])
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
         "-c:a", "libmp3lame", str(voice)])

    edited = work / "edited.mp4"
    transform.transform(src, edited, cfg["transform"])
    outs = compose.compose(
        video=edited, voice=voice,
        script="Day la ban chay thu. Buoc mot kiem tra transform. "
               "Buoc hai kiem tra long tieng. Buoc ba kiem tra caption.",
        out_dir=cfg["paths"]["output_dir"], base_name="selftest",
        cfg=cfg, music=None,
    )
    log.info("SELFTEST OK -> %s", ", ".join(p.name for p in outs))
    log.info("Mo file de xem caption + khung hinh co dung khong.")
    return 0



def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="dyflow",
        description="Douyin -> edit -> long tieng Viet -> export pipeline",
    )
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--jobs", default="jobs.json")
    p.add_argument("--only", help="chi chay 1 job theo id")
    p.add_argument("--selftest", action="store_true",
                   help="chay thu khong can link/mang de kiem tra cai dat")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"dyflow {__version__}")
    args = p.parse_args(argv)

    setup_logging(args.verbose)

    if not Path(args.config).exists():
        log.error("Khong thay config: %s", args.config); return 2

    cfg = load_config(args.config)

    if args.selftest:
        return selftest(cfg)

    if not Path(args.jobs).exists():
        log.error("Khong thay jobs: %s", args.jobs); return 2

    jobs = load_jobs(args.jobs)
    if args.only:
        jobs = [j for j in jobs if j.get("id") == args.only]
        if not jobs:
            log.error("Khong thay job id=%s", args.only); return 2

    ok, fail = 0, 0
    for job in jobs:
        try:
            process_job(job, cfg)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            log.error("JOB %s LOI: %s", job.get("id"), e)
            if args.verbose:
                traceback.print_exc()

    log.info("XONG. Thanh cong: %d | Loi: %d", ok, fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

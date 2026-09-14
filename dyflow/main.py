"""dyflow - orchestrator.

Chay: python -m dyflow.main --config config.yaml --jobs jobs.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .utils import (
    setup_logging, load_config, load_jobs, deep_merge,
    ensure_dirs, pick_music, log, probe_duration, probe_has_audio, run,
)
from . import downloader, transform, voiceover, compose, upload, broll
from .job_timing import (
    begin as timing_begin,
    finish as timing_finish,
    note as timing_note,
    record_step as timing_record,
    set_video_dur,
    step as timing_step,
)

# progress(stage: str, percent: int, message: str) -> None
ProgressFn = Callable[[str, int, str], None]


class JobCancelled(Exception):
    """Nguoi dung bam Huy — dung pipeline som."""


def _emit(progress: Optional[ProgressFn], stage: str, percent: int, message: str) -> None:
    """Goi callback tien do neu co. JobCancelled luon lan ra ngoai."""
    if progress is None:
        return
    try:
        progress(stage, percent, message)
    except JobCancelled:
        raise
    except Exception:  # noqa: BLE001
        log.debug("progress callback loi (bo qua)", exc_info=True)


def process_job(job: dict, cfg: dict, progress: Optional[ProgressFn] = None) -> list[Path]:
    job_id = job.get("id") or "job"
    log.info("=== JOB: %s ===", job_id)
    _emit(progress, "starting", 1, "Kiem tra cau hinh...")

    # merge override cua job len config
    jcfg = dict(cfg)
    for key in ("transform", "voiceover", "caption", "music", "export"):
        if key in job:
            jcfg[key] = deep_merge(cfg.get(key, {}), job[key])
    if "formats" in job and "export" not in job:
        jcfg["export"] = deep_merge(jcfg.get("export", {}), {"formats": job["formats"]})

    from .preflight import assert_job_ready
    assert_job_ready(jcfg, job)

    paths = cfg["paths"]
    work = Path(paths["work_dir"]) / job_id
    ensure_dirs(work, paths["output_dir"])
    timing_begin(job_id, work)
    timing_note(
        "arch",
        transcribe="sequential (Whisper 1-2 lan, khong threadpool)",
        translate="1 LLM batch ca clip (khong per-seg gather)",
        voiceover="prefetch TTS song song (4); overflow/atempo van sequential",
        demucs="thread rieng sau Download, join truoc mix",
        llm_shorten="sequential per overflow segment",
        jobs="desktop 1 worker; web workers trong config (khong song song segment)",
    )
    job_err: str | None = None
    try:
        return _process_job_timed(
            job, cfg, jcfg, paths, work, progress,
        )
    except Exception as e:  # noqa: BLE001
        job_err = str(e)[:300]
        raise
    finally:
        timing_finish(error=job_err)


def _process_job_timed(
    job: dict,
    cfg: dict,
    jcfg: dict,
    paths: dict,
    work: Path,
    progress: Optional[ProgressFn],
) -> list[Path]:
    job_id = job.get("id") or "job"

    # 1. DOWNLOAD (hoac dung file local)
    src = job["source"]
    raw_dir = work / "raw"
    ensure_dirs(raw_dir)
    src_path = Path(str(src))
    with timing_step("Download"):
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
        set_video_dur(video_dur)

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
        timing_note("ffmpeg Trim", preset="veryfast", crf=20, codec="libx264")
        with timing_step("Trim"):
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
            set_video_dur(video_dur)
        log.info("Da cat: %s (%.1fs)", raw.name, video_dur)
    else:
        timing_note("Trim skipped (khong cat)")

    # 2. TRANSFORM (bien doi sau)
    skip_tf = os.getenv("DYFLOW_SKIP_TRANSFORM", "").strip().lower() in ("1", "true", "yes")
    if skip_tf:
        jcfg.setdefault("transform", {})["enabled"] = False
    if jcfg.get("transform", {}).get("enabled", True):
        _emit(progress, "transforming", 20, "Bien doi video...")
        edited = work / "edited.mp4"
        timing_note("ffmpeg Transform", preset="fast", crf=20, codec="libx264")
        with timing_step("Transform"):
            transform.transform(raw, edited, jcfg["transform"])
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
        timing_note("Transform skipped")

    # Demucs CHI can video goc (edited/raw) — khong doc voice.mp3.
    # Bat dau ngay sau Download/Transform, song song Transcribe+Voiceover.
    demucs_job: dict | None = None
    audio_mode_early = str(jcfg.get("voiceover", {}).get("audio_mode", "subtitle")).lower()
    strip_early = bool(jcfg.get("voiceover", {}).get("strip_original_vocals", True))
    # Windows: Demucs sau TTS (khong song song) — chat luong mix giu nguyen,
    # tranh tranh CPU voi Whisper/Edge luc dang long tieng.
    demucs_after_tts = sys.platform == "win32"
    if audio_mode_early != "subtitle" and strip_early and not demucs_after_tts:
        from . import audio_fx as _audio_fx

        work_audio = Path(edited).resolve().parent / "audio_fx"
        demucs_job = {"bed": None, "err": None, "dur": 0.0, "done": threading.Event()}

        def _demucs_run() -> None:
            t0 = time.perf_counter()
            try:
                demucs_job["bed"] = _audio_fx.make_ambient_bed(
                    edited, work_audio,
                    enabled=True,
                    model=str(jcfg.get("voiceover", {}).get("demucs_model", "htdemucs")),
                )
            except Exception as e:  # noqa: BLE001
                demucs_job["err"] = e
                log.warning("Demucs song song that bai: %s", e)
            demucs_job["dur"] = time.perf_counter() - t0
            demucs_job["done"].set()

        threading.Thread(
            target=_demucs_run, name="dyflow-demucs", daemon=True,
        ).start()
        timing_note("Demucs started (parallel)", video=str(edited))

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
            auto_cfg = dict(vo_cfg.get("auto") or {})
            if audio_mode != "subtitle":
                auto_cfg["defer_translate"] = True
            with timing_step("Transcribe+translate"):
                script, segments = voiceover.auto_script(
                    raw, work, auto_cfg, progress=progress,
                )
                segment_cues = segments
                sync_to_video = True
                log.info("Audio mode: %s (%d doan thoai)", audio_mode, len(segments))

                if audio_mode != "subtitle" and bool(
                    auto_cfg.get("cast_speakers", True)
                ):
                    from . import speakers as speakers_mod
                    _emit(progress, "transcribing", 55, "Nhan dien nhan vat, gan giong...")
                    with timing_step("assign_cast"):
                        segments = speakers_mod.assign_cast(
                            segments,
                            source_video=raw,
                            work_dir=work,
                            lang=str(job.get("lang") or "vi"),
                            fallback_voice=str(vo_cfg.get("voice_id") or ""),
                            fallback_cfg=vo_cfg,
                        )
                    segment_cues = segments

                if auto_cfg.get("defer_translate"):
                    from .translate_llm import lock_cast_relations, translate_segments_budgeted
                    lang = str(job.get("lang") or auto_cfg.get("translate_to") or "vi")
                    _emit(progress, "transcribing", 58, "Khoa xung ho + giong TTS + dich...")
                    with timing_step("lock_cast_relations"):
                        segments = lock_cast_relations(
                            segments, target=lang, work_dir=work,
                        )
                    from .speakers import detect_speaker_voice_profile
                    _emit(progress, "transcribing", 60, "Do pitch + khoa giong TTS...")
                    with timing_step("detect_speaker_voice_profile"):
                        segments = detect_speaker_voice_profile(
                            segments,
                            source_video=raw,
                            work_dir=work,
                            target_lang=lang,
                        )
                    with timing_step("translate_segments_budgeted"):
                        segments = translate_segments_budgeted(
                            segments,
                            video_dur=video_dur,
                            target=lang,
                            work_dir=work,
                        )
                    script = " ".join(str(s.get("text") or "") for s in segments)
                    segment_cues = segments
                    (work / "script_auto_vi.txt").write_text(script, encoding="utf-8")
                    (work / "segments_vi.json").write_text(
                        json.dumps(segments, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

            if audio_mode == "subtitle":
                voice_file = None
                jcfg["voiceover"] = {**vo_cfg, "mute_original": False}
            elif audio_mode == "hybrid":
                _emit(progress, "synthesizing", 65, "Tao giong long tieng...")
                voice_file = work / "voice.mp3"
                with timing_step("Voiceover"):
                    voice_file, segment_cues = voiceover.synthesize_segments(
                        segments, work / "seg_tts", voice_file, vo_cfg,
                        video_dur=video_dur, source_video=raw,
                        progress=progress,
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
                with timing_step("Voiceover"):
                    voice_file, segment_cues = voiceover.synthesize_segments(
                        segments, work / "seg_tts", voice_file, vo_cfg,
                        video_dur=video_dur, source_video=raw,
                        progress=progress,
                    )
                from .dub_audit import raise_if_dub_qa_fail
                _emit(progress, "synthesizing", 82, "Kiem tra chat luong dub...")
                raise_if_dub_qa_fail(work, vo_cfg)
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
            with timing_step("Voiceover"):
                voice_file, word_timings = voiceover.synthesize(script, voice_file, vo_cfg)

    # 4. COMPOSE + EXPORT
    music_cfg = dict(jcfg.get("music", {}))
    music_cfg["_music_dir"] = paths.get("music_dir", "./assets/music")
    if job.get("music"):
        music_cfg["file"] = job["music"]
    chosen_music = pick_music(music_cfg)

    _emit(progress, "composing", 88, "Ghep caption + xuat video...")
    exp = jcfg.get("export") or {}
    timing_note(
        "ffmpeg Compose+export",
        preset=str(exp.get("preset", "medium")),
        crf=exp.get("crf", 20),
        codec="libx264",
    )
    ambient_bed = None
    if demucs_job is None and demucs_after_tts and audio_mode_early != "subtitle" and strip_early:
        from . import audio_fx as _audio_fx

        _emit(progress, "composing", 86, "Tach nhac nen (Demucs)...")
        t0 = time.perf_counter()
        try:
            work_audio = Path(edited).resolve().parent / "audio_fx"
            ambient_bed = _audio_fx.make_ambient_bed(
                edited, work_audio,
                enabled=True,
                model=str(jcfg.get("voiceover", {}).get("demucs_model", "htdemucs")),
            )
            timing_record("Demucs", time.perf_counter() - t0, extra="after-tts")
        except Exception as e:  # noqa: BLE001
            log.warning("Demucs sau TTS loi — compose se thu lai: %s", e)
            timing_record("Demucs", time.perf_counter() - t0, extra="after-tts-fail")
    elif demucs_job is not None:
        _emit(progress, "composing", 86, "Cho tach nhac nen...")
        demucs_job["done"].wait()
        timing_record("Demucs", float(demucs_job["dur"] or 0), extra="parallel")
        if demucs_job.get("err"):
            log.warning("Demucs parallel loi — compose se thu lai: %s", demucs_job["err"])
        else:
            ambient_bed = demucs_job.get("bed")

    with timing_step("Compose+export"):
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
            ambient_bed=ambient_bed,
        )

    # 5. UPLOAD (mac dinh tat)
    for out in outputs:
        upload.upload(out, cfg.get("upload", {}))

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

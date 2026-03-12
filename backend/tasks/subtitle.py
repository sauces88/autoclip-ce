"""
字幕烧录 Celery 任务
"""
import json
import logging
from pathlib import Path

from backend.core.celery_app import celery_app
from backend.core.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name='backend.tasks.subtitle.burn_subtitle_task')
def burn_subtitle_task(
    self,
    project_id: str,
    clip_id: str,
    srt_content: str,
    video_path: str,
    output_path: str,
    vid_w: int,
    vid_h: int,
    preset_id: str,
    style_overrides: dict = None,
    trim_start: float = None,
    trim_end: float = None,
):
    """异步执行字幕烧录"""
    db = SessionLocal()
    try:
        from backend.models.clip import Clip
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if clip:
            clip.burn_status = "burning"
            db.commit()

        from backend.pipeline.step7_subtitle_burn import _burn_subtitles
        ok = _burn_subtitles(
            Path(video_path), srt_content, Path(output_path),
            vid_w, vid_h, preset_id, style_overrides,
            trim_start=trim_start,
            trim_end=trim_end,
        )

        if clip:
            clip.burn_status = "done" if ok else "failed"
            db.commit()

        # 更新 clips_metadata.json
        if ok:
            try:
                from backend.core.path_utils import get_projects_directory
                projects_dir = get_projects_directory()
                metadata_path = projects_dir / project_id / "metadata" / "clips_metadata.json"
                if metadata_path.exists():
                    with open(metadata_path, encoding="utf-8") as f:
                        clips = json.load(f)
                    for c in clips:
                        if str(c.get("id", "")) == str(clip_id):
                            c["subtitle_video_path"] = output_path
                            break
                    with open(metadata_path, "w", encoding="utf-8") as f:
                        json.dump(clips, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"更新metadata失败: {e}")

        return {"success": ok}

    except Exception as e:
        logger.error(f"字幕烧录任务失败: {e}")
        try:
            clip = db.query(Clip).filter(Clip.id == clip_id).first()
            if clip:
                clip.burn_status = "failed"
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()

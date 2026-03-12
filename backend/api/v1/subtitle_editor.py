"""
字幕编辑器 API
"""
import json
import logging
import re
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Any

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core.path_utils import get_projects_directory
from ...core.database import get_db
from ...services.project_service import ProjectService

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── 模型 ───────────────────────────────────────────────

class SrtEntry(BaseModel):
    id: str
    index: int
    startTime: float
    endTime: float
    text: str
    disabled: Optional[bool] = False


class BurnRequest(BaseModel):
    preset_id: str = "classic"
    custom_outline: Optional[int] = None       # 0-4，覆盖预设描边粗细
    custom_shadow: Optional[int] = None        # 0-2，覆盖预设阴影
    custom_bold: Optional[int] = None          # 0/1，覆盖加粗
    custom_color: Optional[str] = None         # "#RRGGBB"，覆盖字幕颜色
    custom_outline_color: Optional[str] = None # "#RRGGBB"，覆盖描边颜色
    custom_font_size_ratio: Optional[float] = None  # 0.75 / 1.0 / 1.3 / 1.7


class SubtitleEditRequest(BaseModel):
    project_id: str
    clip_id: str
    deleted_segments: List[str]


class EditPreviewRequest(BaseModel):
    project_id: str
    clip_id: str
    deleted_segments: List[str]


# ─── 工具函数 ────────────────────────────────────────────

def _parse_srt_time(ts: str) -> float:
    import re as _re
    m = _re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        return 0.0
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _seconds_to_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt_file(srt_path: Path) -> List[SrtEntry]:
    """解析 SRT 文件为 SrtEntry 列表"""
    content = srt_path.read_text(encoding="utf-8-sig")
    entries = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        tm = re.match(r"(.+?)\s*-->\s*(.+)", lines[1])
        if not tm:
            continue
        text = "\n".join(lines[2:]).strip()
        entries.append(SrtEntry(
            id=str(uuid.uuid4()),
            index=idx,
            startTime=_parse_srt_time(tm.group(1)),
            endTime=_parse_srt_time(tm.group(2)),
            text=text,
        ))
    return entries


def _entries_to_srt(entries: List[SrtEntry]) -> str:
    """将 SrtEntry 列表序列化为 SRT 字符串"""
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_seconds_to_srt_time(e.startTime)} --> {_seconds_to_srt_time(e.endTime)}")
        lines.append(e.text)
        lines.append("")
    return "\n".join(lines)


def _parse_time_value(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if re.match(r"\d+:\d+:\d+[,.]", s):
        return _parse_srt_time(s.replace(".", ","))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _get_clip_info(project_id: str, clip_id: str, db=None) -> Dict[str, Any]:
    """从 clips_metadata.json 或数据库中查找 clip 信息，返回 clip dict"""
    projects_dir = get_projects_directory()
    metadata_path = projects_dir / project_id / "metadata" / "clips_metadata.json"

    # 优先尝试从 clips_metadata.json 匹配（流水线 ID 或 UUID）
    if metadata_path.exists():
        with open(metadata_path, encoding="utf-8") as f:
            clips = json.load(f)
        for clip in clips:
            if str(clip.get("id", "")) == str(clip_id):
                return clip

    # 回退：用数据库 UUID 查找 clip，构造兼容字典
    if db is not None:
        try:
            from ...models.clip import Clip as ClipModel
            db_clip = db.query(ClipModel).filter(
                ClipModel.id == clip_id,
                ClipModel.project_id == project_id,
            ).first()
            if db_clip:
                return {
                    "id": clip_id,
                    "generated_title": db_clip.title,
                    "title": db_clip.title,
                    "start_time": db_clip.start_time,
                    "end_time": db_clip.end_time,
                    "video_path": db_clip.video_path,
                }
        except Exception as ex:
            logger.warning(f"数据库查找 clip 失败: {ex}")

    raise HTTPException(status_code=404, detail=f"clip {clip_id} 不存在")


def _get_safe_name(clip: Dict) -> str:
    title = clip.get("generated_title", clip.get("title", f"clip_{clip.get('id','')}"))
    return re.sub(r'[\\/:*?"<>|]', "_", str(title))


def _get_srt_path(project_id: str, safe_name: str) -> Path:
    projects_dir = get_projects_directory()
    return projects_dir / project_id / "metadata" / "srt" / f"{safe_name}.srt"


def _get_json_path(project_id: str, safe_name: str) -> Path:
    projects_dir = get_projects_directory()
    return projects_dir / project_id / "metadata" / "srt" / f"{safe_name}.json"


def _entries_to_srt_from_dicts(entries: List[Dict]) -> str:
    """将 dict 列表（含 startTime/endTime 键名）转为 SRT 字符串"""
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_seconds_to_srt_time(e['startTime'])} --> {_seconds_to_srt_time(e['endTime'])}")
        lines.append(e['text'])
        lines.append("")
    return "\n".join(lines)


def _get_clip_video_path(project_id: str, clip: Dict) -> Optional[Path]:
    """查找clip的视频文件路径"""
    # 先从metadata字段查
    for key in ("video_path", "clip_path", "output_path"):
        p = clip.get(key)
        if p and Path(p).exists():
            return Path(p)
    # 从项目clips目录搜索
    projects_dir = get_projects_directory()
    clips_dir = projects_dir / project_id / "output" / "clips"
    clip_id = str(clip.get("id", ""))
    safe_name = _get_safe_name(clip)
    for candidate in [
        clips_dir / f"{clip_id}_{safe_name}.mp4",
        clips_dir / f"{safe_name}.mp4",
        clips_dir / f"clip_{clip_id}.mp4",
        clips_dir / f"{clip_id}.mp4",
    ]:
        if candidate.exists():
            return candidate
    if clips_dir.exists():
        for f in clips_dir.glob(f"{clip_id}_*.mp4"):
            return f
    return None


# ─── 端点 ───────────────────────────────────────────────

def get_db_dep():
    return Depends(get_db)


@router.get("/{project_id}/clips/{clip_id}/subtitles", response_model=List[SrtEntry])
async def get_clip_subtitles(project_id: str, clip_id: str, db: Session = Depends(get_db)):
    """获取clip的SRT条目（优先读纠错SRT，否则从全局SRT实时提取）"""
    clip = _get_clip_info(project_id, clip_id, db)
    safe_name = _get_safe_name(clip)
    srt_path = _get_srt_path(project_id, safe_name)

    # 优先读 JSON（含 disabled 状态）
    json_path = _get_json_path(project_id, safe_name)
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return [SrtEntry(**item) for item in data]

    # 其次读 SRT（旧数据，disabled 全为 false）
    if srt_path.exists():
        return _parse_srt_file(srt_path)

    # fallback：从全局 raw/input.srt 实时提取
    projects_dir = get_projects_directory()
    global_srt = projects_dir / project_id / "raw" / "input.srt"
    if not global_srt.exists():
        return []

    # 导入step7工具函数实时提取
    try:
        from ...pipeline.step7_subtitle_burn import (
            _parse_srt, _extract_clip_entries, _rule_correct_entries, _seconds_to_srt_time as _s2t
        )
        all_entries = _parse_srt(global_srt)
        start = _parse_time_value(clip.get("start_time", 0))
        end = _parse_time_value(clip.get("end_time", 0))
        clip_entries = _extract_clip_entries(all_entries, start, end)
        clip_entries = _rule_correct_entries(clip_entries)
        result = []
        for i, e in enumerate(clip_entries, 1):
            result.append(SrtEntry(
                id=str(uuid.uuid4()),
                index=i,
                startTime=e["start"],
                endTime=e["end"],
                text=e["text"],
            ))
        return result
    except Exception as ex:
        logger.warning(f"实时提取字幕失败: {ex}")
        return []


@router.put("/{project_id}/clips/{clip_id}/subtitles")
async def save_clip_subtitles(project_id: str, clip_id: str, entries: List[SrtEntry], db: Session = Depends(get_db)):
    """保存SRT条目到文件"""
    clip = _get_clip_info(project_id, clip_id, db)
    safe_name = _get_safe_name(clip)
    srt_path = _get_srt_path(project_id, safe_name)
    srt_path.parent.mkdir(parents=True, exist_ok=True)

    # 保存 JSON（含 disabled 字段）
    json_path = _get_json_path(project_id, safe_name)
    json_path.write_text(
        json.dumps([e.dict() for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 同时保存 SRT（仅启用条目，保持兼容）
    enabled = [e for e in entries if not e.disabled]
    srt_content = _entries_to_srt(enabled)
    srt_path.write_text(srt_content, encoding="utf-8")
    return {"success": True}


@router.get("/{project_id}/clips/{clip_id}/presets")
async def get_presets(project_id: str, clip_id: str):
    """返回所有字幕样式预设"""
    try:
        from ...core.subtitle_presets import SUBTITLE_PRESETS
        result = []
        for preset_id, preset in SUBTITLE_PRESETS.items():
            result.append({"id": preset_id, **preset})
        return result
    except ImportError:
        return []


@router.post("/{project_id}/clips/{clip_id}/burn")
async def burn_clip_subtitles(project_id: str, clip_id: str, request: BurnRequest, db: Session = Depends(get_db)):
    """异步提交字幕烧录任务（Celery）"""
    clip = _get_clip_info(project_id, clip_id, db)
    safe_name = _get_safe_name(clip)
    srt_path = _get_srt_path(project_id, safe_name)
    json_path = _get_json_path(project_id, safe_name)

    if not srt_path.exists() and not json_path.exists():
        raise HTTPException(status_code=404, detail="SRT文件不存在，请先完成字幕生成或保存")

    video_path = _get_clip_video_path(project_id, clip)
    if not video_path:
        raise HTTPException(status_code=404, detail="找不到clip视频文件")

    # 读取字幕条目，过滤禁用项
    total_count = 0
    if json_path.exists():
        all_entries = json.loads(json_path.read_text(encoding="utf-8"))
        total_count = len(all_entries)
        enabled_entries = [e for e in all_entries if not e.get("disabled")]
    else:
        parsed = _parse_srt_file(srt_path)
        total_count = len(parsed)
        enabled_entries = [{"startTime": e.startTime, "endTime": e.endTime, "text": e.text}
                           for e in parsed]

    if not enabled_entries:
        raise HTTPException(status_code=400, detail="没有启用的字幕条目可烧录")

    # 计算双向裁剪时间
    trim_start = None
    trim_end = None

    from ...pipeline.step7_subtitle_burn import _get_video_dimensions, _get_video_duration
    video_duration = _get_video_duration(video_path)

    first_start = min(e["startTime"] for e in enabled_entries)
    last_end = max(e["endTime"] for e in enabled_entries)

    # 开头裁剪：第一条启用字幕的 startTime
    if first_start > 0.5:
        trim_start = first_start

    # 结尾裁剪：最后一条启用字幕的 endTime
    if video_duration and last_end < video_duration - 0.5:
        trim_end = last_end

    logger.info(
        f"烧录裁剪计算: video_duration={video_duration}, "
        f"first_start={first_start:.2f}, last_end={last_end:.2f}, "
        f"trim_start={trim_start}, trim_end={trim_end}, "
        f"enabled={len(enabled_entries)}/{total_count}"
    )

    # 如果有开头裁剪，需要将所有字幕时间偏移
    if trim_start and trim_start > 0:
        for e in enabled_entries:
            e["startTime"] -= trim_start
            e["endTime"] -= trim_start

    # 生成仅启用条目的 SRT 内容
    srt_content = _entries_to_srt_from_dicts(enabled_entries)

    projects_dir = get_projects_directory()
    output_dir = projects_dir / project_id / "output" / "clips_with_subs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_name}.mp4"

    # 构建 style_overrides
    vid_w, vid_h = _get_video_dimensions(video_path)
    style_overrides = {}
    if request.custom_outline is not None:
        style_overrides['outline'] = request.custom_outline
    if request.custom_shadow is not None:
        style_overrides['shadow'] = request.custom_shadow
    if request.custom_bold is not None:
        style_overrides['bold'] = request.custom_bold
    if request.custom_color:
        style_overrides['color'] = request.custom_color
    if request.custom_outline_color:
        style_overrides['outline_color'] = request.custom_outline_color
    if request.custom_font_size_ratio is not None:
        style_overrides['font_size_ratio'] = request.custom_font_size_ratio

    # 异步分发 Celery 任务
    from ...tasks.subtitle import burn_subtitle_task
    task = burn_subtitle_task.delay(
        project_id, clip_id, srt_content,
        str(video_path), str(output_path),
        vid_w, vid_h, request.preset_id,
        style_overrides or None,
        trim_start,
        trim_end,
    )

    # 更新 clip burn_status
    try:
        from ...models.clip import Clip as ClipModel
        clip_record = db.query(ClipModel).filter(ClipModel.id == clip_id).first()
        if clip_record:
            clip_record.burn_status = "burning"
            clip_record.burn_task_id = task.id
            db.commit()
    except Exception as e:
        logger.warning(f"更新clip burn_status失败: {e}")

    return {"success": True, "task_id": task.id, "message": "烧录任务已提交"}


@router.get("/{project_id}/clips/{clip_id}/burn-status")
async def get_burn_status(project_id: str, clip_id: str, db: Session = Depends(get_db)):
    """查询字幕烧录状态"""
    from ...models.clip import Clip as ClipModel
    clip = db.query(ClipModel).filter(ClipModel.id == clip_id).first()
    return {"burn_status": clip.burn_status if clip else "none"}


# ─── 旧版端点（保留兼容） ────────────────────────────────

@router.post("/{project_id}/clips/{clip_id}/edit")
async def edit_clip_by_subtitles(
    project_id: str,
    clip_id: str,
    request: SubtitleEditRequest,
    db: Session = Depends(get_db)
):
    """基于字幕删除编辑视频片段"""
    try:
        from ...utils.subtitle_processor import SubtitleProcessor
        from ...utils.video_editor import VideoEditor
        from ...core.shared_config import CLIPS_DIR, COLLECTIONS_DIR

        subtitle_processor = SubtitleProcessor()
        video_editor = VideoEditor(clips_dir=str(CLIPS_DIR), collections_dir=str(COLLECTIONS_DIR))
        project_service = ProjectService(db)

        project = project_service.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

        from ...models.clip import Clip
        clip = db.query(Clip).filter(Clip.id == clip_id, Clip.project_id == project_id).first()
        if not clip:
            raise HTTPException(status_code=404, detail="片段不存在")

        projects_dir = get_projects_directory()
        project_dir = projects_dir / project_id

        video_files = list(project_dir.glob("raw/*.mp4"))
        if not video_files:
            raise HTTPException(status_code=404, detail="原始视频文件不存在")
        original_video = video_files[0]

        srt_file = project_dir / "raw" / "input.srt"
        if not srt_file.exists():
            raise HTTPException(status_code=404, detail="字幕文件不存在")

        subtitle_data = subtitle_processor.parse_srt_to_word_level(srt_file)

        if isinstance(clip.start_time, int):
            clip_start = clip.start_time
        else:
            clip_start = subtitle_processor._srt_time_to_seconds(
                subtitle_processor._seconds_to_srt_time_object(clip.start_time)
            )

        if isinstance(clip.end_time, int):
            clip_end = clip.end_time
        else:
            clip_end = subtitle_processor._srt_time_to_seconds(
                subtitle_processor._seconds_to_srt_time_object(clip.end_time)
            )

        clip_subtitles = [
            seg for seg in subtitle_data
            if seg['startTime'] >= clip_start and seg['endTime'] <= clip_end
        ]

        validation = video_editor.validate_edit_operations(
            clip_subtitles, request.deleted_segments
        )

        if not validation['valid']:
            raise HTTPException(status_code=400, detail=validation['error'])

        output_dir = project_dir / "edited_clips"
        output_dir.mkdir(exist_ok=True)

        edited_video_name = f"{clip_id}_edited.mp4"
        edited_video_path = output_dir / edited_video_name

        edit_result = video_editor.edit_video_by_subtitle_deletion(
            original_video,
            clip_subtitles,
            request.deleted_segments,
            edited_video_path
        )

        if not edit_result['success']:
            raise HTTPException(status_code=500, detail=f"视频编辑失败: {edit_result['error']}")

        edited_srt_path = output_dir / f"{clip_id}_edited.srt"
        subtitle_processor.export_edited_srt(
            clip_subtitles,
            request.deleted_segments,
            edited_srt_path
        )

        return {
            "success": True,
            "message": "视频编辑成功",
            "edited_video_path": str(edited_video_path),
            "deleted_duration": edit_result['totalDeletedDuration'],
            "final_duration": edit_result['finalDuration'],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"编辑视频片段失败: {e}")
        raise HTTPException(status_code=500, detail=f"编辑视频片段失败: {str(e)}")


@router.get("/{project_id}/clips/{clip_id}/edited-video")
async def get_edited_video(project_id: str, clip_id: str):
    """获取编辑后的视频文件"""
    try:
        projects_dir = get_projects_directory()
        edited_video_path = projects_dir / project_id / "edited_clips" / f"{clip_id}_edited.mp4"

        if not edited_video_path.exists():
            raise HTTPException(status_code=404, detail="编辑后的视频文件不存在")

        return FileResponse(
            path=str(edited_video_path),
            media_type="video/mp4",
            filename=f"{clip_id}_edited.mp4"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取编辑后的视频失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取编辑后的视频失败: {str(e)}")


@router.post("/{project_id}/clips/{clip_id}/preview")
async def create_edit_preview(
    project_id: str,
    clip_id: str,
    request: EditPreviewRequest,
    db: Session = Depends(get_db)
):
    """创建编辑预览片段"""
    try:
        from ...utils.subtitle_processor import SubtitleProcessor
        from ...utils.video_editor import VideoEditor
        from ...core.shared_config import CLIPS_DIR, COLLECTIONS_DIR

        subtitle_processor = SubtitleProcessor()
        video_editor = VideoEditor(clips_dir=str(CLIPS_DIR), collections_dir=str(COLLECTIONS_DIR))
        project_service = ProjectService(db)

        project = project_service.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

        from ...models.clip import Clip
        clip = db.query(Clip).filter(Clip.id == clip_id, Clip.project_id == project_id).first()
        if not clip:
            raise HTTPException(status_code=404, detail="片段不存在")

        projects_dir = get_projects_directory()
        project_dir = projects_dir / project_id

        video_files = list(project_dir.glob("raw/*.mp4"))
        if not video_files:
            raise HTTPException(status_code=404, detail="原始视频文件不存在")
        original_video = video_files[0]

        srt_file = project_dir / "raw" / "input.srt"
        if not srt_file.exists():
            raise HTTPException(status_code=404, detail="字幕文件不存在")

        subtitle_data = subtitle_processor.parse_srt_to_word_level(srt_file)

        if isinstance(clip.start_time, int):
            clip_start = clip.start_time
        else:
            clip_start = subtitle_processor._srt_time_to_seconds(
                subtitle_processor._seconds_to_srt_time_object(clip.start_time)
            )

        if isinstance(clip.end_time, int):
            clip_end = clip.end_time
        else:
            clip_end = subtitle_processor._srt_time_to_seconds(
                subtitle_processor._seconds_to_srt_time_object(clip.end_time)
            )

        clip_subtitles = [
            seg for seg in subtitle_data
            if seg['startTime'] >= clip_start and seg['endTime'] <= clip_end
        ]

        preview_dir = project_dir / "edit_previews" / clip_id
        preview_dir.mkdir(parents=True, exist_ok=True)

        preview_files = video_editor.create_preview_clips(
            original_video,
            clip_subtitles,
            request.deleted_segments,
            preview_dir
        )

        return {
            "success": True,
            "preview_files": [str(f) for f in preview_files],
            "count": len(preview_files)
        }

    except Exception as e:
        logger.error(f"创建编辑预览失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建编辑预览失败: {str(e)}")


@router.get("/{project_id}/clips/{clip_id}/preview/{segment_id}")
async def get_preview_segment(project_id: str, clip_id: str, segment_id: str):
    """获取预览片段文件"""
    try:
        projects_dir = get_projects_directory()
        preview_file = projects_dir / project_id / "edit_previews" / clip_id / f"preview_{segment_id}.mp4"

        if not preview_file.exists():
            raise HTTPException(status_code=404, detail="预览文件不存在")

        return FileResponse(
            path=str(preview_file),
            media_type="video/mp4",
            filename=f"preview_{segment_id}.mp4"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取预览文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取预览文件失败: {str(e)}")

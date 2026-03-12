"""
数据同步服务 - 将处理结果同步到数据库
"""

import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from sqlalchemy.orm import Session
from backend.models.clip import Clip, ClipStatus
from backend.models.project import Project, ProjectStatus, ProjectType
from backend.models.task import Task, TaskStatus, TaskType
from datetime import datetime

logger = logging.getLogger(__name__)


class DataSyncService:
    """数据同步服务"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def sync_all_projects_from_filesystem(self, data_dir: Path) -> Dict[str, Any]:
        """从文件系统同步所有项目到数据库"""
        try:
            logger.info(f"开始从文件系统同步所有项目: {data_dir}")
            
            projects_dir = data_dir / "projects"
            if not projects_dir.exists():
                logger.warning(f"项目目录不存在: {projects_dir}")
                return {"success": False, "error": "项目目录不存在"}
            
            synced_projects = []
            failed_projects = []
            
            # 遍历所有项目目录
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir() and not project_dir.name.startswith('.'):
                    project_id = project_dir.name
                    try:
                        result = self.sync_project_from_filesystem(project_id, project_dir)
                        if result["success"]:
                            synced_projects.append(project_id)
                        else:
                            failed_projects.append({"project_id": project_id, "error": result.get("error")})
                    except Exception as e:
                        logger.error(f"同步项目 {project_id} 失败: {str(e)}")
                        failed_projects.append({"project_id": project_id, "error": str(e)})
            
            logger.info(f"同步完成: 成功 {len(synced_projects)} 个, 失败 {len(failed_projects)} 个")
            
            return {
                "success": True,
                "synced_projects": synced_projects,
                "failed_projects": failed_projects,
                "total_synced": len(synced_projects),
                "total_failed": len(failed_projects)
            }
            
        except Exception as e:
            logger.error(f"同步所有项目失败: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def sync_project_from_filesystem(self, project_id: str, project_dir: Path) -> Dict[str, Any]:
        """从文件系统同步单个项目到数据库"""
        try:
            logger.info(f"开始同步项目: {project_id}")
            
            # 检查项目是否已存在于数据库
            existing_project = self.db.query(Project).filter(Project.id == project_id).first()
            if existing_project:
                logger.info(f"项目 {project_id} 已存在于数据库，继续同步切片数据")
            else:
                # 读取项目元数据
                project_metadata = self._read_project_metadata(project_dir)
                if not project_metadata:
                    logger.warning(f"项目 {project_id} 没有元数据文件，创建基础项目记录")
                    project_metadata = {
                        "project_name": f"项目_{project_id[:8]}",
                        "created_at": datetime.now().isoformat(),
                        "status": "pending"
                    }
                
                # 创建项目记录
                project = Project(
                    id=project_id,
                    name=project_metadata.get("project_name", f"项目_{project_id[:8]}"),
                    description=project_metadata.get("description", ""),
                    project_type=ProjectType.KNOWLEDGE,  # 默认类型
                    status=ProjectStatus.PENDING,
                    processing_config=project_metadata.get("processing_config", {}),
                    project_metadata=project_metadata
                )
                
                self.db.add(project)
                self.db.commit()
                self.db.refresh(project)
                
                logger.info(f"项目 {project_id} 同步到数据库成功")
            

            
            # 同步切片数据
            clips_count = self._sync_clips_from_filesystem(project_id, project_dir)

            # 检查项目是否已完成处理，更新项目状态
            self._update_project_status_if_completed(project_id, project_dir)

            return {
                "success": True,
                "project_id": project_id,
                "clips_synced": clips_count,
            }
            
        except Exception as e:
            logger.error(f"同步项目 {project_id} 失败: {str(e)}")
            self.db.rollback()
            return {"success": False, "error": str(e)}
    
    def _read_project_metadata(self, project_dir: Path) -> Optional[Dict[str, Any]]:
        """读取项目元数据"""
        metadata_files = [
            project_dir / "project.json",
            project_dir / "metadata.json",
            project_dir / "info.json"
        ]
        
        for metadata_file in metadata_files:
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"读取元数据文件失败 {metadata_file}: {e}")
        
        return None
    
    def _sync_clips_from_filesystem(self, project_id: str, project_dir: Path) -> int:
        """从文件系统同步切片数据"""
        try:
            # 查找切片数据文件
            clips_files = [
                project_dir / "metadata" / "clips_metadata.json",
                project_dir / "clips_metadata.json",
            ]

            clips_data = None
            for clips_file in clips_files:
                if clips_file.exists():
                    try:
                        with open(clips_file, 'r', encoding='utf-8') as f:
                            clips_data = json.load(f)
                        logger.info(f"成功读取切片文件: {clips_file}, 数据长度: {len(clips_data) if isinstance(clips_data, list) else 'not list'}")
                        break
                    except Exception as e:
                        logger.warning(f"读取切片文件失败 {clips_file}: {e}")
            
            if not clips_data:
                logger.info(f"项目 {project_id} 没有找到切片数据")
                return 0
            
            # 确保clips_data是列表
            if isinstance(clips_data, dict) and "clips" in clips_data:
                clips_data = clips_data["clips"]
            elif not isinstance(clips_data, list):
                logger.warning(f"项目 {project_id} 切片数据格式不正确")
                return 0
            
            synced_count = 0
            updated_count = 0
            for clip_data in clips_data:
                try:
                    # 检查切片是否已存在
                    existing_clip = self.db.query(Clip).filter(
                        Clip.project_id == project_id,
                        Clip.title == clip_data.get("generated_title", clip_data.get("title", ""))
                    ).first()
                    
                    # 解析视频路径：优先用 clips_metadata.json 中已记录的实际路径，
                    # 否则按序号 glob 磁盘查找，最后才构造路径
                    clip_id = clip_data.get('id', str(synced_count + 1))
                    from ..core.path_utils import get_project_directory
                    project_clips_dir = get_project_directory(project_id) / "output" / "clips"
                    project_clips_dir.mkdir(parents=True, exist_ok=True)

                    def _resolve_video_path(cid, data):
                        # 1. metadata 里已有实际路径
                        if data.get('video_path') and Path(data['video_path']).exists():
                            return data['video_path']
                        # 2. glob 按序号找
                        for f in project_clips_dir.glob(f"{cid}_*.mp4"):
                            return str(f)
                        return None

                    if existing_clip:
                        video_path = _resolve_video_path(clip_id, clip_data)
                        if video_path:
                            existing_clip.video_path = video_path
                        if existing_clip.tags is None:
                            existing_clip.tags = []
                        updated_count += 1
                        continue

                    # 转换时间格式
                    start_time = self._convert_time_to_seconds(clip_data.get('start_time', '00:00:00'))
                    end_time = self._convert_time_to_seconds(clip_data.get('end_time', '00:00:00'))
                    duration = end_time - start_time

                    video_path = _resolve_video_path(clip_id, clip_data)
                    
                    # 创建切片记录
                    clip = Clip(
                        project_id=project_id,
                        title=clip_data.get('generated_title', clip_data.get('title', clip_data.get('outline', ''))),
                        description=clip_data.get('recommend_reason', ''),
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        score=clip_data.get('final_score', 0.0),
                        video_path=video_path,
                        tags=[],  # 确保tags是空列表而不是null
                        clip_metadata=clip_data,
                        status=ClipStatus.COMPLETED
                    )
                    
                    self.db.add(clip)
                    synced_count += 1
                    
                except Exception as e:
                    logger.error(f"同步切片失败: {e}")
                    continue
            
            self.db.commit()
            logger.info(f"项目 {project_id} 同步了 {synced_count} 个切片，更新了 {updated_count} 个切片")
            return synced_count
            
        except Exception as e:
            logger.error(f"同步切片数据失败: {str(e)}")
            return 0
    
    def sync_project_data(self, project_id: str, project_dir: Path) -> Dict[str, Any]:
        """同步项目数据到数据库"""
        try:
            logger.info(f"开始同步项目数据: {project_id}")
            
            # 同步clips数据
            clips_count = self._sync_clips(project_id, project_dir)

            # 更新项目统计信息
            self._update_project_stats(project_id, clips_count)

            logger.info(f"项目数据同步完成: {project_id}, clips: {clips_count}")

            return {
                "success": True,
                "clips_synced": clips_count,
            }
            
        except Exception as e:
            logger.error(f"同步项目数据失败: {str(e)}")
            raise
    
    def _sync_clips(self, project_id: str, project_dir: Path) -> int:
        """同步clips数据"""
        clips_file = project_dir / "step4_titles.json"
        if not clips_file.exists():
            logger.warning(f"Clips文件不存在: {clips_file}")
            return 0
        
        try:
            with open(clips_file, 'r', encoding='utf-8') as f:
                clips_data = json.load(f)
            
            clips_count = 0
            for clip_data in clips_data:
                # 检查是否已存在
                existing_clip = self.db.query(Clip).filter(
                    Clip.project_id == project_id,
                    Clip.title == clip_data.get("generated_title")
                ).first()
                
                if existing_clip:
                    logger.info(f"Clip已存在，跳过: {clip_data.get('generated_title')}")
                    continue
                
                # 创建新的clip记录
                clip = Clip(
                    project_id=project_id,
                    title=clip_data.get("generated_title", ""),
                    description=clip_data.get("outline", ""),
                    start_time=self._parse_time(clip_data.get("start_time", "00:00:00")),
                    end_time=self._parse_time(clip_data.get("end_time", "00:00:00")),
                    duration=self._calculate_duration(
                        clip_data.get("start_time", "00:00:00"),
                        clip_data.get("end_time", "00:00:00")
                    ),
                    score=clip_data.get("final_score", 0.0),
                    status=ClipStatus.COMPLETED,
                    tags=[],
                    clip_metadata={
                        "outline": clip_data.get("outline"),
                        "content": clip_data.get("content", []),
                        "recommend_reason": clip_data.get("recommend_reason"),
                        "chunk_index": clip_data.get("chunk_index"),
                        "original_id": clip_data.get("id")
                    }
                )
                
                self.db.add(clip)
                clips_count += 1
                logger.info(f"创建clip: {clip.title}")
            
            self.db.commit()
            logger.info(f"同步了 {clips_count} 个clips")
            return clips_count
            
        except Exception as e:
            logger.error(f"同步clips失败: {str(e)}")
            self.db.rollback()
            raise
    
    def _update_project_stats(self, project_id: str, clips_count: int):
        """更新项目统计信息"""
        try:
            project = self.db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.total_clips = clips_count
                self.db.commit()
                logger.info(f"更新项目统计: clips={clips_count}")
        except Exception as e:
            logger.error(f"更新项目统计失败: {str(e)}")
    
    def _parse_time(self, time_str: str) -> float:
        """解析时间字符串为秒数"""
        try:
            if ',' in time_str:
                time_str = time_str.replace(',', '.')
            
            parts = time_str.split(':')
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            else:
                return 0.0
        except Exception:
            return 0.0
    
    def _calculate_duration(self, start_time: str, end_time: str) -> float:
        """计算持续时间"""
        start_seconds = self._parse_time(start_time)
        end_seconds = self._parse_time(end_time)
        return end_seconds - start_seconds

    def _convert_time_to_seconds(self, time_str: str) -> int:
        """将时间字符串转换为秒数"""
        try:
            # 处理格式 "00:00:00,120" 或 "00:00:00.120"
            time_str = time_str.replace(',', '.')
            parts = time_str.split(':')
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds_parts = parts[2].split('.')
            seconds = int(seconds_parts[0])
            milliseconds = int(seconds_parts[1]) if len(seconds_parts) > 1 else 0
            
            total_seconds = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
            return int(total_seconds)
        except Exception as e:
            logger.error(f"时间转换失败: {time_str}, 错误: {e}")
            return 0
    
    def _update_project_status_if_completed(self, project_id: str, project_dir: Path):
        """检查项目是否已完成处理，如果是则更新状态为completed"""
        try:
            # 检查是否有step6_video_output.json文件，这是处理完成的标志
            step6_output_file = project_dir / "output" / "step6_video_output.json"
            
            if step6_output_file.exists():
                # 获取项目记录
                project = self.db.query(Project).filter(Project.id == project_id).first()
                if project and project.status != ProjectStatus.COMPLETED:
                    # 读取step6输出文件获取统计信息
                    try:
                        with open(step6_output_file, 'r', encoding='utf-8') as f:
                            step6_output = json.load(f)
                        
                        # 更新项目状态和统计信息
                        project.status = ProjectStatus.COMPLETED
                        project.total_clips = step6_output.get("clips_count", 0)
                        project.completed_at = datetime.now()

                        self.db.commit()
                        logger.info(f"项目 {project_id} 状态已更新为已完成，切片数: {project.total_clips}")
                        
                    except Exception as e:
                        logger.error(f"读取step6输出文件失败: {e}")
                        # 即使读取失败，也标记为已完成
                        project.status = ProjectStatus.COMPLETED
                        project.completed_at = datetime.now()
                        self.db.commit()
                        logger.info(f"项目 {project_id} 状态已更新为已完成（无统计信息）")
                        
        except Exception as e:
            logger.error(f"更新项目状态失败: {e}")

#!/usr/bin/env python3
"""
按照原有架构执行真实流水线的脚本
使用PipelineAdapter和原有的流水线步骤
"""

import sys
import os
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ..core.database import SessionLocal
from ..models.project import Project, ProjectStatus
from ..models.task import Task, TaskStatus
from ..services.pipeline_adapter import create_pipeline_adapter_sync
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def execute_real_pipeline(project_id: str):
    """按照原有架构执行真实流水线"""
    
    logger.info(f"开始执行项目 {project_id} 的真实流水线")
    
    try:
        # 创建数据库会话
        db = SessionLocal()
        
        try:
            # 验证项目是否存在
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise ValueError(f"项目 {project_id} 不存在")
            
            logger.info(f"验证项目存在: {project.name}")
            
            # 创建任务记录
            task = Task(
                name=f"真实流水线处理",
                description=f"使用原有架构处理项目 {project_id}",
                task_type="VIDEO_PROCESSING",
                project_id=project_id,
                status=TaskStatus.RUNNING,
                progress=0,
                current_step="初始化",
                total_steps=6
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            
            logger.info(f"任务记录已创建: {task.id}")
            
            # 准备文件路径
            data_root = project_root / "data" / "projects" / project_id
            input_video_path = data_root / "raw" / "input.mp4"
            input_srt_path = data_root / "raw" / "input.srt"
            
            # 验证文件存在
            if not input_video_path.exists():
                raise FileNotFoundError(f"视频文件不存在: {input_video_path}")
            if not input_srt_path.exists():
                raise FileNotFoundError(f"字幕文件不存在: {input_srt_path}")
            
            logger.info(f"文件路径验证成功:")
            logger.info(f"  视频: {input_video_path}")
            logger.info(f"  字幕: {input_srt_path}")
            
            # 创建Pipeline适配器
            pipeline_adapter = create_pipeline_adapter_sync(db, str(task.id), project_id)
            
            # 验证流水线前置条件
            logger.info("验证流水线前置条件...")
            errors = pipeline_adapter.validate_pipeline_prerequisites()
            if errors:
                error_msg = "; ".join(errors)
                logger.error(f"流水线前置条件验证失败: {error_msg}")
                raise ValueError(f"流水线前置条件验证失败: {error_msg}")
            
            logger.info("流水线前置条件验证通过")
            
            # 执行完整的流水线处理
            logger.info("开始执行完整流水线...")
            result = pipeline_adapter.process_project_sync(
                project_id=project_id,
                input_video_path=str(input_video_path),
                input_srt_path=str(input_srt_path)
            )
            
            # 检查处理结果
            if result.get('status') == 'failed':
                error_msg = result.get('message', '处理失败')
                logger.error(f"流水线处理失败: {error_msg}")
                
                # 更新任务状态为失败
                task.status = TaskStatus.FAILED
                task.error_message = error_msg
                db.commit()
                
                return {
                    "success": False,
                    "error": error_msg,
                    "result": result
                }
            else:
                # 处理成功
                logger.info("🎉 流水线处理成功！")
                logger.info(f"处理结果: {result}")
                
                # 更新任务状态为完成
                task.status = TaskStatus.COMPLETED
                task.progress = 100
                task.current_step = "处理完成"
                db.commit()
                
                return {
                    "success": True,
                    "result": result,
                    "message": "流水线处理完成"
                }
                
        finally:
            db.close()
            
    except Exception as e:
        error_msg = f"执行流水线失败: {str(e)}"
        logger.error(error_msg)
        
        # 尝试更新任务状态
        try:
            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.project_id == project_id).order_by(Task.created_at.desc()).first()
                if task:
                    task.status = TaskStatus.FAILED
                    task.error_message = error_msg
                    db.commit()
            finally:
                db.close()
        except Exception as db_error:
            logger.error(f"更新任务状态失败: {db_error}")
        
        return {
            "success": False,
            "error": error_msg
        }

async def main():
    """主函数"""
    if len(sys.argv) != 2:
        print("使用方法: python execute_real_pipeline.py <project_id>")
        sys.exit(1)
    
    project_id = sys.argv[1]
    
    result = await execute_real_pipeline(project_id)
    
    if result["success"]:
        print(f"✅ 流水线执行成功！")
        print(f"📊 结果: {result['result']}")
    else:
        print(f"❌ 流水线执行失败: {result['error']}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

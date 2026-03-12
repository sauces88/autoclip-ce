"""
最小化Celery应用配置
避免所有导入问题，只提供基本的任务处理功能
"""

import os
import sys
from pathlib import Path
from celery import Celery

# 创建Celery应用
celery_app = Celery('autoclip')

# 基本配置
celery_app.conf.update(
    # 序列化格式
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # Redis配置
    broker_url='redis://localhost:6379/0',
    result_backend='redis://localhost:6379/0',
    
    # 时区
    timezone='Asia/Shanghai',
    enable_utc=True,
    
    # 任务配置
    task_always_eager=False,
    task_eager_propagates=True,
    
    # 工作进程配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_disable_rate_limits=True,
    
    # 结果配置
    result_expires=3600,
    task_ignore_result=False,
    
    # 禁用自动发现
    autodiscover_tasks=False,
)

# 手动注册任务
@celery_app.task(bind=True, name='tasks.processing.process_video_pipeline')
def process_video_pipeline(self, project_id: str, input_video_path: str, input_srt_path: str):
    """视频处理流水线任务"""
    print(f"🎬 开始处理项目: {project_id}")
    print(f"📹 视频路径: {input_video_path}")
    print(f"📝 字幕路径: {input_srt_path}")
    
    # 模拟处理过程
    import time
    steps = [
        "大纲提取",
        "时间定位",
        "内容评分",
        "标题生成",
        "视频切割"
    ]

    for i, step in enumerate(steps):
        progress = (i + 1) * 20  # 每步20%
        print(f"📊 步骤 {i+1}/5: {step} - {progress}%")

        # 更新任务状态
        self.update_state(
            state='PROGRESS',
            meta={
                'current': i + 1,
                'total': 5,
                'status': f'正在执行: {step}',
                'progress': progress
            }
        )
        
        time.sleep(2)  # 模拟处理时间
    
    print(f"✅ 项目 {project_id} 处理完成")
    return {
        "success": True,
        "project_id": project_id,
        "message": "视频处理完成",
        "steps": steps
    }

@celery_app.task(bind=True, name='tasks.processing.process_single_step')
def process_single_step(self, project_id: str, step: str, config: dict):
    """单个步骤处理任务"""
    print(f"🔧 开始处理项目 {project_id} 的步骤: {step}")
    
    # 模拟处理过程
    import time
    time.sleep(3)
    
    print(f"✅ 步骤 {step} 处理完成")
    return {
        "success": True,
        "project_id": project_id,
        "step": step,
        "message": f"步骤 {step} 处理完成"
    }

# 兼容性任务名称
@celery_app.task(bind=True, name='backend.tasks.processing.process_video_pipeline')
def backend_process_video_pipeline(self, project_id: str, input_video_path: str, input_srt_path: str):
    """后端视频处理流水线任务（兼容性）"""
    return process_video_pipeline(self, project_id, input_video_path, input_srt_path)

@celery_app.task(bind=True, name='backend.tasks.processing.process_single_step')
def backend_process_single_step(self, project_id: str, step: str, config: dict):
    """后端单个步骤处理任务（兼容性）"""
    return process_single_step(self, project_id, step, config)

if __name__ == '__main__':
    celery_app.start()


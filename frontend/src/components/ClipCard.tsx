import React, { useState, useEffect, useRef } from 'react'
import { Card, Button, Tooltip, Modal, message } from 'antd'
import { PlayCircleOutlined, FolderOpenOutlined, ClockCircleOutlined, StarFilled, EditOutlined, EyeOutlined, ReloadOutlined, LoadingOutlined } from '@ant-design/icons'
import ReactPlayer from 'react-player'
import { Clip } from '../store/useProjectStore'
import SubtitleEditor from './SubtitleEditor'
import { SubtitleSegment } from '../types/subtitle'
import EditableTitle from './EditableTitle'
import { subtitleEditorApi } from '../services/subtitleEditorApi'
import './ClipCard.css'

interface ClipCardProps {
  clip: Clip
  videoUrl?: string
  onDownload: (clipId: string) => void
  projectId?: string
  onClipUpdate?: (clipId: string, updates: Partial<Clip>) => void
}

const ClipCard: React.FC<ClipCardProps> = ({
  clip,
  videoUrl,
  projectId,
  onClipUpdate
}) => {
  const [showPlayer, setShowPlayer] = useState(false)
  const [videoThumbnail, setVideoThumbnail] = useState<string | null>(null)
  const [showSubtitleEditor, setShowSubtitleEditor] = useState(false)
  const [subtitleData, setSubtitleData] = useState<SubtitleSegment[]>([])
  const [showCoverModal, setShowCoverModal] = useState(false)
  const [coverUrl, setCoverUrl] = useState<string | null>(null)
  const [regeneratingCover, setRegeneratingCover] = useState(false)
  const [burnStatus, setBurnStatus] = useState<string>(clip.burn_status || 'none')
  const playerRef = useRef<ReactPlayer>(null)

  // 轮询烧录状态
  useEffect(() => {
    if (burnStatus !== 'burning' || !projectId) return
    const timer = setInterval(async () => {
      try {
        const res = await subtitleEditorApi.getBurnStatus(projectId, clip.id)
        if (res.burn_status !== 'burning') {
          setBurnStatus(res.burn_status)
          if (res.burn_status === 'done') {
            message.success(`「${clip.title || clip.generated_title}」字幕烧录完成`)
          } else if (res.burn_status === 'failed') {
            message.error(`「${clip.title || clip.generated_title}」字幕烧录失败`)
          }
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => clearInterval(timer)
  }, [burnStatus, projectId, clip.id, clip.title, clip.generated_title])

  // 同步 prop 变化
  useEffect(() => {
    if (clip.burn_status) setBurnStatus(clip.burn_status)
  }, [clip.burn_status])

  // 生成视频缩略图
  useEffect(() => {
    if (videoUrl) {
      generateThumbnail()
    }
  }, [videoUrl])

  const generateThumbnail = () => {
    if (!videoUrl) return

    const video = document.createElement('video')
    video.crossOrigin = 'anonymous'
    video.currentTime = 1 // 获取第1秒的帧作为缩略图

    video.onloadeddata = () => {
      const canvas = document.createElement('canvas')
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
      ctx.drawImage(video, 0, 0)

      const thumbnail = canvas.toDataURL('image/jpeg', 0.8)
      setVideoThumbnail(thumbnail)
    }

    video.src = videoUrl
  }

  const handleOpenFolder = async (folderType: 'video' | 'cover' = 'video') => {
    if (!projectId) return
    try {
      const resp = await fetch(`/api/v1/projects/${projectId}/clips/${clip.id}/open-folder?folder_type=${folderType}`, { method: 'POST' })
      const data = await resp.json()
      if (!data.success) {
        message.error(data.detail || '打开文件夹失败')
      }
    } catch {
      message.error('打开文件夹失败')
    }
  }

  const handleClosePlayer = () => {
    setShowPlayer(false)
  }

  const handleOpenSubtitleEditor = () => {
    setShowPlayer(false)
    setShowSubtitleEditor(true)
  }

  const handleSubtitleEditorClose = () => {
    setShowSubtitleEditor(false)
    setSubtitleData([])
    // 字幕编辑器关闭后检查烧录状态（可能刚提交了烧录任务）
    if (projectId) {
      subtitleEditorApi.getBurnStatus(projectId, clip.id).then(res => {
        if (res.burn_status && res.burn_status !== burnStatus) {
          setBurnStatus(res.burn_status)
        }
      }).catch(() => {})
    }
  }


  const handleTitleUpdate = (newTitle: string) => {
    // 更新本地状态
    onClipUpdate?.(clip.id, { title: newTitle })
  }


  const formatDuration = (seconds: number) => {
    if (!seconds || seconds <= 0) return '00:00'
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = Math.floor(seconds % 60)
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  const calculateDuration = (startTime: string, endTime: string): number => {
    if (!startTime || !endTime) return 0

    try {
      // 解析时间格式 "HH:MM:SS,mmm" 或 "HH:MM:SS.mmm"
      const parseTime = (timeStr: string): number => {
        const normalized = timeStr.replace(',', '.')
        const parts = normalized.split(':')
        if (parts.length !== 3) return 0

        const hours = parseInt(parts[0]) || 0
        const minutes = parseInt(parts[1]) || 0
        const seconds = parseFloat(parts[2]) || 0

        return hours * 3600 + minutes * 60 + seconds
      }

      const start = parseTime(startTime)
      const end = parseTime(endTime)

      return Math.max(0, end - start)
    } catch (error) {
      console.error('Error calculating duration:', error)
      return 0
    }
  }

  const getDuration = () => {
    if (!clip.start_time || !clip.end_time) return '00:00'
    const start = clip.start_time.replace(',', '.')
    const end = clip.end_time.replace(',', '.')
    return `${start.substring(0, 8)} - ${end.substring(0, 8)}`
  }

  const getScoreColor = (score: number) => {
    // 根据分数区间设置不同的颜色
    if (score >= 0.9) return '#52c41a' // 绿色 - 优秀
    if (score >= 0.8) return '#1890ff' // 蓝色 - 良好
    if (score >= 0.7) return '#faad14' // 橙色 - 一般
    if (score >= 0.6) return '#ff7a45' // 红橙色 - 较差
    return '#ff4d4f' // 红色 - 差
  }


  // 获取要显示的简介内容
  const getDisplayContent = () => {
    // 优先显示推荐理由（这是AI生成的内容要点）
    if (clip.recommend_reason && clip.recommend_reason.trim()) {
      return clip.recommend_reason
    }

    // 如果没有推荐理由，尝试从content中获取非转写文本的内容要点
    if (clip.content && clip.content.length > 0) {
      // 过滤掉可能是转写文本的内容（通常转写文本很长且包含标点符号）
      const contentPoints = clip.content.filter(item => {
        const text = item.trim()
        // 如果文本长度超过100字符或包含大量标点符号，可能是转写文本
        if (text.length > 100) return false
        if (text.split(/[，。！？；：""''（）【】]/).length > 3) return false
        return true
      })

      if (contentPoints.length > 0) {
        return contentPoints.join(' ')
      }
    }

    // 最后回退到outline（大纲）
    if (clip.outline && clip.outline.trim()) {
      return clip.outline
    }

    return '暂无内容要点'
  }

  const textRef = useRef<HTMLDivElement>(null)

  return (
    <>
      <Card
          className="clip-card"
          hoverable
          style={{
            height: '380px',
            borderRadius: '16px',
            border: '1px solid #303030',
            background: 'linear-gradient(135deg, #1f1f1f 0%, #2a2a2a 100%)',
            overflow: 'hidden',
            cursor: 'pointer'
          }}
          styles={{
            body: {
              padding: 0,
            },
          }}
          cover={
            <div
              style={{
                height: '200px',
                background: videoThumbnail
                  ? `url(${videoThumbnail}) center/cover`
                  : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                position: 'relative',
                cursor: 'pointer',
                overflow: 'hidden'
              }}
              onClick={() => setShowPlayer(true)}
            >
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  background: 'rgba(0,0,0,0.4)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  opacity: 0,
                  transition: 'opacity 0.3s ease'
                }}
                className="video-overlay"
              >
                <PlayCircleOutlined style={{ fontSize: '40px', color: 'white' }} />
              </div>

              {/* 右上角推荐分数 */}
              <div
                style={{
                  position: 'absolute',
                  top: '12px',
                  right: '12px',
                  background: getScoreColor(clip.final_score),
                  color: 'white',
                  padding: '4px 8px',
                  borderRadius: '8px',
                  fontSize: '12px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}
              >
                <StarFilled style={{ fontSize: '12px' }} />
                {(clip.final_score * 100).toFixed(0)}分
              </div>

              {/* 左下角时间区间 */}
              <div
                style={{
                  position: 'absolute',
                  bottom: '12px',
                  left: '12px',
                  background: 'rgba(0,0,0,0.7)',
                  color: 'white',
                  padding: '4px 8px',
                  borderRadius: '8px',
                  fontSize: '12px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}
              >
                <ClockCircleOutlined style={{ fontSize: '12px' }} />
                {getDuration()}
              </div>

              {/* 右下角视频时长 */}
              <div
                style={{
                  position: 'absolute',
                  bottom: '12px',
                  right: '12px',
                  background: 'rgba(0,0,0,0.7)',
                  color: 'white',
                  padding: '4px 8px',
                  borderRadius: '8px',
                  fontSize: '12px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}
              >
                {formatDuration(calculateDuration(clip.start_time, clip.end_time))}
              </div>

              {/* 烧录状态遮罩 */}
              {burnStatus === 'burning' && (
                <div style={{
                  position: 'absolute',
                  inset: 0,
                  background: 'rgba(0,0,0,0.65)',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8,
                  zIndex: 10,
                }}>
                  <LoadingOutlined style={{ fontSize: 28, color: '#4facfe' }} spin />
                  <span style={{ color: '#fff', fontSize: 13, fontWeight: 500 }}>烧录中...</span>
                </div>
              )}
            </div>
          }
        >
          <div style={{
            padding: '16px',
            height: '180px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between'
          }}>
            {/* 内容区域 - 固定高度 */}
            <div style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              minHeight: 0 // 允许flex子项收缩
            }}>
              {/* 标题区域 - 固定高度 */}
              <div style={{
                height: '44px',
                marginBottom: '8px',
                display: 'flex',
                alignItems: 'flex-start'
              }}>
                <EditableTitle
                  title={clip.title || clip.generated_title || '未命名片段'}
                  clipId={clip.id}
                  onTitleUpdate={handleTitleUpdate}
                  style={{
                    fontSize: '16px',
                    fontWeight: 600,
                    lineHeight: '1.4',
                    color: '#ffffff',
                    width: '100%'
                  }}
                />
              </div>

              {/* 内容要点 - 固定高度 */}
              <div style={{
                height: '58px',
                marginBottom: '12px',
                display: 'flex',
                alignItems: 'flex-start'
              }}>
                <Tooltip
                  title={getDisplayContent()}
                  placement="top"
                  styles={{ root: { maxWidth: '300px' } }}
                  mouseEnterDelay={0.5}
                >
                  <div
                    ref={textRef}
                    style={{
                      fontSize: '13px',
                      display: '-webkit-box',
                      WebkitLineClamp: 3,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                      lineHeight: '1.5',
                      color: '#b0b0b0',
                      cursor: 'pointer',
                      wordBreak: 'break-word',
                      textOverflow: 'ellipsis',
                      width: '100%'
                    }}
                  >
                    {getDisplayContent()}
                  </div>
                </Tooltip>
              </div>
            </div>

            {/* 操作按钮 - 固定在底部 */}
            <div style={{
              display: 'flex',
              gap: '8px',
              height: '28px',
              alignItems: 'center',
              marginTop: 'auto'
            }}>
              <Button
                type="text"
                size="small"
                icon={<FolderOpenOutlined />}
                onClick={() => handleOpenFolder('video')}
                style={{
                  color: '#52c41a',
                  border: '1px solid rgba(82, 196, 26, 0.3)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  height: '28px',
                  padding: '0 12px',
                  background: 'rgba(82, 196, 26, 0.1)'
                }}
              >
                文件夹
              </Button>
              <Button
                type="text"
                size="small"
                icon={<EyeOutlined />}
                onClick={async () => {
                  try {
                    const url = `/api/v1/projects/${projectId}/clips/${clip.id}/cover?t=${Date.now()}`
                    setCoverUrl(url)
                    setShowCoverModal(true)
                  } catch {
                    message.error('获取封面失败')
                  }
                }}
                style={{
                  color: '#faad14',
                  border: '1px solid rgba(250, 173, 20, 0.3)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  height: '28px',
                  padding: '0 12px',
                  background: 'rgba(250, 173, 20, 0.1)'
                }}
              >
                封面
              </Button>
            </div>
          </div>
        </Card>

      {/* 视频播放模态框 */}
      <Modal
        open={showPlayer}
        onCancel={handleClosePlayer}
        footer={[
          <Button
            key="subtitle"
            icon={<EditOutlined />}
            onClick={handleOpenSubtitleEditor}
            disabled={burnStatus === 'burning'}
          >
            {burnStatus === 'burning' ? '烧录中...' : '字幕编辑'}
          </Button>,
        ]}
        width={800}
        centered
        destroyOnHidden
        styles={{
          header: {
            borderBottom: '1px solid #303030',
            background: '#1f1f1f'
          }
        }}
        closeIcon={
          <span style={{ color: '#ffffff', fontSize: '16px' }}>×</span>
        }
        title={
          <div style={{
            display: 'flex',
            alignItems: 'center',
            width: '100%',
            paddingRight: '30px' // 为关闭按钮留出空间
          }}>
            <EditableTitle
              title={clip.title || clip.generated_title || '视频预览'}
              clipId={clip.id}
              onTitleUpdate={(newTitle) => {
                // 更新clip的标题
                console.log('播放器标题已更新:', newTitle)
                // 这里可以触发父组件的更新回调
                if (onClipUpdate) {
                  onClipUpdate(clip.id, { title: newTitle })
                }
              }}
              style={{
                color: '#ffffff',
                fontSize: '16px',
                fontWeight: '500',
                flex: 1,
                maxWidth: 'calc(100% - 40px)' // 确保不会与关闭按钮重叠
              }}
            />
          </div>
        }
      >
        {videoUrl && (
          <ReactPlayer
            ref={playerRef}
            url={videoUrl}
            width="100%"
            height="400px"
            controls
            playing={showPlayer}
            config={{
              file: {
                attributes: {
                  controlsList: 'nodownload',
                  preload: 'metadata'
                },
                forceHLS: false,
                forceDASH: false
              }
            }}
            onReady={() => {
              console.log('Video ready for seeking')
            }}
            onError={(error) => {
              console.error('ReactPlayer error:', error)
            }}
          />
        )}
      </Modal>

      {/* 字幕编辑器 */}
      {showSubtitleEditor && (
        <>
          {console.log('Rendering SubtitleEditor with:', { showSubtitleEditor, subtitleDataLength: subtitleData.length })}
          <SubtitleEditor
            projectId={projectId || ''}
            clipId={clip.id}
            videoUrl={videoUrl || ''}
            clipTitle={clip.generated_title || clip.title}
            onClose={handleSubtitleEditorClose}
          />
        </>
      )}

      {/* 封面预览弹窗 */}
      <Modal
        open={showCoverModal}
        onCancel={() => setShowCoverModal(false)}
        footer={[
          <Button
            key="open-folder"
            icon={<FolderOpenOutlined />}
            onClick={() => handleOpenFolder('cover')}
          >
            打开文件夹
          </Button>,
          <Button
            key="regenerate"
            icon={<ReloadOutlined spin={regeneratingCover} />}
            loading={regeneratingCover}
            onClick={async () => {
              if (!projectId) return
              setRegeneratingCover(true)
              try {
                const resp = await fetch(`/api/v1/projects/${projectId}/clips/${clip.id}/regenerate-cover`, { method: 'POST' })
                const data = await resp.json()
                if (data.success) {
                  message.success('封面已重新生成')
                  setCoverUrl(`/api/v1/projects/${projectId}/clips/${clip.id}/cover?t=${Date.now()}`)
                } else {
                  message.error(data.detail || '重新生成封面失败')
                }
              } catch {
                message.error('重新生成封面失败')
              } finally {
                setRegeneratingCover(false)
              }
            }}
          >
            重新生成
          </Button>,
        ]}
        width={500}
        centered
        destroyOnHidden
        title="封面预览"
      >
        {coverUrl && (
          <img
            key={coverUrl}
            src={coverUrl}
            alt="封面"
            style={{ width: '100%', borderRadius: '8px' }}
            onError={() => message.error('封面图片加载失败，可能尚未生成')}
          />
        )}
      </Modal>
    </>
  )
}

export default ClipCard

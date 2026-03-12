import { SubtitleSegment } from '../types/subtitle'
import { SrtEntry, SubtitlePreset } from '../types/subtitle'

export interface SubtitleDataResponse {
  segments: SubtitleSegment[]
  total_duration: number
  word_count: number
  segment_count: number
}

export interface SubtitleEditRequest {
  project_id: string
  clip_id: string
  deleted_segments: string[]
}

export interface SubtitleEditResponse {
  success: boolean
  message: string
  edited_video_path?: string
  deleted_duration?: number
  final_duration?: number
}

export interface EditPreviewRequest {
  project_id: string
  clip_id: string
  deleted_segments: string[]
}

export interface EditPreviewResponse {
  success: boolean
  preview_files: string[]
  count: number
}

class SubtitleEditorApi {
  private baseUrl = '/api/v1/subtitle-editor'

  /**
   * 获取片段的字粒度字幕数据
   */
  async getClipSubtitles(projectId: string, clipId: string): Promise<SubtitleDataResponse> {
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/subtitles`)
    
    if (!response.ok) {
      throw new Error(`获取字幕数据失败: ${response.statusText}`)
    }
    
    return response.json()
  }

  /**
   * 基于字幕删除编辑视频片段
   */
  async editClipBySubtitles(
    projectId: string, 
    clipId: string, 
    deletedSegments: string[]
  ): Promise<SubtitleEditResponse> {
    const request: SubtitleEditRequest = {
      project_id: projectId,
      clip_id: clipId,
      deleted_segments: deletedSegments
    }

    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/edit`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(request)
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`编辑视频失败: ${errorText}`)
    }

    return response.json()
  }

  /**
   * 获取编辑后的视频文件URL
   */
  getEditedVideoUrl(projectId: string, clipId: string): string {
    return `${this.baseUrl}/${projectId}/clips/${clipId}/edited-video`
  }

  /**
   * 创建编辑预览片段
   */
  async createEditPreview(
    projectId: string, 
    clipId: string, 
    deletedSegments: string[]
  ): Promise<EditPreviewResponse> {
    const request: EditPreviewRequest = {
      project_id: projectId,
      clip_id: clipId,
      deleted_segments: deletedSegments
    }

    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/preview`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(request)
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`创建预览失败: ${errorText}`)
    }

    return response.json()
  }

  /**
   * 获取预览片段文件URL
   */
  getPreviewSegmentUrl(projectId: string, clipId: string, segmentId: string): string {
    return `${this.baseUrl}/${projectId}/clips/${clipId}/preview/${segmentId}`
  }

  /**
   * 下载编辑后的视频
   */
  async downloadEditedVideo(projectId: string, clipId: string, filename?: string): Promise<void> {
    const url = this.getEditedVideoUrl(projectId, clipId)
    
    try {
      const response = await fetch(url)
      
      if (!response.ok) {
        throw new Error(`下载失败: ${response.statusText}`)
      }

      const blob = await response.blob()
      const downloadUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = filename || `${clipId}_edited.mp4`
      
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      
      window.URL.revokeObjectURL(downloadUrl)
    } catch (error) {
      console.error('下载编辑后的视频失败:', error)
      throw error
    }
  }

  /**
   * 验证编辑操作
   */
  async validateEditOperations(
    projectId: string, 
    clipId: string, 
    deletedSegments: string[]
  ): Promise<{ valid: boolean; error?: string }> {
    try {
      // 先获取字幕数据来验证
      const subtitleData = await this.getClipSubtitles(projectId, clipId)
      
      // 检查删除的字幕段是否存在
      const existingIds = new Set(subtitleData.segments.map(seg => seg.id))
      const invalidIds = deletedSegments.filter(id => !existingIds.has(id))
      
      if (invalidIds.length > 0) {
        return {
          valid: false,
          error: `无效的字幕段ID: ${invalidIds.join(', ')}`
        }
      }

      // 检查删除后是否还有剩余内容
      const remainingSegments = subtitleData.segments.filter(
        seg => !deletedSegments.includes(seg.id)
      )

      if (remainingSegments.length === 0) {
        return {
          valid: false,
          error: '删除所有字幕段后没有剩余内容'
        }
      }

      return { valid: true }
    } catch (error) {
      return {
        valid: false,
        error: `验证失败: ${error instanceof Error ? error.message : '未知错误'}`
      }
    }
  }

  /**
   * 获取clip的SRT条目（纠错后）
   */
  async getSrtEntries(projectId: string, clipId: string): Promise<SrtEntry[]> {
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/subtitles`)
    if (!response.ok) {
      throw new Error(`获取字幕条目失败: ${response.statusText}`)
    }
    return response.json()
  }

  /**
   * 保存SRT条目到后端
   */
  async saveSrtEntries(projectId: string, clipId: string, entries: SrtEntry[]): Promise<void> {
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/subtitles`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entries),
    })
    if (!response.ok) {
      throw new Error(`保存字幕失败: ${response.statusText}`)
    }
  }

  /**
   * 获取字幕样式预设列表
   */
  async getPresets(projectId: string, clipId: string): Promise<SubtitlePreset[]> {
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/presets`)
    if (!response.ok) {
      return []
    }
    return response.json()
  }

  /**
   * 烧录字幕到视频
   */
  async burnSubtitles(
    projectId: string,
    clipId: string,
    presetId: string,
    styleOverrides?: {
      outline?: number
      shadow?: number
      bold?: number
      color?: string
      outline_color?: string
      font_size_ratio?: number
    }
  ): Promise<{ success: boolean; task_id?: string; message?: string }> {
    const body: Record<string, unknown> = { preset_id: presetId }
    if (styleOverrides) {
      if (styleOverrides.outline !== undefined) body.custom_outline = styleOverrides.outline
      if (styleOverrides.shadow !== undefined) body.custom_shadow = styleOverrides.shadow
      if (styleOverrides.bold !== undefined) body.custom_bold = styleOverrides.bold
      if (styleOverrides.color) body.custom_color = styleOverrides.color
      if (styleOverrides.outline_color) body.custom_outline_color = styleOverrides.outline_color
      if (styleOverrides.font_size_ratio !== undefined) body.custom_font_size_ratio = styleOverrides.font_size_ratio
    }
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/burn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const err = await response.text()
      throw new Error(`烧录失败: ${err}`)
    }
    return response.json()
  }

  async getBurnStatus(projectId: string, clipId: string): Promise<{ burn_status: string }> {
    const response = await fetch(`${this.baseUrl}/${projectId}/clips/${clipId}/burn-status`)
    if (!response.ok) {
      return { burn_status: 'none' }
    }
    return response.json()
  }
}

export const subtitleEditorApi = new SubtitleEditorApi()

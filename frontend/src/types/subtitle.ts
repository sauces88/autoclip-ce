// 字幕单词类型
export interface SubtitleWord {
  id: string
  text: string
  startTime: number  // 秒
  endTime: number    // 秒
  confidence?: number // 语音识别置信度
}

// 字幕段落类型
export interface SubtitleSegment {
  id: string
  startTime: number  // 秒
  endTime: number    // 秒
  words: SubtitleWord[]
  text: string       // 完整文本
  index: number      // 原始SRT索引
}

// 视频编辑操作类型
export interface VideoEditOperation {
  type: 'delete' | 'insert' | 'modify'
  segmentIds: string[]
  timestamp: number
  metadata?: {
    originalText?: string
    newText?: string
    timeRange?: {
      start: number
      end: number
    }
  }
}

// 字幕编辑器状态
export interface SubtitleEditorState {
  currentTime: number
  playing: boolean
  selectedWords: Set<string>
  deletedSegments: Set<string>
  editHistory: VideoEditOperation[]
  historyIndex: number
  showDeleted: boolean
}

// 字幕数据API响应
export interface SubtitleDataResponse {
  segments: SubtitleSegment[]
  total_duration: number
  word_count: number
  segment_count: number
}

// 视频编辑结果
export interface VideoEditResult {
  originalVideoPath: string
  editedVideoPath: string
  operations: VideoEditOperation[]
  totalDeletedDuration: number
  finalDuration: number
}

// ─── 新增：SRT条目类型（字幕编辑器重构用）───

export interface SrtEntry {
  id: string        // uuid，前端生成
  index: number     // SRT序号
  startTime: number // 秒
  endTime: number   // 秒
  text: string      // 可含 \n 换行
  disabled?: boolean // 禁用（烧录时过滤）
}

export interface SubtitlePreset {
  id: string
  label: string
  color: string           // 字色 hex
  outline_color?: string
  back_color?: string     // 底色（含透明度）
  outline: number
  shadow?: number
  bold?: number
}

import { create } from 'zustand'

export interface Clip {
  id: string
  title?: string  // 可能没有原始title
  start_time: string
  end_time: string
  final_score: number  // 匹配后端字段名
  recommend_reason: string  // 匹配后端字段名
  generated_title?: string
  outline: string
  content: string[]
  chunk_index?: number  // 添加缺失字段
  burn_status?: string  // none | burning | done | failed
}

// 项目状态类型定义，与后端保持一致
type ProjectStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'error'

export interface Project {
  id: string
  name: string
  description?: string
  project_type?: string
  status: ProjectStatus
  source_url?: string
  source_file?: string
  settings?: any
  processing_config?: {
    download_status?: string
    download_progress?: number
    download_message?: string
    [key: string]: any
  }
  created_at: string
  updated_at: string
  completed_at?: string
  total_clips?: number
  total_tasks?: number
  // 前端特有字段
  video_path?: string
  video_category?: string
  thumbnail?: string
  clips?: Clip[]
  current_step?: number
  total_steps?: number
  error_message?: string
}

interface ProjectStore {
  projects: Project[]
  currentProject: Project | null
  loading: boolean
  error: string | null
  lastEditTimestamp: number
  isDragging: boolean

  // Actions
  setProjects: (projects: Project[]) => void
  setCurrentProject: (project: Project | null) => void
  addProject: (project: Project) => void
  updateProject: (id: string, updates: Partial<Project>) => void
  deleteProject: (id: string) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  updateClip: (projectId: string, clipId: string, updates: Partial<Clip>) => void
  setDragging: (isDragging: boolean) => void
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  error: null,
  lastEditTimestamp: 0,
  isDragging: false,

  setProjects: (projects) => {
    const state = get()

    // 如果正在拖拽，则跳过更新以避免冲突
    if (state.isDragging) {
      console.log('Skipping update: dragging in progress')
      return
    }

    set({ projects })
  },

  setCurrentProject: (project) => set({ currentProject: project }),

  addProject: (project) => set((state) => ({
    projects: [project, ...state.projects]
  })),

  updateProject: (id, updates) => set((state) => ({
    projects: state.projects.map(p => p.id === id ? { ...p, ...updates } : p),
    currentProject: state.currentProject?.id === id
      ? { ...state.currentProject, ...updates }
      : state.currentProject
  })),

  deleteProject: (id) => {
    // 清理缩略图缓存
    const thumbnailCacheKey = `thumbnail_${id}`
    localStorage.removeItem(thumbnailCacheKey)

    set((state) => ({
      projects: state.projects.filter(p => p.id !== id),
      currentProject: state.currentProject?.id === id ? null : state.currentProject
    }))
  },

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error }),

  updateClip: (projectId, clipId, updates) => set((state) => ({
    projects: state.projects.map(p =>
      p.id === projectId
        ? { ...p, clips: (p.clips || []).map(c => c.id === clipId ? { ...c, ...updates } : c) }
        : p
    ),
    currentProject: state.currentProject?.id === projectId
      ? {
          ...state.currentProject,
          clips: (state.currentProject.clips || []).map(c => c.id === clipId ? { ...c, ...updates } : c)
        }
      : state.currentProject
  })),

  setDragging: (isDragging) => set({ isDragging })
}))

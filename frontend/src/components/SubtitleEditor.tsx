import React, { useState, useRef, useCallback, useEffect } from 'react'
import {
  message, Modal, Tooltip, Input,
} from 'antd'
import {
  UndoOutlined, RedoOutlined, SaveOutlined, ScissorOutlined,
  ArrowLeftOutlined, MergeCellsOutlined, EnterOutlined,
  CheckOutlined, EyeOutlined, EyeInvisibleOutlined,
} from '@ant-design/icons'
import ReactPlayer from 'react-player'
import { SrtEntry, SubtitlePreset } from '../types/subtitle'
import { subtitleEditorApi } from '../services/subtitleEditorApi'

interface SubtitleEditorProps {
  projectId: string
  clipId: string
  videoUrl: string
  clipTitle?: string
  onClose: () => void
}

function formatTime(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  const ms = Math.round((sec % 1) * 10)
  return `${h ? h + ':' : ''}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${ms}`
}

function genId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

const DEFAULT_PRESETS: SubtitlePreset[] = [
  { id: 'classic',      label: '经典',    color: '#ffffff', outline_color: '#000000', outline: 2, shadow: 1 },
  { id: 'douyin',       label: '抖音',    color: '#ffffff', outline_color: '#000000', outline: 3, shadow: 0 },
  { id: 'xiaohongshu',  label: '小红书',  color: '#ffffff', outline_color: '#ffdd00', outline: 2, shadow: 1 },
  { id: 'warm',         label: '暖白',    color: '#fff5e4', outline_color: '#8b6914', outline: 2, shadow: 1 },
  { id: 'documentary',  label: '纪录片',  color: '#f0e6c8', back_color: '#00000080', outline: 1, shadow: 0 },
  { id: 'neon',         label: '霓虹',    color: '#00ffcc', outline_color: '#ff00aa', outline: 3, shadow: 2 },
  { id: 'news',         label: '新闻',    color: '#ffffff', back_color: '#1a3a6eff', outline: 1, shadow: 0 },
  { id: 'ted',          label: 'TED',     color: '#ffffff', back_color: '#e62b1eff', outline: 1, shadow: 0 },
  { id: 'bilibili',     label: 'B站',     color: '#00aeec', outline_color: '#000000', outline: 1, shadow: 1 },
  { id: 'youtube',      label: 'YouTube', color: '#ffffff', back_color: '#000000bf', outline: 0, shadow: 0 },
  { id: 'clean_black',  label: '简约黑',  color: '#000000', back_color: '#ffffffd0', outline: 1, shadow: 0 },
  { id: 'karaoke',      label: '卡拉OK',  color: '#ffff00', outline_color: '#ff6600', outline: 3, shadow: 2 },
]

const FONT_SIZE_RATIOS = { S: 0.75, M: 1.0, L: 1.3, XL: 1.7 }

// ── 解析 back_color (#RRGGBBAA) → CSS rgba ──────────────
function parseBackColor(raw?: string): string | undefined {
  if (!raw) return undefined
  const s = raw.replace('#', '')
  if (s.length < 6) return undefined
  const r = parseInt(s.slice(0, 2), 16)
  const g = parseInt(s.slice(2, 4), 16)
  const b = parseInt(s.slice(4, 6), 16)
  const a = s.length >= 8 ? 1 - parseInt(s.slice(6, 8), 16) / 255 : 1
  return `rgba(${r},${g},${b},${a})`
}

const EDITOR_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

  .sube-root * { box-sizing: border-box; }
  .sube-root { font-family: 'Syne', system-ui, sans-serif; }

  .sube-list::-webkit-scrollbar { width: 4px; }
  .sube-list::-webkit-scrollbar-track { background: transparent; }
  .sube-list::-webkit-scrollbar-thumb { background: #2e2f40; border-radius: 2px; }
  .sube-list::-webkit-scrollbar-thumb:hover { background: #3d3f58; }

  .sube-panel::-webkit-scrollbar { width: 4px; }
  .sube-panel::-webkit-scrollbar-track { background: transparent; }
  .sube-panel::-webkit-scrollbar-thumb { background: #2e2f40; border-radius: 2px; }

  .sube-card {
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 6px;
    cursor: pointer;
    transition: background 0.12s, border-color 0.12s;
    border: 1px solid #22233a;
    background: #15161f;
    position: relative;
  }
  .sube-card::before {
    content: '';
    position: absolute;
    left: 0; top: 6px; bottom: 6px;
    width: 3px;
    border-radius: 2px;
    background: transparent;
    transition: background 0.15s;
  }
  .sube-card:hover { background: #1c1d2b; border-color: #2d2f47; }
  .sube-card:hover::before { background: #3d3f58; }
  .sube-card.is-current { background: #181a2e; border-color: #4f5fd0; }
  .sube-card.is-current::before { background: #6366f1; }
  .sube-card.is-selected { background: #1a1c2a; border-color: #3a3d60; }
  .sube-card.is-selected::before { background: #3a3d60; }

  .sube-card.is-disabled {
    opacity: 0.4;
    text-decoration: line-through;
  }
  .sube-card.is-disabled input,
  .sube-card.is-disabled textarea {
    pointer-events: none;
  }

  .sube-card-actions { opacity: 0; transition: opacity 0.12s; }
  .sube-card:hover .sube-card-actions { opacity: 1; }

  .sube-preset-card {
    border-radius: 8px;
    padding: 10px 8px 8px;
    cursor: pointer;
    border: 1.5px solid #22233a;
    background: #15161f;
    transition: border-color 0.15s, transform 0.1s;
    text-align: center;
  }
  .sube-preset-card:hover { border-color: #3a3d60; transform: translateY(-1px); }
  .sube-preset-card.active { border-color: #6366f1; background: #1a1b2e; }

  .sube-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 5px;
    border-radius: 7px; border: none; cursor: pointer;
    font-family: 'Syne', system-ui, sans-serif;
    font-weight: 600; font-size: 12px;
    transition: background 0.12s, opacity 0.12s, transform 0.08s;
    outline: none;
    white-space: nowrap;
  }
  .sube-btn:active:not(:disabled) { transform: scale(0.96); }
  .sube-btn:disabled { opacity: 0.35; cursor: not-allowed; }

  .sube-btn-ghost {
    background: transparent; color: #6b6e8a; padding: 6px 10px;
  }
  .sube-btn-ghost:hover:not(:disabled) { background: #1f2038; color: #9da0be; }

  .sube-btn-outline {
    background: transparent; color: #9da0be; padding: 6px 12px;
    border: 1px solid #2e3050;
  }
  .sube-btn-outline:hover:not(:disabled) { background: #1f2038; border-color: #4a4e80; color: #c5c8e0; }

  .sube-btn-primary {
    background: #4f5fd0; color: #fff; padding: 7px 16px;
  }
  .sube-btn-primary:hover:not(:disabled) { background: #5a6bdc; }

  .sube-btn-burn {
    background: linear-gradient(135deg, #e05a1a, #f07830);
    color: #fff; padding: 7px 16px;
  }
  .sube-btn-burn:hover:not(:disabled) { background: linear-gradient(135deg, #ec6820, #f58840); }

  .sube-btn-danger {
    background: transparent; color: #6b6e8a; padding: 4px 7px; border-radius: 5px;
  }
  .sube-btn-danger:hover:not(:disabled) { background: #2d1a1a; color: #ef5656; }

  .sube-btn-icon {
    background: transparent; color: #6b6e8a; padding: 5px 8px; border-radius: 5px;
  }
  .sube-btn-icon:hover:not(:disabled) { background: #1f2038; color: #9da0be; }

  .sube-input {
    background: transparent !important;
    color: #d4d7f0 !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px 0 !important;
    font-size: 13px !important;
    font-family: 'Syne', system-ui, sans-serif !important;
    resize: none !important;
    line-height: 1.6 !important;
  }
  .sube-input:focus { box-shadow: none !important; }
  .sube-input::placeholder { color: #3d3f58 !important; }

  .sube-size-btn {
    width: 34px; height: 28px;
    display: inline-flex; align-items: center; justify-content: center;
    border-radius: 6px; border: 1px solid #2a2c42;
    background: #15161f; color: #6b6e8a;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 500;
    cursor: pointer; transition: all 0.12s;
  }
  .sube-size-btn:hover { border-color: #3a3d60; color: #9da0be; }
  .sube-size-btn.active {
    border-color: #6366f1; background: #1a1b2e; color: #a0a3f8;
  }

  .sube-pos-btn {
    padding: 5px 14px;
    border-radius: 6px; border: 1px solid #2a2c42;
    background: #15161f; color: #6b6e8a;
    font-family: 'Syne', system-ui, sans-serif;
    font-size: 12px; font-weight: 500;
    cursor: pointer; transition: all 0.12s;
  }
  .sube-pos-btn:hover { border-color: #3a3d60; color: #9da0be; }
  .sube-pos-btn.active {
    border-color: #6366f1; background: #1a1b2e; color: #a0a3f8;
  }

  .sube-merge-bar {
    padding: 8px 12px;
    border-bottom: 1px solid #1e1f2e;
    background: #12132059;
  }
  .sube-merge-btn {
    width: 100%; padding: 7px 12px;
    border-radius: 7px; border: 1px dashed #3a3d60;
    background: transparent; color: #6366f1;
    font-family: 'Syne', system-ui, sans-serif;
    font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
    display: flex; align-items: center; justify-content: center; gap: 6px;
  }
  .sube-merge-btn:hover { background: #1a1b2e; border-color: #6366f1; color: #8a8df8; }

  @keyframes spin { to { transform: rotate(360deg); } }
  .sube-spinner {
    width: 20px; height: 20px;
    border: 2px solid #2d2f47;
    border-top-color: #6366f1;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    display: inline-block;
  }

  @keyframes fadeSlideIn {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .sube-card { animation: fadeSlideIn 0.15s ease both; }

  .sube-badge {
    display: inline-flex; align-items: center; justify-content: center;
    background: #1e1f2e; color: #6b6e8a;
    border-radius: 10px; padding: 1px 8px;
    font-size: 11px; font-family: 'JetBrains Mono', monospace;
    border: 1px solid #2a2c42;
  }
`

const SubtitleEditor: React.FC<SubtitleEditorProps> = ({
  projectId, clipId, videoUrl, clipTitle = '字幕编辑', onClose,
}) => {
  const [entries, setEntries] = useState<SrtEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [presets, setPresets] = useState<SubtitlePreset[]>(DEFAULT_PRESETS)
  const [activePreset, setActivePreset] = useState<string>('classic')
  const [undoStack, setUndoStack] = useState<SrtEntry[][]>([])
  const [redoStack, setRedoStack] = useState<SrtEntry[][]>([])
  const [currentTime, setCurrentTime] = useState(0)
  const [burning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [fontSizeKey, setFontSizeKey] = useState<'S' | 'M' | 'L' | 'XL'>('M')
  const [position, setPosition] = useState<'bottom' | 'top'>('bottom')
  // 自定义样式（覆盖预设）
  const [styleOutline, setStyleOutline] = useState(2)
  const [styleShadow, setStyleShadow] = useState(1)
  const [styleBold, setStyleBold] = useState(true)
  const [styleColor, setStyleColor] = useState('#ffffff')
  const [styleOutlineColor, setStyleOutlineColor] = useState('#000000')
  const playerRef = useRef<ReactPlayer>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const activeCardRef = useRef<HTMLDivElement>(null)
  const inputRefs = useRef<Record<string, HTMLInputElement | HTMLTextAreaElement | null>>({})

  useEffect(() => {
    Promise.all([
      subtitleEditorApi.getSrtEntries(projectId, clipId).catch(() => []),
      subtitleEditorApi.getPresets(projectId, clipId).catch(() => DEFAULT_PRESETS),
    ]).then(([srtEntries, presetList]) => {
      setEntries(srtEntries.length ? srtEntries : [])
      if (presetList && presetList.length) setPresets(presetList)
      // 初始同步默认预设的样式值
      const defaultPreset = (presetList && presetList.length ? presetList : DEFAULT_PRESETS)
        .find((p: SubtitlePreset) => p.id === activePreset)
      if (defaultPreset) {
        setStyleOutline(defaultPreset.outline ?? 2)
        setStyleShadow(defaultPreset.shadow ?? 1)
        setStyleBold(!!(defaultPreset.bold))
        setStyleColor(defaultPreset.color || '#ffffff')
        setStyleOutlineColor(defaultPreset.outline_color || '#000000')
      }
      setLoading(false)
    })
  }, [projectId, clipId])

  // 选择预设时同步自定义值（仅用户点击预设卡片时调用）
  const applyPresetStyle = useCallback((presetId: string) => {
    const p = presets.find(p => p.id === presetId)
    if (p) {
      setStyleOutline(p.outline ?? 2)
      setStyleShadow(p.shadow ?? 1)
      setStyleBold(!!(p.bold))
      setStyleColor(p.color || '#ffffff')
      setStyleOutlineColor(p.outline_color || '#000000')
    }
    setActivePreset(presetId)
  }, [presets])

  // 自动滚动当前字幕进入视野
  useEffect(() => {
    if (activeCardRef.current && listRef.current) {
      activeCardRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [currentTime])

  const pushUndo = useCallback((prev: SrtEntry[]) => {
    setUndoStack(s => [...s, prev])
    setRedoStack([])
  }, [])

  const updateEntries = useCallback((newEntries: SrtEntry[], oldEntries: SrtEntry[]) => {
    pushUndo(oldEntries)
    setEntries(newEntries)
  }, [pushUndo])

  const undo = useCallback(() => {
    setUndoStack(s => {
      if (!s.length) return s
      const prev = s[s.length - 1]
      setRedoStack(r => [...r, entries])
      setEntries(prev)
      return s.slice(0, -1)
    })
  }, [entries])

  const redo = useCallback(() => {
    setRedoStack(s => {
      if (!s.length) return s
      const next = s[s.length - 1]
      setUndoStack(r => [...r, entries])
      setEntries(next)
      return s.slice(0, -1)
    })
  }, [entries])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      await subtitleEditorApi.saveSrtEntries(projectId, clipId, entries)
      message.success('字幕已保存')
    } catch (e: any) {
      message.error(`保存失败: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }, [projectId, clipId, entries])

  const handleBurn = useCallback(async () => {
    Modal.confirm({
      title: '确认烧录字幕',
      content: `将使用「${presets.find(p => p.id === activePreset)?.label || activePreset}」样式烧录字幕到视频`,
      onOk: async () => {
        try {
          await subtitleEditorApi.saveSrtEntries(projectId, clipId, entries)
          await subtitleEditorApi.burnSubtitles(projectId, clipId, activePreset, {
            outline: styleOutline,
            shadow: styleShadow,
            bold: styleBold ? 1 : 0,
            color: styleColor,
            outline_color: styleOutlineColor,
            font_size_ratio: FONT_SIZE_RATIOS[fontSizeKey],
          })
          message.success('烧录任务已提交，可在视频列表中查看进度')
          onClose()
        } catch (e: any) {
          message.error(`烧录失败: ${e.message}`)
        }
      },
    })
  }, [projectId, clipId, entries, activePreset, presets, styleOutline, styleShadow, styleBold, styleColor, styleOutlineColor, fontSizeKey, onClose])

  const handleTextChange = useCallback((id: string, text: string) => {
    setEntries(prev => {
      const updated = prev.map(e => e.id === id ? { ...e, text } : e)
      pushUndo(prev)
      return updated
    })
  }, [pushUndo])

  const handleToggleDisable = useCallback((id: string) => {
    pushUndo(entries)
    setEntries(prev => prev.map(e =>
      e.id === id ? { ...e, disabled: !e.disabled } : e
    ))
  }, [entries, pushUndo])

  const handleMerge = useCallback(() => {
    if (selected.size !== 2) return
    const selArr = Array.from(selected)
    const idx0 = entries.findIndex(e => e.id === selArr[0])
    const idx1 = entries.findIndex(e => e.id === selArr[1])
    if (idx0 < 0 || idx1 < 0) return
    const [firstIdx, secondIdx] = idx0 < idx1 ? [idx0, idx1] : [idx1, idx0]
    if (secondIdx !== firstIdx + 1) { message.warning('只能合并相邻的两条字幕'); return }
    const first = entries[firstIdx]
    const second = entries[secondIdx]
    const merged: SrtEntry = {
      id: genId(),
      index: first.index,
      startTime: first.startTime,
      endTime: second.endTime,
      text: first.text.trimEnd() + '\n' + second.text.trimStart(),
    }
    const updated = [...entries.slice(0, firstIdx), merged, ...entries.slice(secondIdx + 1)]
    updateEntries(updated, entries)
    setSelected(new Set())
  }, [selected, entries, updateEntries])

  const handleSplit = useCallback((id: string) => {
    const idx = entries.findIndex(e => e.id === id)
    if (idx < 0) return
    const entry = entries[idx]
    const text = entry.text.replace(/\n/g, '')

    // 找中间附近的标点断点
    const breakChars = new Set('，。！？、；,;!? ')
    let bestPos = -1
    let bestDist = text.length
    const mid = Math.floor(text.length / 2)
    for (let i = 0; i < text.length; i++) {
      if (breakChars.has(text[i]) && i > 0 && i < text.length - 1) {
        const pos = i + 1
        const dist = Math.abs(pos - mid)
        if (dist < bestDist) { bestDist = dist; bestPos = pos }
      }
    }
    if (bestPos < 0) {
      // 没有标点，取中间位置
      bestPos = mid
    }

    const text1 = text.slice(0, bestPos).trim()
    const text2 = text.slice(bestPos).trim()
    if (!text1 || !text2) { message.warning('文本太短，无法拆分'); return }

    const midTime = entry.startTime + (entry.endTime - entry.startTime) * (bestPos / text.length)
    const part1: SrtEntry = { id: genId(), index: entry.index, startTime: entry.startTime, endTime: midTime, text: text1 }
    const part2: SrtEntry = { id: genId(), index: entry.index + 1, startTime: midTime, endTime: entry.endTime, text: text2 }

    const updated = [...entries.slice(0, idx), part1, part2, ...entries.slice(idx + 1)]
    updateEntries(updated, entries)
  }, [entries, updateEntries])

  const handleInsertNewline = useCallback((id: string) => {
    const el = inputRefs.current[id]
    const pos = el?.selectionStart ?? -1
    setEntries(prev => {
      const entry = prev.find(e => e.id === id)
      if (!entry) return prev
      const cursorPos = pos >= 0 && pos <= entry.text.length ? pos : Math.floor(entry.text.length / 2)
      const newText = entry.text.slice(0, cursorPos) + '\n' + entry.text.slice(cursorPos)
      const updated = prev.map(e => e.id === id ? { ...e, text: newText } : e)
      pushUndo(prev)
      return updated
    })
  }, [pushUndo])

  const handleRemoveNewlines = useCallback((id: string) => {
    setEntries(prev => {
      const updated = prev.map(e => e.id === id ? { ...e, text: e.text.replace(/\n/g, '') } : e)
      pushUndo(prev)
      return updated
    })
  }, [pushUndo])

  const handleEntryClick = (entry: SrtEntry) => {
    playerRef.current?.seekTo(entry.startTime, 'seconds')
    setCurrentTime(entry.startTime)
  }

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const ns = new Set(prev)
      if (ns.has(id)) ns.delete(id); else ns.add(id)
      return ns
    })
  }

  const currentEntry = entries.find(e => currentTime >= e.startTime && currentTime <= e.endTime)
  const activePresetDef = presets.find(p => p.id === activePreset) || presets[0]
  const fontSizeRatio = FONT_SIZE_RATIOS[fontSizeKey]

  const canMerge = selected.size === 2 && (() => {
    const selArr = Array.from(selected)
    const i0 = entries.findIndex(e => e.id === selArr[0])
    const i1 = entries.findIndex(e => e.id === selArr[1])
    return Math.abs(i0 - i1) === 1
  })()

  const getSubtitleOverlayStyle = (): React.CSSProperties => {
    if (!activePresetDef) return {}
    // 使用自定义值（已在切换预设时同步）
    const color = styleColor
    const outlineColor = styleOutlineColor
    const backColor = parseBackColor(activePresetDef.back_color)
    const outline = styleOutline
    const shadow = styleShadow

    let textShadow = ''
    if (outline > 0) {
      const o = outline
      textShadow = `${o}px ${o}px 0 ${outlineColor},-${o}px ${o}px 0 ${outlineColor},${o}px -${o}px 0 ${outlineColor},-${o}px -${o}px 0 ${outlineColor}`
    }
    if (shadow > 0) {
      const s = `${shadow + 1}px ${shadow + 1}px ${shadow * 2}px rgba(0,0,0,0.8)`
      textShadow = textShadow ? textShadow + ',' + s : s
    }

    const style: React.CSSProperties = {
      position: 'absolute',
      left: '50%',
      transform: 'translateX(-50%)',
      color,
      textShadow: textShadow || undefined,
      fontSize: `${Math.round(20 * fontSizeRatio)}px`,
      fontWeight: styleBold ? 'bold' : 'normal',
      textAlign: 'center',
      whiteSpace: 'pre-wrap',
      lineHeight: 1.4,
      padding: '4px 14px',
      borderRadius: '4px',
      pointerEvents: 'none',
      maxWidth: '90%',
      zIndex: 10,
      backgroundColor: backColor,
    }
    if (position === 'bottom') style.bottom = '8%'
    else style.top = '8%'
    return style
  }

  // ── RENDER ────────────────────────────────────────────

  return (
    <Modal
      open={true}
      onCancel={onClose}
      footer={null}
      destroyOnClose
      width="100vw"
      style={{ top: 0, maxWidth: '100vw', padding: 0, margin: 0 }}
      styles={{ body: { padding: 0, height: '100vh', overflow: 'hidden' } }}
    >
      <style>{EDITOR_STYLES}</style>
      <div
        className="sube-root"
        style={{
          display: 'flex', flexDirection: 'column',
          height: '100vh',
          background: '#0d0e14',
          color: '#d4d7f0',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* ── 烧录遮罩 ── */}
        {burning && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 999,
            background: 'rgba(8,9,15,0.85)',
            backdropFilter: 'blur(6px)',
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 16,
          }}>
            <div className="sube-spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
            <div style={{ color: '#9da0be', fontSize: 14, fontFamily: 'Syne, sans-serif', letterSpacing: '0.04em' }}>
              烧录字幕中，请稍候...
            </div>
          </div>
        )}

        {/* ── 顶部工具栏 ── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '0 12px',
          height: 48,
          background: '#0b0c12',
          borderBottom: '1px solid #1a1b2a',
          flexShrink: 0,
        }}>
          {/* 返回 + 标题 */}
          <button className="sube-btn sube-btn-ghost" style={{ padding: '6px 8px', gap: 4 }} onClick={onClose}>
            <ArrowLeftOutlined style={{ fontSize: 13 }} />
          </button>
          <div style={{ width: 1, height: 20, background: '#1e1f2e', margin: '0 2px' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#d4d7f0', lineHeight: 1.2, letterSpacing: '0.01em' }}>
              {clipTitle}
            </div>
            <div style={{ fontSize: 10, color: '#3d3f58', fontFamily: "'JetBrains Mono', monospace" }}>
              字幕编辑器
            </div>
          </div>

          {/* 撤销/重做 */}
          <div style={{ display: 'flex', gap: 2 }}>
            <Tooltip title="撤销 (Ctrl+Z)">
              <button
                className="sube-btn sube-btn-ghost"
                onClick={undo}
                disabled={!undoStack.length}
              >
                <UndoOutlined style={{ fontSize: 13 }} />
                <span style={{ fontSize: 11 }}>撤销</span>
              </button>
            </Tooltip>
            <Tooltip title="重做 (Ctrl+Y)">
              <button
                className="sube-btn sube-btn-ghost"
                onClick={redo}
                disabled={!redoStack.length}
              >
                <RedoOutlined style={{ fontSize: 13 }} />
                <span style={{ fontSize: 11 }}>重做</span>
              </button>
            </Tooltip>
          </div>

          <div style={{ width: 1, height: 20, background: '#1e1f2e' }} />

          {/* 保存 */}
          <button
            className="sube-btn sube-btn-primary"
            onClick={handleSave}
            disabled={saving}
            style={{ gap: 6 }}
          >
            {saving
              ? <div className="sube-spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />
              : <SaveOutlined style={{ fontSize: 12 }} />
            }
            <span>保存</span>
          </button>

          {/* 烧录 */}
          <button
            className="sube-btn sube-btn-burn"
            onClick={handleBurn}
            disabled={burning}
            style={{ gap: 6 }}
          >
            <ScissorOutlined style={{ fontSize: 12 }} />
            <span>烧录字幕</span>
          </button>
        </div>

        {/* ── 主体三栏 ── */}
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

          {/* ── 左栏：字幕列表 ── */}
          <div style={{
            width: 300,
            borderRight: '1px solid #1a1b2a',
            display: 'flex', flexDirection: 'column',
            background: '#0f1018',
            overflow: 'hidden',
            flexShrink: 0,
          }}>
            {/* 列表头 */}
            <div style={{
              padding: '10px 14px',
              borderBottom: '1px solid #1a1b2a',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#6b6e8a', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                字幕列表
              </span>
              {!loading && (
                <span className="sube-badge">{entries.length}</span>
              )}
            </div>

            {/* 合并操作 */}
            {canMerge && (
              <div className="sube-merge-bar">
                <button className="sube-merge-btn" onClick={handleMerge}>
                  <MergeCellsOutlined style={{ fontSize: 12 }} />
                  合并选中的两条字幕
                </button>
              </div>
            )}

            {/* 列表 */}
            <div ref={listRef} className="sube-list" style={{ flex: 1, overflowY: 'auto', padding: '8px 10px' }}>
              {loading ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, gap: 10 }}>
                  <div className="sube-spinner" />
                  <span style={{ color: '#3d3f58', fontSize: 12 }}>加载中...</span>
                </div>
              ) : entries.length === 0 ? (
                <div style={{ textAlign: 'center', paddingTop: 60, color: '#3d3f58', fontSize: 13 }}>
                  暂无字幕
                </div>
              ) : entries.map((entry) => {
                const isCurrent = currentTime >= entry.startTime && currentTime <= entry.endTime
                const isSel = selected.has(entry.id)
                const classes = ['sube-card', isCurrent ? 'is-current' : '', isSel ? 'is-selected' : '', entry.disabled ? 'is-disabled' : ''].filter(Boolean).join(' ')

                return (
                  <div
                    key={entry.id}
                    ref={isCurrent ? activeCardRef : undefined}
                    className={classes}
                    onClick={() => handleEntryClick(entry)}
                  >
                    {/* 时间行 */}
                    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6, gap: 4 }}>
                      {/* 复选框 */}
                      <div
                        onClick={e => { e.stopPropagation(); toggleSelect(entry.id) }}
                        style={{
                          width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                          border: `1.5px solid ${isSel ? '#6366f1' : '#2a2c42'}`,
                          background: isSel ? '#6366f1' : 'transparent',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          cursor: 'pointer', transition: 'all 0.1s',
                        }}
                      >
                        {isSel && <CheckOutlined style={{ fontSize: 9, color: '#fff' }} />}
                      </div>

                      <span style={{
                        flex: 1,
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 10, fontWeight: 500,
                        color: isCurrent ? '#6366f1' : '#444664',
                        letterSpacing: '0.01em',
                      }}>
                        {formatTime(entry.startTime)} — {formatTime(entry.endTime)}
                      </span>

                      {/* 操作按钮（hover 显示） */}
                      <div className="sube-card-actions" style={{ display: 'flex', gap: 2 }}>
                        <Tooltip title="拆分字幕">
                          <button
                            className="sube-btn sube-btn-icon"
                            onClick={e => { e.stopPropagation(); handleSplit(entry.id) }}
                            style={{ fontSize: 11 }}
                          >
                            <ScissorOutlined />
                          </button>
                        </Tooltip>
                        <Tooltip title="在光标处换行">
                          <button
                            className="sube-btn sube-btn-icon"
                            onClick={e => { e.stopPropagation(); handleInsertNewline(entry.id) }}
                            style={{ fontSize: 11 }}
                          >
                            <EnterOutlined />
                          </button>
                        </Tooltip>
                        {entry.text.includes('\n') && (
                          <Tooltip title="取消所有换行">
                            <button
                              className="sube-btn sube-btn-icon"
                              onClick={e => { e.stopPropagation(); handleRemoveNewlines(entry.id) }}
                              style={{ fontSize: 11, color: '#f59e0b' }}
                            >
                              ↩
                            </button>
                          </Tooltip>
                        )}
                        <Tooltip title={entry.disabled ? '启用字幕' : '禁用字幕'}>
                          <button
                            className="sube-btn sube-btn-danger"
                            onClick={e => { e.stopPropagation(); handleToggleDisable(entry.id) }}
                          >
                            {entry.disabled
                              ? <EyeOutlined style={{ fontSize: 11 }} />
                              : <EyeInvisibleOutlined style={{ fontSize: 11 }} />}
                          </button>
                        </Tooltip>
                      </div>
                    </div>

                    {/* 文本编辑 */}
                    {entry.text.includes('\n') ? (
                      <Input.TextArea
                        ref={el => { inputRefs.current[entry.id] = el?.resizableTextArea?.textArea ?? null }}
                        className="sube-input"
                        value={entry.text}
                        onChange={e => handleTextChange(entry.id, e.target.value)}
                        onClick={e => e.stopPropagation()}
                        autoSize={{ minRows: 2, maxRows: 4 }}
                        placeholder="（空字幕）"
                      />
                    ) : (
                      <Input
                        ref={el => { inputRefs.current[entry.id] = el?.input ?? null }}
                        className="sube-input"
                        value={entry.text}
                        onChange={e => handleTextChange(entry.id, e.target.value)}
                        onClick={e => e.stopPropagation()}
                        placeholder="（空字幕）"
                      />
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ── 中栏：视频预览 ── */}
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column',
            background: '#070709', position: 'relative',
            overflow: 'hidden',
          }}>
            <div style={{ flex: 1, position: 'relative' }}>
              <ReactPlayer
                ref={playerRef}
                url={videoUrl}
                width="100%"
                height="100%"
                controls
                onProgress={p => setCurrentTime(p.playedSeconds)}
                style={{ position: 'absolute', top: 0, left: 0 }}
              />
              {currentEntry && (
                <div style={getSubtitleOverlayStyle()}>
                  {currentEntry.text}
                </div>
              )}
            </div>

            {/* 当前字幕预览条 */}
            <div style={{
              height: 40,
              borderTop: '1px solid #1a1b2a',
              background: '#0b0c12',
              display: 'flex', alignItems: 'center',
              padding: '0 16px', gap: 10,
              flexShrink: 0,
            }}>
              <span style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 10, color: '#2e3050',
                letterSpacing: '0.04em',
              }}>
                {formatTime(currentTime)}
              </span>
              <div style={{ width: 1, height: 14, background: '#1e1f2e' }} />
              <span style={{
                fontSize: 12, color: currentEntry ? '#9da0be' : '#2e3050',
                fontStyle: currentEntry ? 'normal' : 'italic',
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {currentEntry ? currentEntry.text.replace('\n', ' ') : '无字幕'}
              </span>
            </div>
          </div>

          {/* ── 右栏：样式面板 ── */}
          <div style={{
            width: 248,
            borderLeft: '1px solid #1a1b2a',
            background: '#0f1018',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
            flexShrink: 0,
          }}>
            {/* 面板头 */}
            <div style={{
              padding: '10px 14px',
              borderBottom: '1px solid #1a1b2a',
            }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#6b6e8a', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                字幕样式
              </span>
            </div>

            <div className="sube-panel" style={{ flex: 1, overflowY: 'auto', padding: '12px 10px' }}>
              {/* 样式预设网格 */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 20 }}>
                {presets.map(p => {
                  const bgColor = parseBackColor(p.back_color)
                  const outlineStyle = p.outline_color && p.outline
                    ? `1px 1px 0 ${p.outline_color},-1px 1px 0 ${p.outline_color},1px -1px 0 ${p.outline_color},-1px -1px 0 ${p.outline_color}`
                    : 'none'
                  return (
                    <div
                      key={p.id}
                      className={`sube-preset-card${activePreset === p.id ? ' active' : ''}`}
                      onClick={() => applyPresetStyle(p.id)}
                    >
                      {/* 预览区 */}
                      <div style={{
                        height: 36, borderRadius: 5,
                        background: bgColor || '#0a0a10',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        marginBottom: 6, overflow: 'hidden',
                        position: 'relative',
                        border: '1px solid #1e1f2e',
                      }}>
                        <span style={{
                          color: p.color,
                          fontSize: 13, fontWeight: 700,
                          textShadow: outlineStyle,
                          letterSpacing: '0.03em',
                        }}>
                          字幕
                        </span>
                        {activePreset === p.id && (
                          <div style={{
                            position: 'absolute', top: 3, right: 3,
                            width: 12, height: 12, borderRadius: '50%',
                            background: '#6366f1',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}>
                            <CheckOutlined style={{ fontSize: 7, color: '#fff' }} />
                          </div>
                        )}
                      </div>
                      <div style={{ color: '#6b6e8a', fontSize: 10, fontWeight: 500, letterSpacing: '0.03em' }}>
                        {p.label}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* 分隔线 */}
              <div style={{ height: 1, background: '#1a1b2a', marginBottom: 16 }} />

              {/* 字号 */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b6e8a', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
                  字号
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {(['S', 'M', 'L', 'XL'] as const).map(k => (
                    <button
                      key={k}
                      className={`sube-size-btn${fontSizeKey === k ? ' active' : ''}`}
                      onClick={() => setFontSizeKey(k)}
                    >
                      {k}
                    </button>
                  ))}
                </div>
              </div>

              {/* 位置 */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b6e8a', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
                  位置
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className={`sube-pos-btn${position === 'bottom' ? ' active' : ''}`} onClick={() => setPosition('bottom')}>
                    下方
                  </button>
                  <button className={`sube-pos-btn${position === 'top' ? ' active' : ''}`} onClick={() => setPosition('top')}>
                    上方
                  </button>
                </div>
              </div>

              {/* 分隔线 */}
              <div style={{ height: 1, background: '#1a1b2a', marginBottom: 14 }} />
              <div style={{ fontSize: 11, fontWeight: 600, color: '#6b6e8a', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 14 }}>
                自定义调节
              </div>

              {/* 描边粗细 */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: '#6b6e8a', marginBottom: 6 }}>描边粗细</div>
                <div style={{ display: 'flex', gap: 5 }}>
                  {[
                    { v: 0, label: '无' },
                    { v: 1, label: '细' },
                    { v: 2, label: '中' },
                    { v: 3, label: '粗' },
                    { v: 4, label: '特粗' },
                  ].map(({ v, label }) => (
                    <button
                      key={v}
                      className={`sube-size-btn${styleOutline === v ? ' active' : ''}`}
                      style={{ width: 'auto', padding: '0 8px', fontSize: 10 }}
                      onClick={() => setStyleOutline(v)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 阴影 */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: '#6b6e8a', marginBottom: 6 }}>阴影</div>
                <div style={{ display: 'flex', gap: 5 }}>
                  {[{ v: 0, label: '关' }, { v: 1, label: '弱' }, { v: 2, label: '强' }].map(({ v, label }) => (
                    <button
                      key={v}
                      className={`sube-size-btn${styleShadow === v ? ' active' : ''}`}
                      style={{ width: 'auto', padding: '0 10px', fontSize: 10 }}
                      onClick={() => setStyleShadow(v)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 加粗 */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: '#6b6e8a', marginBottom: 6 }}>加粗</div>
                <div style={{ display: 'flex', gap: 5 }}>
                  <button
                    className={`sube-size-btn${styleBold ? ' active' : ''}`}
                    style={{ width: 'auto', padding: '0 12px', fontSize: 10 }}
                    onClick={() => setStyleBold(true)}
                  >开</button>
                  <button
                    className={`sube-size-btn${!styleBold ? ' active' : ''}`}
                    style={{ width: 'auto', padding: '0 12px', fontSize: 10 }}
                    onClick={() => setStyleBold(false)}
                  >关</button>
                </div>
              </div>

              {/* 字幕颜色 */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: '#6b6e8a', marginBottom: 6 }}>字幕颜色</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="color"
                    value={styleColor}
                    onChange={e => setStyleColor(e.target.value)}
                    style={{ width: 32, height: 26, border: 'none', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 1 }}
                  />
                  <input
                    value={styleColor}
                    onChange={e => setStyleColor(e.target.value)}
                    style={{
                      flex: 1, background: '#0f1018', border: '1px solid #2a2c42',
                      borderRadius: 6, color: '#d4d7f0', fontSize: 11,
                      padding: '4px 8px', fontFamily: "'JetBrains Mono', monospace",
                      outline: 'none',
                    }}
                    maxLength={7}
                  />
                </div>
              </div>

              {/* 描边颜色 */}
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, color: '#6b6e8a', marginBottom: 6 }}>描边颜色</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="color"
                    value={styleOutlineColor}
                    onChange={e => setStyleOutlineColor(e.target.value)}
                    style={{ width: 32, height: 26, border: 'none', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 1 }}
                  />
                  <input
                    value={styleOutlineColor}
                    onChange={e => setStyleOutlineColor(e.target.value)}
                    style={{
                      flex: 1, background: '#0f1018', border: '1px solid #2a2c42',
                      borderRadius: 6, color: '#d4d7f0', fontSize: 11,
                      padding: '4px 8px', fontFamily: "'JetBrains Mono', monospace",
                      outline: 'none',
                    }}
                    maxLength={7}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  )
}

export default SubtitleEditor

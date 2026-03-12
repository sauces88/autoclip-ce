# xhs-cover-skill

一键生成小红书爆款封面与系列信息图提示词。

## ✨ 功能特性

- **爆款文案优化**：自动生成吸引眼球的标题、正文、标签，融入小红书特有的 emoji 风格。
- **系列图拆解**：智能将长内容拆解为封面、内容图、结尾图的组合，符合用户阅读习惯。
- **多风格生图**：支持多种视觉风格（卡通高饱和、高级莫兰迪），输出高质量 JSON 格式提示词。

## 🎨 风格说明

| 风格 | 关键词 | 说明 |
|------|--------|------|
| **Plan A (默认)** | `默认` / `卡通` | **高饱和卡通信息图**。活泼、可爱、高互动感，适合大众话题、教程。 |
| **Plan B** | `Plan B` / `高级` | **高级配色版 (Pro)**。莫兰迪色系、克制留白、极简排版，适合高知、深度内容。 |

## 📥 安装指南

### 第一步：获取代码

```bash
# 找一个合适的目录存放代码
cd ~/code/skills

# 克隆仓库 (如果适用)
# git clone <your-repo-url>
```

### 第二步：安装到 Claude

#### 方法 A：使用 Openskills CLI (推荐)

会自动处理路径依赖和配置同步。

```bash
# 1. 进入 Skill 目录
cd xhs-cover-skill

# 2. 安装 skill (确保 openskills 已安装)
openskills install .

# 3. 同步配置到 Agent
openskills sync
```

#### 方法 B：Claude 标准安装 (手动)

手动将 Skill 集成到 Claude 项目的标准方式。

```bash
# 1. 定位或创建项目的 skills 目录
mkdir -p YourProject/.claude/skills

# 2. 将整个文件夹复制过去
cp -r xhs-cover-skill YourProject/.claude/skills/

# 3. 验证：确保 SKILL.md 存在于目标目录
ls YourProject/.claude/skills/xhs-cover-skill/SKILL.md
```

## 🚀 如何使用

1. **自然语言触发**
   - "请把这个内容做成小红书封面，Plan B风格"
   - "帮我给这篇食谱配一套小红书图片提示词"
   - "把这个笔记拆解成小红书图"

2. **指定风格**
   - **默认 (Plan A)**：`"生成小红书封面"` -> 输出活泼卡通风格
   - **Plan B**：`"生成小红书封面，风格用Plan B"` -> 输出高级莫兰迪风格

## 🖼️ 输出示例

**JSON 格式输出：**

```json
{
  "title": "标题...",
  "content_polished": "文案...",
  "image_prompts": [
     { "type": "封面", "prompt": "..." },
     { "type": "内容", "prompt": "..." }
  ]
}
```

## ❤️ 致谢与工具推荐

1. **模版致谢**：`references/templates/style_infographic_cartoon.md` 来源宝玉，感谢宝玉开源精神。
2. **批量生图**：生成的 JSON 内容直接粘给 [Gemini](https://gemini.google.com/) 可以批量生产图片。
3. **无水印下载**：想下载无水印图片可参考岚叔开发的插件：[gemini-downloader-extension](https://github.com/cclank/gemini-downloader-extension)

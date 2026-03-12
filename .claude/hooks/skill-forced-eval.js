#!/usr/bin/env node
/**
 * UserPromptSubmit Hook - 强制技能评估 Hook
 * 在用户提交消息时自动注入技能评估指令
 */
const fs = require("fs");

// 技能定义列表
const SKILLS = [
    {
        id: "brainstorming",
        name: "头脑风暴",
        keywords: ["头脑风暴", "脑洞", "创意", "设计", "功能", "新功能", "需求分析", "产品设计"],
        description: "在任何创造性工作前使用 - 创建功能、构建组件、添加功能或修改行为"
    },
    {
        id: "skill-creator",
        name: "技能创作",
        keywords: ["编写skill", "创作skill", "创建skill", "新skill", "skill开发"],
        description: "创建或更新技能时使用"
    },
    {
        id: "frontend-design",
        name: "前端设计",
        keywords: ["前端", "界面", "UI", "组件", "网页", "页面设计", "样式", "布局"],
        description: "创建高质量的前端界面和组件"
    },
    {
        id: "ui-ux-pro-max",
        name: "UI/UX专业设计",
        keywords: [
            "ui", "ux", "界面设计", "用户体验", "设计系统",
            "landing page", "dashboard", "admin panel", "网站",
            "按钮", "导航栏", "侧边栏", "卡片", "表格", "表单",
            "glassmorphism", "minimalism", "响应式", "dark mode", "暗黑模式",
            "配色", "颜色", "字体", "排版", "布局", "动画",
            "shadcn", "tailwind", "react", "vue", "svelte"
        ],
        description: "UI/UX设计智能助手。支持50种设计风格、21种配色方案、50种字体搭配、20种图表。涵盖网站、仪表板、后台管理、电商、SaaS等项目类型。"
    },
    {
        id: "find-skills",
        name: "skill发现",
        keywords: [
            "find skill", "找skill", "有没有skill", "skill搜索", "搜索skill",
            "skill for", "查找skill", "安装skill", "扩展能力", "工具搜索",
            "skills.sh", "npx skills", "怎么做", "能不能", "有没有工具"
        ],
        description: "帮助用户发现和安装代理skill，当用户询问\"如何做X\"、\"有没有skill可以...\"或想要扩展功能时使用"
    },
    {
        id: "xhs-cover-skill",
        name: "封面制作",
        keywords: [
            "cover", "封面"
        ],
        description: "帮助用户生成视频封面"
    }
];

// 读取标准输入
let inputData = "";
try {
    inputData = fs.readFileSync(0, "utf8");
} catch (e) {
    process.exit(0);
}

// 解析输入 JSON
let hookData;
try {
    hookData = JSON.parse(inputData);
} catch (e) {
    process.exit(0);
}

const userMessage = hookData?.prompt || "";

// 检查是否需要跳过评估
const trimmedMessage = userMessage.trim();

// 定义跳过规则
const skipRules = [
    {
        condition: trimmedMessage.startsWith("/"),
        reason: "斜杠命令"
    },
    {
        condition: /^(hi|hello|你好|嗨|hey|谢谢|thanks|thank you|ok|好的|明白|懂了)$/i.test(trimmedMessage),
        reason: "简单问候或确认"
    },
    {
        condition: trimmedMessage.length < 5,
        reason: "消息过短"
    },
    {
        condition: /^[\?？]+$/.test(trimmedMessage),
        reason: "仅包含问号"
    },
    {
        condition: /继续|continue|下一步|next/i.test(trimmedMessage) && trimmedMessage.length < 15,
        reason: "简单的继续指令"
    }
];

// 检查是否应该跳过
for (const rule of skipRules) {
    if (rule.condition) {
        const skipResult = {
            systemMessage: `⏭️ [Hook 跳过] 技能评估已跳过 (${rule.reason})`
        };
        console.log(JSON.stringify(skipResult));
        process.exit(0);
    }
}

// 关键词匹配分析
function analyzeKeywords(message) {
    const lowerMessage = message.toLowerCase();
    const matches = [];

    for (const skill of SKILLS) {
        const matchedKeywords = skill.keywords.filter(keyword =>
            lowerMessage.includes(keyword.toLowerCase())
        );

        if (matchedKeywords.length > 0) {
            matches.push({
                skillId: skill.id,
                skillName: skill.name,
                matchedKeywords: matchedKeywords,
                score: matchedKeywords.length
            });
        }
    }

    // 按匹配得分排序
    matches.sort((a, b) => b.score - a.score);
    return matches;
}

// 分析用户消息中的关键词匹配
const keywordMatches = analyzeKeywords(userMessage);

// 如果没有关键词匹配，跳过评估
if (keywordMatches.length === 0) {
    const skipResult = {
        systemMessage: `⏭️ [Hook 跳过] 无关键词匹配，直接处理请求`
    };
    console.log(JSON.stringify(skipResult));
    process.exit(0);
}

// 生成技能列表文本（带描述和关键词提示）
const skillListText = SKILLS.map((s) =>
    `- **${s.id}** (${s.name}): ${s.description}\n  关键词: ${s.keywords.slice(0, 5).join(", ")}...`
).join("\n\n");

// 生成关键词匹配提示
let keywordHint = "";
if (keywordMatches.length > 0) {
    keywordHint = `\n### 🎯 关键词匹配提示\n根据用户消息中的关键词，以下技能可能相关：\n${keywordMatches.map(m =>
        `- **${m.skillName}** (匹配: ${m.matchedKeywords.join(", ")})`
    ).join("\n")}\n`;
}

// 构建强制评估指令（添加明显标记）
const evalInstructions = `
## 🔧 指令：强制技能激活流程（必须执行）

**重要**：在响应用户之前，你必须先完成技能评估流程。

### 步骤 1 - 分析任务类型
快速分析用户请求的性质：
- 是否涉及创造性工作（新功能、设计、重构）？
- 是否涉及特定领域处理（视频、前端、技能开发）？
- 是否需要专业工具或工作流支持？
${keywordHint}
### 步骤 2 - 评估技能相关性
对照以下可用技能列表，确定是否有技能与任务相关：

${skillListText}

### 步骤 3 - 决策与激活
- ✅ **如果有相关技能** → **立即**使用 \`Skill()\` 工具激活最相关的技能（不要只是提到，必须调用工具）
- ⏭️ **如果没有相关技能** → 直接继续处理用户请求

### 步骤 4 - 执行任务
只有在完成技能评估和激活（如需要）后，才能开始实际实现。

**注意**：技能激活是自动化的一部分，不需要向用户解释评估过程，直接激活即可。

---
**用户原始请求：**`;

// 输出结果
// 使用 hookSpecificOutput.additionalContext 添加上下文，同时用 systemMessage 通知用户
const matchedNames = keywordMatches.map(m => m.skillName).join(", ");
const result = {
    systemMessage: `🎯 [Hook] 检测到关键词匹配: ${matchedNames}`,
    hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext: evalInstructions,
        metadata: {
            matchedSkills: keywordMatches.map(m => m.skillId),
            totalSkills: SKILLS.length,
            analysisTimestamp: new Date().toISOString()
        }
    }
};

console.log(JSON.stringify(result));

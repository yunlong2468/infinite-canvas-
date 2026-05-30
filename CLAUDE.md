# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# 项目信息

## 技术栈
- **后端**: Node.js + Express + sql.js（本地SQLite）
- **前端**: 原生 HTML/JS/CSS（无框架），SVG 画布
- **Python**: 辅助脚本（命名表生成、爬虫桥接）
- **端口**: 3001
- **启动**: `node server.js`

## 目录结构
```
├── server.js              # 主后端（~4800行）
├── public/
│   ├── write.html         # 写作模块前端
│   ├── write.js           # 写作模块JS（~4600行）
│   ├── canvas.html        # 画布模块（独立）
│   └── name_pool.json     # 随机命名表（10KB）
├── prompts/               # 系统提示词（10个.md文件）
│   ├── orchestrator.md    # 五阶段编排师
│   ├── design_worldview.md # 世界观设计Agent
│   ├── character.md       # 角色设计Agent
│   ├── outliner.md        # 大纲生成Agent
│   ├── outliner_volume.md # 卷大纲Agent
│   └── ...
├── scripts/
│   └── generate_name_pool.py  # 随机命名表生成
└── data.db                # SQLite 数据库
```

## 写作模块五阶段工作流
1. **需求采访** → 2. **世界观构建** → 3. **角色设计** → 4. **卷蓝图规划** → 5. **大纲生成**

核心原则：每次只问一个问题、不能跳步、按钮即引导。

## 关键数据库表
- `world_entities` / `world_relations` — 世界观结构
- `writing_characters` / `relationship_edges` — 角色和关系
- `writing_volumes` / `writing_chapters` — 大纲（draft→confirmed）
- `plot_timeline_events` — 时间线事件
- `story_blueprints` — 故事蓝图
- `agent_conversations` — 对话历史

## 关键API前缀
- `/api/writing-projects/:id/` — 写作模块主路由
- 工具调用: `executeToolAsync()` 匹配 toolName 到具体分支
- 阶段门控: `_checkStagePrerequisites()`
- 上下文组装: `_buildAssembledContext()`
- 提示词加载: `loadPrompt('xxx.md')`

# Ivory Lace — Zed 桌宠

一个透明的 Windows 桌面宠物，专为 **Zed 编辑器 cli-acp（Agent Client Protocol）用户**准备：你在 Zed 的 agent 面板里跑 claude-acp、codex-acp 等 agent 时，桌宠会实时用动作反馈 agent 的状态——工作、等待、失败、完成。

**零依赖**：只需要 Python 3.8+（自带 tkinter 的标准安装），**不需要 pip install 任何东西**。

## 快速开始

1. 确认电脑上有 Python：`python --version`（[python.org](https://www.python.org/downloads/) 官方安装包自带 tkinter）
2. **第一次使用先构建宠物形象**（一次性，见下方"素材与构建"）：
   ```powershell
   # 用你自己的图集：
   python build_atlas.py 你的图集.png
   # 或者没有素材，先生成一个占位形象：
   python build_atlas.py --placeholder
   ```
   （这一步需要 Pillow：`pip install Pillow`；**之后运行桌宠不再需要任何安装**）
3. 双击 `run-pet.cmd`
4. 打开 Zed 用 agent 面板跑一个会话，桌宠会跟着动起来

> 桌宠出现但没有反应？双击 `run-pet-console.cmd` 看控制台输出。右键桌宠可暂停监听、测试每个动作或退出；双击桌宠会挥手。

## 素材与构建

**本程序不包含任何宠物素材。** 构建器把任何雪碧图集（sprite sheet）切成桌宠运行所需的 PNG 帧：

- 图集是一个大图，按网格排列每个状态的动作帧；默认布局是 9 行（每行一个状态），单元格 192×208，见 `layout.example.json`
- 图集尺寸和布局与默认不同时，复制 `layout.example.json` 改成自己的行列映射：
  ```powershell
  python build_atlas.py 我的图集.png --layout my-layout.json --scale 1.25
  ```
- 没有任何素材？`python build_atlas.py --placeholder` 会程序化生成一个简单的占位桌宠形象，功能和状态动画完整，之后随时可以用自己的图集替换（重新构建覆盖 `assets/frames/` 即可）
- 构建产物在 `assets/frames/`，这目录只在本机生成、只属于你，**不要随包分发**

## 状态与动作

| 检测到的状态 | 桌宠动作 |
|---|---|
| agent 正在工作（生成/调工具） | 坐在椅子上看书、喝茶 |
| agent 在问你问题 | 捧着鲜花坐在地上 |
| agent 调用失败 | 拿手帕哭泣 |
| 回合完成 | 短暂悬浮庆祝，然后查看结果 |
| Zed 开着但 agent 空闲 | 安静待机，偶尔眨眼/呼吸 |
| 什么都没检测到 | 保持待机 |

## 支持哪些 agent

| Agent | 数据来源 | 说明 |
|---|---|---|
| **Claude Code**（claude-acp） | `~/.claude/projects/**/*.jsonl` | 只读尾随会话记录 |
| **Codex**（codex-acp） | `~/.codex/logs_2.sqlite` | SQLite 只读查询 |
| **Zed 本身** | `Zed.log`（ACP 连接事件） | 提供"Zed 已连接"信号 |

想支持更多 ACP agent（amp、gemini 等）？在 `pet_core.py` 的 `ActivitySource` 协议上加一个源即可——状态机完全通用，与具体 agent 无关。

## 工作原理

- **解耦架构**：`pet_core.py` 是通用核心（状态机 + 事件词汇），三个数据源（`source_claude.py` / `source_codex.py` / `source_zed.py`）各自把 agent 活动翻译成统一的 `activity / final / failure / waiting / resolved` 事件
- **零配置自动检测**：自动探测 `zed.exe` / `claude` / `codex` 进程和日志活性，谁在跑就听谁的
- **不修改任何程序**：不注入、不控制 Zed 或 agent，不写它们的配置、会话或数据库

## 隐私

- 所有数据源**只读**：JSONL 用 `rb` 按字节偏移增量读取；SQLite 用 `mode=ro` + `query_only`；Zed.log 只做增量尾随
- 只提取事件**元数据**（事件类型、时间戳、工具名）；提示词、回答内容、命令参数和工具输出**不会进入桌宠程序**
- 不设置开机自启、不写环境变量、不注册 Hook / MCP / 插件
- 关闭桌宠不会影响 Zed 或正在运行的 agent

## 常见问题

- **显示"未检测到 Zed"**：Zed 没在运行，或 agent 从未启动过。开着 Zed 跑一次 agent 会话即可
- **打开 Zed 的 agent 面板后桌宠没反应**：确认 agent 会话真的开始干活了（桌宠从 transcript 里读事件）；Zed 刚升级后日志结构若变化，桌宠会安全降级为待机，不影响使用
- **双击没反应**：用 `run-pet-console.cmd` 查看报错；`python` 不在 PATH 的话请安装 python.org 官方版本

## 分发给别人

想分享给其他 Zed cli-acp 用户时，运行：

```powershell
python make_dist.py
```

生成 `ivory-lace-zed-pet-dist.zip`——只包含代码、文档和构建器，**不含任何素材**（`assets/`、`pet-settings.json`、缓存都会被排除）。对方拿到后按"快速开始"构建自己的形象即可。

## 给开发者

- `build_atlas.py`：通用图集构建器（任意素材 + 布局 JSON + `--placeholder` 占位生成），需要 Pillow，仅构建时用；运行时零依赖
- `make_dist.py`：分发打包（stdlib zipfile，自动排除素材与个人文件）
- 帧目录结构：`assets/frames/<状态>-<序号>.png`，状态集见 `pet_ui.py` 的 `STATE_NAMES`

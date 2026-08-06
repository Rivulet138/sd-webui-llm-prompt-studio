# LLM Prompt Studio for Stable Diffusion WebUI Forge Neo

面向 Forge Neo 的提示词生成、批处理、缓存和自动生图扩展。插件以实际工作流为中心：连接一次 LLM，批量生成不同 Prompt，按队列写入 txt2img / img2img，并与 Ranbooru、PNG Prompt Collector 联动。

## 核心功能

- 支持 OpenAI Responses、OpenAI Chat Completions、Anthropic、Gemini、OpenRouter、DeepSeek、Ollama、LM Studio 和自定义 OpenAI 兼容接口。
- 单条生成 Danbooru Tag、自然语言、混合 Prompt 和 Regional Prompt 结构。
- 批处理坚持“一行一次请求”：重复要求也会作为独立任务发送，不因缓存或相同输入自动跳过。
- 批次请求会要求模型改变动作、构图、环境、时间天气、道具和叙事内容，禁止只替换风格词制造差异。
- txt2img / img2img 内嵌 `Prompt 批量生成` 面板，可从 LLM 或缓存取词并连续生图。
- 本地 SQLite Prompt 缓存，支持筛选、手动评分、编辑、撤销删除和 JSON / CSV 导入导出。
- 自动发现并同步 Ranbooru Tag / 自然语言缓存，支持实时交接。
- 接收 PNG Prompt Collector 的 `prompt_batch.v1` 批次，逐条润色或扩写。
- 静态词库自动建立和增量刷新索引，无需手动重建。

插件没有 RAG / Few-Shot 生成链路，也没有 LLM 自动评分。缓存评分仅由用户本地手动维护，不会产生额外 Provider 请求。

## 安装

在 Forge Neo 的扩展目录执行：

```powershell
cd E:\sd-webui-forge-neo\extensions
git clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

重新启动 Forge Neo，然后执行一次浏览器强制刷新（`Ctrl + F5`）。

更新：

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-llm-prompt-studio
git pull
```

## 界面说明

### 生成

用于验证单条生成链路和保存通用工作参数。

| 控件 | 作用 |
| --- | --- |
| 创作要求 | 描述需要生成的画面 |
| 源 Danbooru 标签 | 可选；填写后优先作为本次输入 |
| System Prompt 预设 | 选择 Tag、自然语言或混合输出规则 |
| 目标底模 | 追加对应模型的 Prompt 约束 |
| 内容模式 | 选择 SFW / NSFW |
| 高级 Prompt 约束 | 自定义 System Prompt、NSFW 注入和输出要求 |
| 标签后处理 | 去除不良词、通配排除、打乱、下划线转换和数量限制 |
| 缓存 | 决定是否保存结果及本地手动评分 |

`生成提示词` 是一次逻辑生成请求。遇到连接超时、连接重置、HTTP 408/409/425/429 或 5xx 时，服务端最多再重试 2 次，并使用退避等待；结果成功后只写入一次缓存，不会追加评分请求。

### 批处理

#### 服务端批量生成

输入框每行是一条独立创作要求。点击 `预览生成队列` 可检查任务，点击 `开始生成并缓存` 后逐行调用 LLM。

- 一行对应一个独立逻辑请求；临时网络错误最多进行 2 次服务端重试，永久错误不会重试。
- 相同的两行仍会分别调用两次。
- `跳过批次开始前已有缓存` 默认关闭；仅在用户主动勾选时生效。
- 生成结果直接显示在批量结果表格，并按 `0 / unrated` 缓存。
- 单条失败可继续处理；错误和跳过记录支持选择后重新提交。
- 批量取消会阻止下一次重试或下一行请求；当前底层 HTTP 返回或超时后停止，已经完成的结果保留。

#### 浏览器生图队列

可先生成 Prompt 到浏览器队列，再投入 Forge 原生 txt2img / img2img；也可使用 `重新调用 LLM 并立即生图`。

队列只维护当前页面任务状态和浏览器本地缓存，不使用跨标签页 Web Locks、租约或任务所有者判断。

Forge 生图失败或观察超时不会自动再次点击生图按钮，因为生图操作可能已经在 Forge 内部完成，自动重放会造成重复图片。对应队列记录会恢复为 `pending`，确认 Forge 已空闲后可用 `投入已有队列生图` 显式重试。

#### PNG 润色 / 扩写

接收 PNG Prompt Collector 发送的 `prompt_batch.v1` JSON：

1. 导入批次。
2. 选择扩写或润色。
3. 批量处理，保持一张图片对应一条记录。
4. 使用 `追加并下一条` 将结果写入 txt2img / img2img。

相同输入可复用同一次处理结果；已有 `processed` 的记录不会再次发送到 LLM。取消按钮直接停止当前批次，不维护任务 ID 或所有者。

#### 直接批量导入

每行一个 Prompt，也支持：

```text
8.5<TAB>prompt text
```

无法解析的分数按 Prompt 正文处理。导入评分属于本地手动评分。

### 缓存与联动

缓存页提供：

- 按关键词、最低手动评分、输出格式和目标底模筛选。
- 选择、查看、编辑、另存、删除和撤销上次删除。
- 按全库可见序号预览和删除。
- JSON / CSV 导入导出。
- Ranbooru 缓存自动检测、预览和同步。
- Ranbooru 实时交接箱：载入生成页、使用 LLM 处理并缓存、跳过或清理。

Ranbooru 的源评分只用于筛选源记录，不会作为本插件 Prompt 评分。同步的新记录和发生变化的记录按未评分保存。

### 连接设置

每个 Provider 独立保存：

- 接口地址
- 模型 ID
- 温度
- 超时
- 最大输出 Token
- 是否发送温度参数
- API Key

API Key 写入 `user/credentials.json`，界面不会回填明文。`测试 API` 只进行一次最小连接请求。

### 工具

#### 静态词库

启动插件、打开页面或修改词库目录时都会自动增量索引。搜索框只查询已经建立的本地索引。

默认词库位于：

```text
assets/wildcards/
```

#### WD14 + LLM

调用已安装的 Forge WD14 Tagger 获取标签，再使用当前 LLM 设置扩写或润色。WD14 和 LLM 是两个连续步骤，任一步不可用都会返回明确状态。

## txt2img / img2img 内嵌面板

内嵌面板只保留无限生成联动需要的控件：

- 本轮创作要求
- System Prompt 预设
- 目标底模
- 内容模式：`SFW` / `NSFW`
- Prompt 来源：`LLM 自动生成` / `缓存顺序读取`
- Ranbooru 固定基底追加
- 轮数（`0` 表示持续）
- `取一条并写入`、`开始连续生成`、`停止`

三个生成选项与独立 `LLM Prompt Studio` 页使用同一组选项，并自动载入已保存的工作参数；在生成页、批处理页或任一内嵌面板修改时，其他面板会同步更新。详细 Provider、自定义 System Prompt 和后处理参数仍统一在独立页面保存。

### Ranbooru 式追加

连续任务开始时冻结当前正向 Prompt 作为基础。追加模式的每一轮都独立组合：

```text
第 1 轮 = 基础 Prompt + 本轮 Prompt A
第 2 轮 = 基础 Prompt + 本轮 Prompt B
第 3 轮 = 基础 Prompt + 本轮 Prompt C
```

上一轮的 A 不会进入下一轮，也不会无限累积。内嵌面板不再提供覆盖模式。

## 输出预设

| 预设 | 输出 |
| --- | --- |
| Danbooru Tags | 逗号分隔 Tag Prompt |
| Danbooru + Natural | Tag 与自然语言组合 |
| Natural Language | 普通自然语言描述 |
| NoobAI Tags | 面向 NoobAI 的 Tag 规则 |
| Anima Tags | 面向 Anima 的 Tag 规则 |
| Krea 2 Natural | 面向 Krea 2 的自然语言描述 |

结构化输出可选 `Plain Prompt`、`Regional JSON` 或 `Regional Markdown`。

## 本地 API

插件注册以下接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/llm-prompt-studio/v1/generate` | 使用已保存连接生成 Prompt |
| GET | `/llm-prompt-studio/v1/cache` | 查询本地缓存 |
| POST | `/llm-prompt-studio/v1/handoff` | 接收 Ranbooru 交接 |
| POST | `/llm-prompt-studio/v1/handoff/process` | 处理并缓存交接记录 |
| GET | `/llm-prompt-studio/v1/handoffs` | 查询交接箱 |

默认只允许本机访问。Forge 配置 `--api-auth` 后，远程调用必须通过对应 Basic Auth。API 不接受请求正文中的 API Key，也不能临时切换到未保存的 Provider 或地址。

最小生成请求：

```json
{
  "request": "one young girl in a complex railway station scene",
  "preset": "NoobAI Tags",
  "base_model": "NoobAI",
  "cache_result": false
}
```

## 数据目录

```text
user/
├── prompt_studio.db
├── credentials.json
├── exports/
└── backups/
```

- `prompt_studio.db`：Prompt、工作设置、静态词库索引和交接记录。
- `credentials.json`：按 Provider 与 URL 保存的凭据。
- `exports/`：JSON / CSV 导出。
- `backups/`：删除操作创建的可恢复备份。

旧数据库中的 `score_source=llm`、评分模型和评分理由字段仅用于历史兼容；当前版本不会生成新的 LLM 评分。

## 联动插件

- [sd-webui-ranbooru-Forge-neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)
- [sd-webui-png-prompt-collector](https://github.com/Rivulet138/sd-webui-png-prompt-collector)
- [stable-diffusion-webui-wd14-tagger](https://github.com/toriato/stable-diffusion-webui-wd14-tagger)

三个插件均可独立运行。缺少联动插件时，相应按钮会报告目标不可用，不影响本插件基础生成和缓存功能。

## 开发与验证

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-llm-prompt-studio
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m unittest discover -s tests -v
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m ruff check scripts tests
node --check javascript/llm_prompt_studio_auto_loop.js
node tests/auto_loop_runtime_test.js
```

当前回归套件覆盖 Provider 请求与响应适配、API、缓存、批处理独立请求、Ranbooru 交接、PNG 批次、自动词库索引、Ranbooru 式追加、取消和浏览器队列。

## 已知边界

- 模型 ID 必须手动填写，插件不自动枚举 Provider 模型。
- LLM 请求开始后无法中断底层 HTTP 连接；取消会在当前请求返回或超时后生效，并阻止后续退避重试。
- 浏览器队列保存在当前浏览器的 `localStorage`；清理站点数据会移除队列。
- 修改 Python 或 JavaScript 后需要重启 Forge，并使用 `Ctrl + F5` 强制刷新。

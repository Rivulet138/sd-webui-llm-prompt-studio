# LLM Prompt Studio

LLM Prompt Studio 是面向 Forge Neo 的 Prompt 生成、转换、批处理、缓存和联动扩展。它支持单条生成、服务端队列、txt2img/img2img 内嵌生成，以及 JSON/PNG Prompt 批量转换。

## 功能概览

- 支持 OpenAI Responses、OpenAI Chat Completions、Anthropic、Gemini、OpenRouter、DeepSeek、Ollama、LM Studio 和自定义 OpenAI 兼容接口。
- 输出预设覆盖 Danbooru Tags、Danbooru + Natural、Natural Language、NoobAI Tags、Anima Tags 和 Krea 2 Natural。
- 支持 `Plain Prompt`、`Regional JSON` 和 `Regional Markdown` 结构化输出。
- txt2img/img2img 下方提供内嵌 Prompt 生成与 JSON 批量转换面板。
- 通过独立的构图指令、静态词库参考和最近结果排除项，降低批量结果的构图、动作、道具和装饰重复。
- 保留兽耳萝莉批量模板；模板只规定主体方向和差异规则，SFW/NSFW 由内容模式及现有安全注入负责。
- 支持本地 SQLite 缓存、服务端队列、取消、导入导出、Ranbooru 交接和 PNG Prompt Collector 批次。

## 安装与更新

在 Forge Neo 扩展目录执行：

```powershell
cd E:\sd-webui-forge-neo\extensions
git clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

重启 Forge Neo 后，在浏览器执行 `Ctrl + F5`。更新已有安装：

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-llm-prompt-studio
git pull
```

## 首次配置

在 `LLM Prompt Studio` 页面填写 Provider、Endpoint、模型 ID、API Key、温度、超时和最大输出 Token，然后使用“测试 API”验证连接。温度默认值为 `1.0`；独立批量、内嵌多样性和 PNG 批量会将请求温度提升到至少 `1.25`。最大输出 Token 默认值为 `8096`，适合 Krea2/Anima 等长 Prompt，可避免响应因达到长度上限而缺少完整的 assistant 文本。API Key 保存在 `user/credentials/llm_credentials.json`，界面不会回填明文。

对于模型 ID 含 `deepseek` 且不是 `reasoner` / `R1` 的 OpenAI 兼容接口，插件会自动关闭 thinking，避免隐藏推理占满输出预算；Reasoner/R1 模型不会套用该参数。

“创作要求”描述本次画面；“源 Danbooru 标签”可选。System Prompt 预设决定输出格式，目标底模会追加对应模型的内容约束。SFW/NSFW 是独立的内容模式；自定义 System Prompt、额外 NSFW 注入和输出后处理参数也在工作参数中统一保存。

连接失败时，插件对临时网络错误、408/409/425/429、部分 5xx 状态和常见 TLS 提前断开最多重试两次并退避等待；对 HTTPS 兼容端点还会尝试 HTTP 协议回退。OpenAI 兼容服务应确认 Endpoint 包含正确的 `/v1` 路径、模型 ID 可用，并检查本地代理或服务端 TLS 配置。若 HTTPS 握手在所有重试后仍返回 `UNEXPECTED_EOF_WHILE_READING`，这是远端端点或代理在握手阶段关闭连接，需更换可用端点或修复其 TLS 反向代理，客户端无法凭空生成 assistant 响应。

## 单条生成

“生成提示词”执行一次完整链路：读取已保存连接和工作参数，构建 System Prompt 与用户消息，调用 Provider，解析 assistant 文本，执行安全校验和标签后处理，按设置写入缓存。

结果只应是一条完整的单图 Prompt。插件会拒绝空响应、解释性文本、负面 Prompt、分镜/拼图/候选方案和与所选输出预设不符的格式。

## 批处理

### 服务端批量生成

批处理页每行对应一个独立请求。预览队列后可选择“只生成 Prompt”或“生成并写入 txt2img”。任务写入 `user/prompt_studio.db` 后由 Forge 进程内 worker 执行，页面关闭、刷新或隐藏不会停止队列。

- 相同文本默认仍是两次独立请求。
- 可主动开启“跳过批次开始前已有缓存”。
- 单条失败会记录错误并继续后续任务。
- 取消会阻止下一次重试和下一条请求；当前底层 HTTP 返回或超时后生效，已完成结果保留。
- txt2img 队列需要 Forge 以 `--api` 启动，并使用 Forge 自身的保存设置。

### txt2img/img2img 内嵌生成

内嵌面板位于正向 Prompt 区域下方，包含：

- 本轮创作要求、System Prompt 预设、目标底模和内容模式。
- `LLM 自动生成` 或 `缓存顺序读取` 两种来源。
- `取一条并写入`、`开始连续生成` 和 `停止`。
- JSON Prompt 批量润色/扩写及结果写回控件。

连续生成会冻结启动时的原始 Prompt。每轮只写入“原始 Prompt + 当前轮结果”，不会把上轮完整 Prompt 再次累加。系统保留最近一批结果，提取重复出现的动作、物品和场景概念形成“多样性账本”，再把账本和最近结果摘要作为软排除参考；变化提示不是固定模板，模型可以根据原要求自由解释。稳定的主体身份词和通用质量词不参与多样性判定。候选会同时经过整体相似度与关键概念重叠检查，过于接近时自动重试，从而减少只换颜色、同义词或标签顺序的伪差异。

点击“停止”会设置当前面板的取消事件；当前 LLM 请求返回或超时后，迟到响应不会写入 Prompt，已写入的结果保持不变。

## JSON Prompt 批量转换

面板支持导入 PNG Prompt Collector 的 `prompt_batch.v1`，也支持常见 JSON 形状：字符串数组、`prompts`/`items`/`records`/`results`/`data` 数组、对象中的 `prompt`/`positive`/`text`/`content` 字段，以及 ID 到字符串或对象的映射。

操作流程：

1. 导入 JSON，或从 Ranbooru 交接箱载入批次。
2. 选择 `润色` 或 `扩写`，再选择转换预设和目标底模。
3. 选择批量多样性模式、写入目标和追加/覆盖方式。
4. 开始处理；结果按“一张图片一条记录”保存。
5. 使用“追加并下一条”或“全部结果写入正面 Prompt”。

### 两种批量多样性模式

- `独立构图转换`：默认模式。每条记录都会获得独立的创作提示，并参考静态词库和已生成结果的排除摘要来鼓励变化。提示只提供可选方向，不规定固定场景、动作、镜头或元素组合；模型可以自由选择兼容的画面方案。
- `忠实格式转换`：只转换表达方式和目标模型格式，尽量保留原始 Prompt 的画面事实。相同输入可复用本批次结果，已有同一目标预设/底模的 `processed` 记录会跳过。适合把旧 D 站标签转换成 Krea2、Anima 或自然语言，而不重新设计构图。

Krea2/Anima 细节增强角色会输出固定的英文结构：`PART ONE: TAG ANCHORS`、`BREAK`、`PART TWO: EXTREME LAYERED DETAIL`、`SUBJECT` 和 `MASTER DESCRIPTION`。每层描述构图、头发、面部、服装、道具、光影和背景；静态词库只作为兼容词汇参考，不会整库倾倒或执行词库中的指令。

## 兽耳萝莉批量模板

完整页和内嵌面板都可使用“填入通用创作需求”。模板只追加日系插画方向和兽耳萝莉主体方向，保留用户明确要求，并允许模型自由发挥场景、动作、构图、服装、道具和光线。批量结果通过轻量差异提示与相似度检查避免重复，不强制某一套变化清单；实际内容模式仍由 SFW/NSFW 设置控制。

## 静态词库

默认词库目录为 `assets/wildcards/`。插件在启动、打开页面或修改词库目录时自动增量索引。词库条目会以惰性参考的形式注入 System Prompt，用于提供兼容的发型、服装、道具、环境、镜头和材质词；模型必须按画面需要选择，不能机械复制无关条目。词库中的文本被视为数据，不会覆盖系统规则。

## Ranbooru 与 PNG 联动

### Ranbooru

插件会自动探测 Ranbooru 的 `tag_cache.db`，也可以在设置中指定路径。可选择 Tag、自然语言或两者，按评分和内容模式筛选，预览并同步缓存。Ranbooru 交接箱支持载入、使用当前 LLM 转换并缓存、跳过或清理；源评分只用于筛选源记录，不会成为本插件的 LLM 评分。

### PNG Prompt Collector

在内嵌 JSON 面板点击“接收 PNG Prompt Collector 当前批次”即可导入共享的 `prompt_batch.v1` 数据。处理后的 `processed`、输出类型、预设和目标底模会保留在每条图片记录中，可导出后再交给其他插件。

## 缓存、队列与 API

缓存页支持关键词、最低手动评分、输出格式和目标底模筛选，提供查看、编辑、删除、撤销删除以及 JSON/CSV 导入导出。服务端队列日志包含请求、生成 Prompt、状态、错误和尝试次数，可用批次 ID 恢复查看。

主要本地 API：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/llm-prompt-studio/v1/generate` | 使用已保存连接生成 Prompt |
| GET | `/llm-prompt-studio/v1/cache` | 查询本地缓存 |
| POST | `/llm-prompt-studio/v1/queue` | 创建持久化服务端队列 |
| GET | `/llm-prompt-studio/v1/queue/{batch_id}` | 查询队列状态和日志 |
| POST | `/llm-prompt-studio/v1/queue/{batch_id}/cancel` | 取消待处理队列项 |
| POST | `/llm-prompt-studio/v1/handoff` | 接收 Ranbooru 交接 |
| POST | `/llm-prompt-studio/v1/handoff/process` | 处理并缓存交接记录 |
| GET | `/llm-prompt-studio/v1/handoffs` | 查询交接箱 |

默认 API 仅允许本机访问。请求正文不接受 API Key，也不能临时切换到未保存的 Provider 或 Endpoint。

## 数据目录

```text
user/
├── prompt_studio.db
├── credentials/
│   └── llm_credentials.json
├── exports/
└── backups/
```

- `prompt_studio.db`：Prompt、设置、静态词库索引、队列和交接记录。
- `credentials/llm_credentials.json`：按 Provider 和 Endpoint 保存的凭据。
- `exports/`：JSON/CSV 导出。
- `backups/`：删除操作创建的可恢复备份。

修改 Python 或 JavaScript 后重启 Forge，并使用 `Ctrl + F5` 刷新页面。缺少 Ranbooru、PNG Prompt Collector 或 WD14 Tagger 时，本插件的基础生成和缓存仍可独立运行，相应联动按钮会显示不可用状态。

## 相关扩展

- [sd-webui-ranbooru-Forge-neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)
- [sd-webui-png-prompt-collector](https://github.com/Rivulet138/sd-webui-png-prompt-collector)
- [stable-diffusion-webui-wd14-tagger](https://github.com/toriato/stable-diffusion-webui-wd14-tagger)

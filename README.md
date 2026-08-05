# LLM 提示词工作室 for Stable Diffusion WebUI Forge Neo

面向 Stable Diffusion WebUI Forge Neo 的本地优先提示词扩展。插件把 LLM 提示词生成、模型专属规则、静态 Danbooru Tag 词库、本地高分 Prompt RAG、Few-Shot、Ranbooru 缓存联动、批量缓存、WD14 Tagger 和区域提示词结构化输出整合到同一套中文界面中。

插件提供两个入口：

- Forge 顶部独立页签 `LLM 提示词工作室`，用于完整配置、生成、批量任务和缓存管理。
- txt2img 与 img2img 正向提示词区域中的内嵌面板，位于 Ranbooru 区域之后、原生负面提示词之前，可以直接把 LLM 结果写入正向提示词。
- `PNG Prompt Collector` 可把历史 PNG 按一图一条发送到 `批处理 > PNG 润色 / 扩写`，Prompt Batch 不设置批次数量上限，处理后可逐条追加到 txt2img / img2img 正向 Prompt。

运行时只使用 Python 标准库以及 Forge 已提供的 Gradio、FastAPI 和 Pillow，不需要额外安装 Python 包。

## 功能概览

- 六种 System Prompt 输出预设：Danbooru 标签、Danbooru + 自然语言、自然语言、NoobAI 标签、Anima 标签、Krea 2 自然语言。
- NoobAI、Anima、Krea 2、Flux、Pony / Illustrious 与自动底模规则，不包含旧 SD1.5 或 SDXL 配置档。
- 固定 Prompt Policy 优先级，用户要求为低优先级约束，RAG 和静态词库只作为参考数据。
- SFW / NSFW 模式、NSFW 专用 System Prompt 注入和生成结果的本地 SFW 关键词校验。
- OpenAI Responses、OpenAI Chat Completions、Anthropic Messages、Google Gemini、OpenRouter、DeepSeek、Ollama、LM Studio 和自定义 OpenAI 兼容接口。
- Provider、URL、模型 ID、温度、超时、最大输出 Token 和 API Key 的独立持久化。
- 完整工作参数保存与自动恢复，重新打开 Forge 后无需重复填写。
- 插件内置 74 个分类 Tag 词库文件，也支持选择其他本地词库目录。
- 本地 SQLite 高分 Prompt 库、LLM 质量评分与评分溯源、稀疏向量 RAG 和 Few-Shot 示例注入。
- 自动发现并只读同步 `sd-webui-ranbooru-reforge` 的 Tag 与自然语言缓存，支持分级、源评分、数量限制、幂等更新和来源溯源。
- 接收 Ranbooru 当前缓存记录的实时结构化交接，可直接载入生成页，或使用已保存的 LLM 参数处理、评分并缓存。
- Ranbooru 风格标签处理：移除不良标签、自定义通配排除、去重、随机打乱、下划线转空格和最大标签数量。
- 独立的批量缓存页面，支持 LLM 批量生成、直接批量导入、队列预览、单次请求、错误后跳过、问题汇总、勾选后明确重新提交、取消和进度记录。
- `自动生图循环` 将 Prompt 批量生成与 Forge 原生 txt2img / img2img 生图分成两个阶段，可先保存队列，再确认投入生图。
- 独立的缓存库页面，支持筛选、多选、查看、编辑、另存、删除预览、撤销和 JSON / CSV 导入导出。
- 调用已安装的 WD14 Tagger API，并使用 LLM 扩写或润色反推标签。
- 输出 Regional JSON 或 Regional Markdown，为多人、分区和 Regional Prompter 工作流提供结构化数据。
- 提供受 Forge `--api-auth` 保护的本地 API，方便脚本或其他工具测试和调用。

## 目录

- [安装与更新](#安装与更新)
- [界面入口](#界面入口)
- [首次使用](#首次使用)
- [System Prompt 与规则优先级](#system-prompt-与规则优先级)
- [LLM Provider 设置](#llm-provider-设置)
- [参数保存与自动恢复](#参数保存与自动恢复)
- [提示词生成工作流](#提示词生成工作流)
- [标签后处理](#标签后处理)
- [静态 Tag 词库](#静态-tag-词库)
- [本地 RAG 与 Few-Shot](#本地-rag-与-few-shot)
- [批量缓存](#批量缓存)
- [Ranbooru 缓存联动](#ranbooru-缓存联动)
- [缓存库管理](#缓存库管理)
- [WD14 Tagger 与 LLM](#wd14-tagger-与-llm)
- [Regional Prompter 结构化输出](#regional-prompter-结构化输出)
- [本地 API](#本地-api)
- [数据目录与安全](#数据目录与安全)
- [开发与测试](#开发与测试)
- [常见问题](#常见问题)
- [已知边界](#已知边界)

## 安装与更新

### 安装

关闭 Forge Neo，进入它的 `extensions` 目录：

```powershell
cd E:\sd-webui-forge-neo\extensions
git clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

重新启动 Forge Neo。启动日志中出现类似以下内容，表示内置词库已完成检查或索引：

```text
[LLM Prompt Studio] wildcard library ready: ...
```

### 更新

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-llm-prompt-studio
git pull --ff-only
```

更新后重新启动 Forge Neo。运行数据保存在插件的 `user` 目录中，正常 `git pull` 不会覆盖该目录。

### 安装要求

- Stable Diffusion WebUI Forge Neo。
- 一个可以访问的 LLM 服务及其模型 ID。
- 使用 WD14 功能时，需要另外安装并启用 WD14 Tagger 扩展。
- 使用 Ranbooru 缓存联动时，需要安装 `sd-webui-ranbooru-reforge`，或提供兼容的 `tag_cache.db` 路径。
- 使用区域提示词时，Regional Prompter 是可选扩展；本插件只负责生成结构化结果，不会直接控制 Regional Prompter 的界面。

## 界面入口

### 独立管理页

Forge 顶部的 `LLM 提示词工作室` 包含六个一级页签：

| 页签 | 用途 |
| --- | --- |
| `生成提示词` | 单次生成、System Prompt 预览、标签处理和 RAG 设置 |
| `批量缓存` | LLM 批量生成或直接导入本地 Prompt |
| `缓存库` | 筛选、选择、编辑、删除、撤销、导入和导出 |
| `LLM 连接` | Provider、URL、模型、采样参数和凭据管理 |
| `静态词库` | 建立索引、切换词库目录和搜索词条 |
| `WD14 + LLM` | 图片反推标签，以及 LLM 扩写或润色 |

### txt2img / img2img 内嵌面板

内嵌面板会捕获 Forge 原生正向提示词组件，并在负面提示词之前创建 `LLM 提示词工作室` 折叠区。

点击 `生成并写入正向提示词` 后，生成结果会同时显示在插件输出框中，并写入当前 txt2img 或 img2img 的正向提示词。

如果 Forge 或其他扩展修改了正向提示词组件 ID，日志可能显示“未找到正向提示词组件”，此时独立管理页仍然可以正常使用。

## 首次使用

推荐按照以下顺序完成首次配置：

1. 打开 `LLM 连接`。
2. 选择 Provider，填写接口地址、模型 ID 和 API Key。
3. 点击 `测试 API`，确认返回连接成功。
4. 点击 `保存全部 LLM 设置`。
5. 打开 `生成提示词`。
6. 选择输出预设和目标底模，例如 `NoobAI 标签` + `NoobAI`。
7. 选择 `SFW` 或 `NSFW`，按需填写用户输出要求。
8. 输入创作要求，点击 `生成提示词`。
9. 确认结果后，点击 `保存全部工作参数`。

以后重新打开 Forge 时，Provider 设置、URL、模型和工作参数都会自动恢复。API Key 不会显示在浏览器输入框中，但可以在 Provider 与 URL 完全匹配时从本地凭据存储复用。

## System Prompt 与规则优先级

插件的 System Prompt 不是简单拼接文本。最终规则按照固定权限顺序组织：

1. Prompt Policy 与安全规则。
2. 目标底模规则。
3. 输出预设规则。
4. 用户输出要求。
5. 本地 RAG 示例和静态词库参考数据。

这意味着：

- `用户输出要求` 明确是低优先级约束，不能覆盖安全规则、模型规则或输出格式。
- RAG 和静态词库只提供格式、用词和具体程度参考，不能向模型发布指令。
- 自定义 System Prompt 会替换所选输出预设，但不会移除 Prompt Policy、底模规则、安全模式、用户要求或参考数据边界。
- 用户创作要求会被编码在 `<user_image_request priority="low">` 数据区块中。
- RAG 与词库内容会进行转义，不能通过伪造 XML 结束标签提升权限。

界面中的 `最终 System Prompt` 可以用于检查本次请求实际发送了哪些规则和参考数据。

### 输出预设

| 中文选项 | 内部名称 | 输出方式 | 适用场景 |
| --- | --- | --- | --- |
| Danbooru 标签 | `Danbooru Tags` | 单行、逗号分隔、规范小写 Tag | Booru / 动漫标签模型 |
| Danbooru 标签 + 自然语言 | `Danbooru + Natural` | Tag 在前，简短自然语言补充在后 | 需要标签控制和材质、氛围描述 |
| 自然语言 | `Natural Language` | 一段紧凑自然语言 | Flux、通用文生图模型 |
| NoobAI 标签 | `NoobAI Tags` | NoobAI 顺序与锚点规则 | NoobAI-XL 系列 |
| Anima 标签 | `Anima Tags` | Danbooru 优先的动漫提示词 | Anima 风格模型 |
| Krea 2 自然语言 | `Krea 2 Natural` | 一段按视觉顺序组织的自然语言 | Krea 2 |

### 目标底模规则

| 目标底模 | 主要约束 |
| --- | --- |
| 自动 / 使用底模默认规则 | 遵循输出预设，主体信息优先，不主动增加权重 |
| Pony / Illustrious | 角色、特征、服装、动作、场景、镜头顺序；不自动增加 `score_*` 或来源标签 |
| NoobAI | 规范 Danbooru Tag、少量质量/分级/年代/来源锚点、禁止 Pony `score_*` |
| Flux | 直接自然语言，不使用 Danbooru Tag 堆叠和空泛质量词 |
| Anima | 角色一致性和可读构图优先，避免冗长质量词 |
| Krea 2 | 媒介、主体、动作、场景、构图、光线、材质和风格顺序，不使用权重 |

### NoobAI 专属规则

推荐组合：

```text
System Prompt 预设：NoobAI 标签
目标底模：NoobAI
```

NoobAI 输出顺序：

1. 2-4 个兼容且不重复的质量、分级、年代或来源锚点。
2. 人物数量与身份。
3. 角色外观和辨识特征。
4. 服装。
5. 动作与表情。
6. 场景和物体。
7. 构图与镜头。
8. 光线。
9. 风格细节。

权重规则：

- 最多使用三个显式 `(tag:weight)`。
- 权重范围为 `1.05-1.20`。
- 只为真正需要强调的视觉特征加权。
- 质量、分级、年代和来源锚点不加权。
- 禁止输出 Pony `score_*` 标签和虚构艺术家标签。

### SFW 与 NSFW

`SFW` 模式会要求模型避免成人、裸露、性行为、恋物和性化内容，并在 LLM 返回后进行本地关键词检查。命中本地阻止词时，结果不会写入提示词或缓存。

`NSFW` 模式允许使用 `NSFW System Prompt 注入` 增加本地工作流约束上层规则。NSFW 注入只在明确选择 `NSFW` 时加入最终 System Prompt。

## LLM Provider 设置

### 支持的 Provider

| Provider | 默认 Base URL | 协议 | API Key |
| --- | --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | Responses API | 必需 |
| OpenAI Chat Completions | `https://api.openai.com/v1` | Chat Completions | 必需 |
| OpenRouter | `https://openrouter.ai/api/v1` | OpenAI Chat 兼容 | 必需 |
| Anthropic Claude | `https://api.anthropic.com` | Messages API | 必需 |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta` | generateContent | 必需 |
| DeepSeek | `https://api.deepseek.com` | OpenAI Chat 兼容 | 必需 |
| Ollama 本地服务 | `http://127.0.0.1:11434` | `/api/chat` | 默认不需要 |
| LM Studio 本地服务 | `http://127.0.0.1:1234/v1` | OpenAI Chat 兼容 | 默认不需要 |
| 自定义 OpenAI 兼容接口 | `http://127.0.0.1:1234/v1` | OpenAI Chat 兼容 | 取决于服务端 |

### 设置字段

- `接口地址`：填写服务的 Base URL。必须是绝对 `http://` 或 `https://` 地址，不能包含账号密码、查询字符串或 URL Fragment。
- `模型 ID`：填写服务端实际公开的模型名称。插件不会自动枚举模型。
- `API Key`：保存后不会回填到浏览器；以后可以留空复用。
- `温度`：范围 `0-2`。
- `超时秒数`：范围 `5-600`。
- `最大输出 Token`：设为 `0` 时尽量使用 Provider 默认值。
- `发送温度参数`：推理模型或兼容服务拒绝 temperature 时关闭。

插件会识别 Base URL 是否已经包含最终 API 路径，避免重复添加 `/responses`、`/chat/completions`、`/v1/messages`、`/models/...:generateContent` 或 `/api/chat`。

### Token 参数映射

不同 Provider 的最大输出参数名称不同：

- OpenAI Responses：`max_output_tokens`。
- OpenAI Chat Completions：`max_completion_tokens`。
- OpenAI 兼容、OpenRouter、DeepSeek、LM Studio：`max_tokens`。
- Anthropic：`max_tokens`，协议必填；界面值为 `0` 时回退到 `1024`。
- Gemini：`generationConfig.maxOutputTokens`。
- Ollama：`options.num_predict`，并强制使用非流式返回。

### Provider 设置与凭据保存

每个 Provider 独立保存以下内容：

- Base URL。
- 模型 ID。
- 温度。
- 超时。
- 最大输出 Token。
- 是否发送温度。
- 与 Provider + URL 精确绑定的 API Key。

切换 Provider 时，界面会自动载入该 Provider 上次保存的设置。API Key 只有在 Provider 和标准化 URL 同时匹配时才会复用，避免把一个服务的凭据发送给另一个服务。

`清除已保存的 API Key` 只删除当前 Provider 与 URL 对应的凭据。

插件只读取 `llm_connections_v2`。旧 `llm_connection` 设置会在连接配置加载时自动删除，不再参与回退。旧版单凭据文件会在下一次保存 API Key 时迁移到多 Provider 格式。

## 参数保存与自动恢复

可用的保存按钮：

- `生成提示词` 页：`保存全部工作参数`。
- `生成提示词` 页：`恢复默认工作参数`。
- `批量缓存` 页：`保存批量与工作参数`。
- txt2img / img2img 内嵌面板：`保存提示词参数`。

完整页保存以下内容：

| 参数组 | 保存内容 |
| --- | --- |
| Prompt | 输出预设、自定义 System Prompt、目标底模、SFW / NSFW、NSFW 注入、用户输出要求 |
| 结构化输出 | 普通 / Regional JSON / Regional Markdown、区域数量 |
| 标签处理 | 移除不良标签、额外排除规则、随机打乱、下划线转换、最大标签数 |
| RAG 与缓存 | Few-Shot 数量、最低评分、LLM 自动评分、手动备用评分、是否缓存 |
| 批量任务 | 跳过已有输入、错误后继续和本地评分；每条去重输入只请求一次 |
| WD14 | API 地址、模型、阈值 |
| 静态词库 | 当前词库目录 |

Ranbooru 联动参数使用独立设置保存，包括数据库路径、同步内容、分级、最低源评分、读取上限，以及 Tag / 自然语言各自的输出预设和目标底模。点击 `保存联动参数`、`预览 Ranbooru 缓存` 或 `同步到本插件缓存` 都会保存当前值。

不会保存：

- 创作要求正文。
- 源标签正文。
- 批量队列正文。
- 待处理图片。
- 浏览器中的 API Key 输入值。

API Key 和 LLM 连接参数使用独立的凭据与 Provider 设置存储，不会混入工作参数档。

## 提示词生成工作流

### 单次生成

1. 在 `创作要求` 中描述画面。
2. 可选填写 `源 Danbooru 标签`。
3. 选择输出预设、目标底模和内容模式。
4. 按需设置用户输出要求、标签后处理、RAG 和缓存评分。
5. 点击 `生成提示词`。
6. 检查 `生成的提示词`、`最终 System Prompt` 和状态信息。

如果填写了 `源 Danbooru 标签`，它会优先于 `创作要求` 成为本次 LLM 请求、RAG 检索、静态词库匹配和缓存源标签。当前版本不会自动把两个输入合并。

### 内嵌生成并写入 Forge

在 txt2img 或 img2img 中展开 `LLM 提示词工作室`：

1. 输入创作要求或源标签。
2. 选择参数。
3. 点击 `生成并写入正向提示词`。

生成成功后会覆盖当前正向提示词组件的内容。建议在生成前保存需要保留的原提示词。

### LLM 扩写与润色

`WD14 + LLM` 页支持两种操作：

- `扩写`：保留明确事实和输出格式，增加兼容的视觉细节。
- `润色`：提高提示词清晰度、视觉具体性和模型兼容性，不增加没有依据的事实。

## 标签后处理

后处理应用于 `Danbooru Tags`、`NoobAI Tags` 和 `Anima Tags` 结果。自然语言和混合预设不会执行整行 Tag 清理。

### 移除不良标签

默认不良标签包括：

```text
watermark, signature, text, english text, chinese text,
speech bubble, commentary, username, artist name, logo,
copyright name, website, translation request, sample watermark
```

### 额外排除标签 / 通配规则

使用逗号分隔，支持 `*` 通配：

```text
watermark, * text, artist *
```

匹配时会先把待处理 Tag 的下划线转换为空格并忽略大小写，因此通配规则也应使用空格形式。例如 `english_text` 应使用 `* text` 匹配。

### 其他处理

- 自动移除重复 Tag，保留第一次出现的位置。
- `随机打乱标签` 使用系统随机源重新排列结果。
- `将“_”转换为空格` 把 `blue_eyes` 转为 `blue eyes`。
- `最大标签数` 在处理结束后截断；`0` 表示不限。

## 静态 Tag 词库

默认词库目录：

```text
assets/wildcards
```

当前内置 74 个文本文件，覆盖：

- 人物、角色数量、头发、眼睛和嘴巴。
- 动作、手部、腿部、姿势和互动。
- 室内、城市、建筑和自然场景。
- 上衣、外套、裙裤、鞋袜、制服、主题服饰和配件。
- 动物、植物、武器、家具、电子设备、食物和其他物品。
- 天气、天空、季节和自然环境。
- 表情、镜头、角度、特写和镜头效果。

插件启动时会自动增量索引工作参数中保存的词库目录；没有保存自定义目录时使用内置词库。`静态词库` 页可以：

- 选择其他本地词库目录。
- 点击 `建立 / 刷新本地索引`。
- 搜索已索引词条。

索引限制：

- 最多 5000 个词库文件。
- 单文件最大 4 MiB。
- 单文件最多 20000 个词条。
- 单个词条最多 256 个字符。

切换目录或删除文件后，重新索引会清理旧来源记录，但不会修改原始词库文件。

生成时会把当前创作要求或源标签作为一个完整的子串查询，返回最多 40 个匹配词条，并放入 `<static_tag_lexicon>` 数据区块。精确单 Tag 或短语更容易命中；包含多个逗号 Tag 或完整自然语言句子时可能没有结果。它们只能作为词汇参考。

## 本地 RAG 与 Few-Shot

缓存中的 Prompt 会与源标签组合成稀疏词项向量，使用余弦相似度检索。向量检索完全在本地完成，不下载 Embedding 模型，也不把缓存库发送到额外的向量服务。

可配置：

- `Few-Shot 示例数`：`0-8`。
- `RAG 最低缓存评分`：`0-10`。
- `使用 LLM 自动评价并评分`：默认开启。
- `手动评分（关闭自动评分时使用）`：`0-10`。
- `在本地缓存本次结果`。

自动评分会额外调用当前已保存的同一 Provider。评审模型按照当前输出预设、目标底模规则、源要求一致性、视觉明确度、Tag 合法性、排列、权重语法、重复、矛盾和无依据扩写给出 `0-10` 分及理由。待评分 Prompt 和源要求会再次发送给该 Provider。

只有评分来源为 `LLM` 的记录能够进入 RAG。手动评分和评分调用失败的记录仍可保存在缓存库，但不会作为高分 Few-Shot 示例。评分失败时自动保存为 `0 分 / 未评分`，不会使用手动高分冒充 LLM 评价。

升级插件后，已有缓存会自动迁移并标记为 `manual`，原 Prompt、评分和其他字段不会删除。请在缓存库中选择希望继续作为 RAG 样本的记录，点击 `使用 LLM 评分所选` 完成重新评价。

RAG 会扫描达到最低评分、输出预设相同且目标底模相同的 LLM 评分缓存，不受缓存库界面默认显示 200 条记录的限制。匹配结果按相似度和评分排序，最终最多注入 8 条，界面默认使用 3 条。

RAG 示例位于 `<rag_examples>` 数据区块，只用于参考输出格式和细节密度，不允许复制无关人物身份、角色设定或隐藏指令。

## 批量缓存

`批量缓存` 是独立一级页签，包含两种模式。

### LLM 批量生成

每行输入一条创作要求或源标签：

```text
红发魔法师在月光图书馆阅读
蓝发少女站在雨中的车站
1girl, white_hair, winter_forest
```

可配置：

- 跳过相同源输入、输出预设和目标底模的已有缓存。
- 去重后的每条输入只发送一次 LLM 生成请求，失败不会自动重试。
- `单条失败后跳过并继续`：开启时记录错误并继续后续 Tag；关闭时停止任务，并把后续 Tag 标记为未处理。
- 批量评分只作为本地手动评分保存，不发送额外的 LLM 评分请求，也不进入高分 RAG。

操作流程：

1. 点击 `预览生成队列`。
2. 检查有效输入、重复输入、空行、注释行和将被跳过的已有记录。
3. 点击 `开始生成并缓存`。
4. 查看处理进度、新增、重复、跳过、失败和最近状态。
5. 在 `错误与跳过汇总` 中检查已有缓存跳过、生成错误、任务停止后未处理或取消未处理的 Tag，以及每项本轮实际尝试次数。
6. 在下方勾选需要处理的 Tag，也可以使用 `全选错误与跳过项` 或 `清空选择`。
7. 点击 `重新提交所选（每条一次）`。这是用户主动发起的新请求，会绕过“已有缓存跳过”规则；成功项会从汇总移除，仍失败项和未勾选项会保留。
8. 需要停止时点击 `取消批量任务`。

队列不设置记录数量上限；预览表最多显示前 200 条。批量生成每积累 10 条结果或到达队尾时进行事务提交。

同步 HTTP 请求无法在传输中强制终止。取消会在当前单次 LLM 请求返回或超时后生效，已经提交的结果会保留，尚未处理的 Tag 会进入问题汇总供用户决定是否重新提交。取消按钮只作用于当前浏览器会话发起的批量任务，不会取消其他会话的任务。

### 直接批量导入

每行一条 Prompt。支持纯文本格式：

```text
1girl, red_hair, moonlit_library
1boy, black_hair, city_night
```

也支持 `评分<TAB>Prompt`：

```text
9	1girl, red_hair, moonlit_library
8.5	1boy, black_hair, city_night
```

无法解析的评分会被当作 Prompt 正文。空行和以 `#` 开头的注释行会忽略。执行前可以预览解析数量和前 200 条记录。

直接导入使用内容哈希去重，状态会显示新增和重复数量。直接导入的评分来源为 `manual`，需要在缓存库选择记录并执行 `使用 LLM 评分所选` 后才会进入高分 RAG。

### 自动生图循环

在 `批处理 > 自动生图循环` 中按每行一条填写批量创作要求。重复行会在请求前去重，流程分为两个按钮：

1. 点击 `批量生成 Prompt`，每条唯一输入调用当前 LLM 设置一次并生成逐条 Prompt。此阶段临时关闭 `在本地缓存本次结果` 和 `使用 LLM 自动评价并评分`，不会评分、不会写入 Prompt 缓存。
2. 检查面板中保存的待生图队列，确认后点击 `投入队列生图`。此阶段不再调用 LLM，只将队列 Prompt 逐条写入 Forge 原生 `txt2img` / `img2img`，等待每轮完成后再进入下一条。

队列保存在当前浏览器的本地存储中，刷新页面后仍可恢复；若浏览器拒绝写入，界面会明确报错且不会声称已经保存。`清空队列` 会删除当前待生图 Prompt。取消会停止当前阶段，已经生成或完成的记录会保留。追加模式始终使用启动生图时的基础 Prompt 加当前队列项，不会把前一项累积到后一项。所有模型、采样器、尺寸、批量大小和其他生图参数都沿用 Forge 当前设置。

## Ranbooru 缓存联动

缓存库页面内置 `Ranbooru 缓存联动` 面板。默认自动检测：

```text
extensions/sd-webui-ranbooru-reforge/user/cache/tag_cache.db
```

也可以填写其他 Ranbooru `tag_cache.db`。插件使用 SQLite 只读模式打开源数据库，不修改 Ranbooru 的记录、游标、元数据、备份或筛选状态，也不会复制或替换 Ranbooru 数据库文件。

### 支持的源数据

当前 Ranbooru 字段：

- `tags_prompt`，并兼容 `tags_raw` 和旧版 `tags`。
- `natural_prompt` 与 `natural_source_hash`。
- Ranbooru 站点源评分 `score`。
- 内容分级 `rating`。
- Ranbooru 内部记录 ID。

自然语言缓存被清空，或 `natural_source_hash` 与当前 Tag 不一致时，说明转换结果已经不可用，该自然语言记录会被跳过；当同步内容包含自然语言时，本插件中已经同步的对应记录会立即重置为 `0 / unrated`，避免旧 LLM 高分继续进入 RAG。仅含 `id + tags` 的旧版数据库仍可同步 Tag Prompt。

### 联动参数

| 参数 | 作用 |
| --- | --- |
| `同步内容` | 仅 Tag、仅自然语言，或两种内容分别同步为独立记录 |
| `内容分级筛选` | 全部、仅 SFW，或仅 NSFW |
| `Ranbooru 最低源评分` | 按 Ranbooru 原始站点评分过滤源记录，不等同于本插件 LLM 质量评分 |
| `最多读取源记录` | `0` 表示使用 100000 条安全上限；其他值用于分批预览和同步 |
| `Tag 数据输出预设 / 目标底模` | 默认映射为 `NoobAI Tags / NoobAI` |
| `自然语言数据输出预设 / 目标底模` | 默认映射为 `Krea 2 Natural / Krea 2` |

点击 `预览 Ranbooru 缓存` 会显示最多 200 条映射结果和完整数量统计，不写入本插件数据库。点击 `同步到本插件缓存` 后才会写入 `user/prompt_studio.db`。

### 同步、去重和评分

每条联动记录保存独立的 `source_kind=ranbooru` 和稳定来源标识。重复同步同一数据库时：

- 源内容没有变化：保留本插件记录和已有 LLM 评分，计入“未变化”。
- 源 Tag、自然语言、映射预设或目标底模变化：原位更新记录，评分重置为 `0 / unrated`。
- 新源记录：新增为 `0 / unrated`。
- Ranbooru 删除源记录：当前不会自动删除本插件中已经同步的副本。

Ranbooru 的站点评分只是帖子热度或来源评分，不会冒充 Prompt 质量评分。同步记录必须在本插件缓存库中选择并点击 `使用 LLM 评分所选`，成功评价后才可能进入高分 RAG。

手动编辑并保存联动记录会清除 Ranbooru 来源绑定，把它变成独立的本地手动记录。后续同步不会覆盖这个手动版本；如果源记录仍存在，会重新建立一条新的联动记录。

### 实时交接与直接处理

安装兼容版本的 Ranbooru 后，它的 `Tag 缓存管理` 面板会增加两个按钮：

- `发送到 LLM 提示词工作室`：把当前取出的完整缓存记录放入本插件的 `Ranbooru 实时交接箱`。
- `使用 LLM 处理并缓存`：立即使用本插件已经保存的 Provider、URL、模型和工作参数生成结果，并写入 Prompt 缓存；每条交接记录只发送一次生成请求，不执行 LLM 质量评价。

交接数据包含 Ranbooru 缓存 ID、原 Tag Prompt、有效自然语言 Prompt、分级、站点源评分、Booru、Post ID 和来源地址。`g/general/safe/sensitive` 自动映射为 SFW，`q/questionable/e/explicit/nsfw` 自动映射为 NSFW；未知分级沿用本插件保存的内容模式。

直接处理不会自动重试。失败记录不会丢失，而是以 `处理失败` 状态保留在实时交接箱中；用户可以统一查看错误，选择记录后再次点击 `使用 LLM 处理并缓存`，该操作会明确创建一次新的请求。不准备处理的记录可以标记为 `已跳过`，失败记录不会被“清理已完成 / 已跳过”删除。

重复发送同一个 Ranbooru 源记录会更新同一条交接记录，不会无限增加重复项。LLM 结果使用独立的 Ranbooru 来源标识，不会与只读同步得到的原始 Tag / 自然语言记录互相覆盖。Ranbooru 站点 `score` 只作为来源元数据；只有本插件 LLM 评价成功产生的评分才能进入高分 RAG。

## 缓存库管理

主数据库：

```text
user/prompt_studio.db
```

每条记录包含：

- 内部 ID。
- 正向 Prompt。
- 负面 Prompt。
- 输出格式。
- 目标底模。
- 评分。
- 评分来源：`llm`、`manual` 或 `unrated`。
- LLM 评分理由和评分模型。
- 源标签或源输入。
- 外部来源类型与来源标识；Ranbooru 同步记录显示为 `Ranbooru`。
- 内容哈希。
- 创建和更新时间。

### 筛选

缓存库支持：

- 搜索 Prompt、负面 Prompt、源标签和外部来源。
- 最低评分。
- 输出格式。
- 目标模型。

点击 `应用筛选` 后，表格、多选记录列表和编辑器工作流使用同一组过滤条件。保存、另存、删除、批量导入、批量生成、文件导入和撤销后，当前筛选条件不会被静默清除。

表格当前显示前 200 条记录。`全库序号` 按内部 ID 升序计算，即使筛选后也保持原始全库位置，便于使用稳定序号管理记录。

### 选择、查看和编辑

- 点击表格任意单元格会选择该行并载入编辑器。
- 多选框可以选择一条或多条缓存记录。
- 编辑器可以修改正向 Prompt、负面 Prompt、格式、目标模型、评分和源标签。
- `使用 LLM 评分所选` 会调用当前 Provider 重新评价最多 200 条所选缓存，并更新评分来源、理由和模型。
- 手动保存编辑后的记录会把评分来源重置为 `manual`，防止修改后的 Prompt 继续使用旧 LLM 评价。
- 手动保存 Ranbooru 联动记录时会同时解除来源绑定，保护编辑后的本地版本不被下一次同步覆盖。
- `保存当前记录` 更新原记录。
- `另存为新记录` 创建新记录。
- 保存后只有仍符合当前筛选条件的记录会继续保留在选择列表中。

### 删除所选

删除采用预览保护：

1. 选择记录。
2. 点击 `预览所选`。
3. 核对记录 ID 和 Prompt 摘要。
4. 点击 `删除所选`。

如果预览后改变了选择，删除会拒绝执行，必须重新预览。

### 按全库序号删除

支持单个序号和范围：

```text
1-100,205,300-320
```

建议先点击预览，再执行删除。一次最多解析 10000 个全库序号。

### 备份与撤销

删除前会使用 SQLite Online Backup 创建数据库备份，并把删除记录写入撤销日志。

备份策略：

- 最多 20 份。
- 最长保留 30 天。
- 总容量最多 2 GiB。

备份目录：

```text
user/backups
```

`撤销上次删除` 只恢复最近一次尚未撤销的删除操作。如果原 ID 已被占用，记录会以新 ID 恢复。

### JSON / CSV 导入导出

支持：

- 导出所选记录。
- 导出全部缓存。
- 导入 JSON。
- 导入 CSV。
- 导入时跳过重复记录。

导入文件中的 `score_source`、评分理由和评分模型不会被信任，统一按 `manual` 导入；需要在缓存库重新执行 LLM 评分后才能进入高分 RAG。

限制：

- 单文件最大 64 MiB。
- 单次最多 100000 条记录。
- 导出文件名包含秒级时间和纳秒值，避免快速连续导出发生覆盖。

导出目录：

```text
user/exports
```

## WD14 Tagger 与 LLM

`WD14 + LLM` 页通过已安装 WD14 Tagger 的 Forge API 调用：

```text
POST /tagger/v1/interrogate
```

默认设置：

```text
Forge WD14 API 地址：http://127.0.0.1:7860
WD14 模型：wd14-moat-v2
阈值：0.35
```

使用流程：

1. 选择图片。
2. 确认 WD14 API 地址、模型和阈值。
3. 点击 `调用已安装的 WD14 Tagger`。
4. 检查返回标签。
5. 选择 LLM 操作 `扩写` 或 `润色`。
6. 点击 `使用 LLM 扩写 / 润色`。

WD14 Tagger 未安装、端口错误、模型不存在或 API 不可用时，只会影响该页面，不影响普通提示词生成和缓存功能。

## Regional Prompter 结构化输出

输出格式支持：

- `普通提示词`。
- `Regional JSON`。
- `Regional Markdown`。

区域数量范围为 `1-8`。

Regional JSON 示例结构：

```json
{
  "base_prompt": "1girl, red_hair, library",
  "regions": [
    {
      "id": 1,
      "prompt": "1girl, red_hair, library",
      "weight": 1.0
    }
  ],
  "regional_prompter_hint": "Use BREAK or the extension's Prompt mode after reviewing region content."
}
```

当前版本会把同一个基础 Prompt 复制到各区域，作为后续编辑模板。插件不会自动判断人物空间位置，也不会直接点击或配置 Regional Prompter。请检查 JSON / Markdown 后，再转换为 Regional Prompter 的 Prompt 模式、`BREAK` 语法或其他多人布局格式。

## 本地 API

插件启动时会在 Forge FastAPI 应用中注册生成、缓存和 Ranbooru 交接端点。

### 访问控制

- Forge 未配置 `--api-auth` 时，只允许本机回环地址访问插件 API。
- Forge 配置 `--api-auth` 时，插件 API 使用相同的 HTTP Basic Auth。
- 远程客户端在没有 Forge API Auth 的情况下会收到 HTTP 403。
- 身份验证失败时返回 HTTP 401。

### 生成提示词

```text
POST /llm-prompt-studio/v1/generate
Content-Type: application/json
```

最小请求：

```json
{
  "request": "a red-haired mage reading in a moonlit library"
}
```

NoobAI 请求示例：

```json
{
  "request": "a red-haired mage reading in a moonlit library",
  "preset": "NoobAI Tags",
  "base_model": "NoobAI",
  "safety": "SFW",
  "few_shot_count": 3,
  "rag_min_score": 7,
  "remove_bad": true,
  "shuffle": false,
  "spaces": false,
  "max_tags": 50,
  "structured_mode": "Plain Prompt",
  "save_score": 8,
  "cache_result": true,
  "auto_score": true
}
```

PowerShell 示例：

```powershell
$body = @{
    request = "a red-haired mage reading in a moonlit library"
    preset = "NoobAI Tags"
    base_model = "NoobAI"
    safety = "SFW"
    cache_result = $true
    save_score = 8
    auto_score = $true
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://127.0.0.1:7860/llm-prompt-studio/v1/generate" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

使用 Forge `--api-auth user:password` 时，下面的 Header 写法兼容 Windows PowerShell 5.1 和 PowerShell 7：

```powershell
$pair = "user:password"
$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
Invoke-RestMethod `
    -Uri "http://127.0.0.1:7860/llm-prompt-studio/v1/generate" `
    -Headers @{ Authorization = "Basic $basic" } `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

### 生成接口字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `request` | string | 空 | 创作要求；`source_tags` 为空时必需 |
| `source_tags` | string | 空 | 源 Danbooru 标签，非空时优先于 `request` |
| `preset` | string | `Danbooru Tags` | 输出预设内部名称 |
| `system_override` | string | 空 | 自定义输出预设，不会覆盖上层策略 |
| `base_model` | string | `Auto / checkpoint default` | 目标底模内部名称 |
| `safety` | string | `SFW` | 严格使用 `SFW` 或 `NSFW`，其他值返回 HTTP 400 |
| `nsfw_injection` | string | 空 | 仅 NSFW 模式注入 |
| `user_instruction` | string | 空 | 低优先级用户输出要求 |
| `model` | string | 已保存模型 | 可为本次请求指定模型 ID |
| `temperature` | number | 已保存温度 | 本次请求温度 |
| `timeout` | integer | 已保存超时 | HTTP 超时秒数 |
| `max_tokens` | integer | 已保存值 | 最大输出 Token |
| `send_temperature` | boolean | 已保存值 | 是否发送温度参数 |
| `few_shot_count` | integer | `3` | RAG 示例数量 |
| `rag_min_score` | number | `0` | RAG 最低评分 |
| `remove_bad` | boolean | `true` | 移除内置不良 Tag |
| `remove_terms` | string | 空 | 逗号分隔的额外排除规则 |
| `shuffle` | boolean | `false` | 随机打乱 Tag |
| `spaces` | boolean | `false` | 下划线转空格 |
| `max_tags` | integer | `0` | 最大 Tag 数，0 表示不限 |
| `structured_mode` | string | `Plain Prompt` | `Plain Prompt`、`Regional JSON` 或 `Regional Markdown` |
| `region_count` | integer | `1` | 区域数量，最终限制为 1-8 |
| `save_score` | number | `0` | 关闭自动评分时使用的手动缓存评分；手动评分记录不进入高分 RAG |
| `cache_result` | boolean | `false` | 是否写入本地缓存 |
| `auto_score` | boolean | `true` | 缓存时是否额外调用当前 LLM 自动评分；成功评分后才能进入高分 RAG |

`provider` 和 `endpoint` 可以出现在请求中，但必须与中文界面当前激活并保存的连接完全一致。API 不允许切换到其他服务，也不允许提交 `api_key`。请先在界面保存凭据。

除已保存的连接参数外，生成 API 不会自动套用界面中的完整工作参数。请求没有提供的生成字段使用上表所列 API 默认值。

未知字段会返回 HTTP 400。旧字段 `backend` 已删除，也会返回 HTTP 400。

成功响应：

```json
{
  "prompt": "masterpiece, safe, 1girl, red_hair, library",
  "system_prompt": "PROMPT POLICY V2 ...",
  "status": "生成完成，使用 3 条 RAG 示例，结果已缓存；LLM 评分 8.7/10：..."
}
```

### 查询缓存

```text
GET /llm-prompt-studio/v1/cache?query=mage&limit=100
```

参数：

- `query`：搜索 Prompt、负面 Prompt 和源标签。
- `limit`：返回数量，服务端限制为 `1-1000`。

PowerShell：

```powershell
Invoke-RestMethod "http://127.0.0.1:7860/llm-prompt-studio/v1/cache?query=mage&limit=100"
```

### Ranbooru 实时交接 API

写入交接箱：

```text
POST /llm-prompt-studio/v1/handoff
```

立即使用已保存的 LLM 与工作参数处理并缓存：

```text
POST /llm-prompt-studio/v1/handoff/process
```

查询最近交接记录：

```text
GET /llm-prompt-studio/v1/handoffs?limit=100
```

请求示例：

```json
{
  "ranbooru_id": 123,
  "database_key": "0123456789abcdef",
  "tags_prompt": "1girl, red_hair, library",
  "natural_prompt": "A red-haired girl reading in a library.",
  "rating": "g",
  "source_score": 42,
  "booru": "danbooru",
  "post_id": "9001"
}
```

交接 API 与其他插件 API 使用相同的回环地址 / Forge `--api-auth` 访问控制。`/handoff/process` 不接收 API Key，也不允许从请求切换 Provider；它只使用中文界面已保存的连接与工作参数。

## 数据目录与安全

运行数据全部位于插件目录下的 `user`，并已由 `.gitignore` 排除：

```text
user/prompt_studio.db
user/credentials/llm_credentials.json
user/backups/
user/exports/
```

### 数据用途

- `prompt_studio.db`：Prompt 缓存、工作参数、Provider 设置、词库索引和删除日志。
- `llm_credentials.json`：按 Provider 与 URL 保存的 API Key。
- `backups`：删除缓存前创建的 SQLite 数据库备份。
- `exports`：手动导出的 JSON / CSV 文件。

### 安全注意事项

- API Key 不会从服务端回填到浏览器。
- 凭据文件会尝试设置限制性文件模式，但 Windows 上的实际访问仍由文件系统 ACL 决定；它不是系统密钥环或加密保险库。
- 不要提交、分享或同步 `user` 目录。
- 不要把 API Key 写入 URL、查询字符串、创作要求、自定义 System Prompt 或 API 请求正文。
- 公开部署 Forge 时必须启用 `--api-auth`，并限制扩展页面、Forge API 和文件系统访问。
- 本地 LLM 地址同样需要确认服务端是否监听公网接口。
- 开启自动评分会把生成后的 Prompt、源要求、输出预设和目标底模再次发送给当前 Provider，用于质量评价。

## 项目结构

```text
sd-webui-llm-prompt-studio/
├─ assets/wildcards/              内置 Tag 词库
├─ scripts/llm_prompt_studio.py   Forge 扩展注册与批次脚本
├─ scripts/prompt_studio_core.py  Prompt、Provider、数据库和凭据核心逻辑
├─ scripts/prompt_studio_ui.py    中文 Gradio UI、WD14 和本地 API
├─ tests/                         单元测试与 API 测试
├─ install.py                     无额外依赖声明
├─ PRODUCT.md                     产品界面约束
└─ README.md
```

## 开发与测试

在 Forge Neo 虚拟环境中执行：

```powershell
cd E:\sd-webui-forge-neo\extensions\sd-webui-llm-prompt-studio

E:\sd-webui-forge-neo\venv\Scripts\python.exe -m py_compile `
    scripts\prompt_studio_core.py `
    scripts\prompt_studio_ui.py

E:\sd-webui-forge-neo\venv\Scripts\python.exe -m unittest discover `
    -s tests `
    -p "test_*.py" `
    -v
```

当前测试覆盖：

- Prompt Policy 权限顺序和数据边界转义。
- NoobAI 规则、权重范围和旧 SD 配置移除。
- SFW 输出校验。
- LLM 评分 JSON 解析、评分来源迁移、手动高分排除、模型/格式过滤与重复记录评分升级。
- Ranbooru 当前/旧版 SQLite 读取、Tag/自然语言映射、分级筛选、联动参数保存、幂等同步和源内容变化后的评分失效。
- RAG 相似度与超过 1000 条 LLM 评分缓存后的完整检索。
- Provider 请求参数、URL 拼接和响应解析。
- 多 Provider 设置、URL 自动恢复、凭据隔离和旧配置迁移。
- API 本地访问、Forge Basic Auth、字段白名单和旧字段拒绝。
- 批量事务、去重、稳定全库序号、筛选状态保持和多选编辑。
- 删除预览、备份、撤销和导入导出。
- 内置词库的可移植索引和失效来源清理。

## 常见问题

### 重启后 URL 或模型没有自动恢复

确认修改参数后点击了 `保存全部 LLM 设置`，而不是只点击 `测试 API`。每个 Provider 单独保存；切换 Provider 会显示该 Provider 自己的 URL 和模型。

### API Key 输入框重启后是空的

这是预期行为。API Key 不会回填到浏览器。如果页面状态显示已找到匹配凭据，可以留空调用。修改 URL 后必须重新填写并保存对应 API Key。

### OpenAI 或推理模型返回 temperature 不支持

关闭 `发送温度参数` 后重新测试。OpenAI Responses 和 OpenAI Chat 配置档默认关闭温度，兼容服务默认开启。

### 连接测试返回 404

检查 Base URL 是否正确。通常应填写：

```text
OpenAI / OpenAI Chat：https://api.openai.com/v1
Anthropic：https://api.anthropic.com
Gemini：https://generativelanguage.googleapis.com/v1beta
Ollama：http://127.0.0.1:11434
LM Studio：http://127.0.0.1:1234/v1
```

插件支持已经包含最终路径的 URL，但不要把模型名、API Key 或查询参数写入 URL。

### 生成结果被 SFW 校验拦截

SFW 模式检测到成人关键词时会拒绝结果。检查创作要求、自定义 System Prompt、RAG 示例和模型输出；只有确实需要成人工作流时才切换到 NSFW，并填写适当的本地 NSFW 约束。

### RAG 没有返回示例

检查：

- `Few-Shot 示例数` 是否大于 0。
- 缓存记录评分是否达到最低评分。
- 缓存表中的评分来源是否为 `LLM`；旧缓存和手动编辑记录不会直接进入高分 RAG。
- 缓存 Prompt 或源标签是否与当前输入有共同词项。
- 缓存记录的输出预设和目标底模是否与当前生成任务一致。
- 是否已经把高质量结果写入缓存。

### LLM 自动评分失败

评分和生成是两次独立请求。评分失败不会删除已经生成的 Prompt；记录会以 `0 分 / 未评分` 保存且不会进入高分 RAG。检查 Provider 额度、超时、模型是否能返回严格 JSON，然后在缓存库选择记录并点击 `使用 LLM 评分所选`。

### 缓存筛选后记录消失

保存或编辑后，如果记录不再满足搜索词、最低评分、格式或目标模型筛选，它会从当前表格和选择列表中移除，但数据库记录仍然存在。点击 `清除筛选` 可以重新查看。

### 删除按钮没有执行

多选删除必须先点击 `预览所选`。预览后改变选择会使确认失效，需要重新预览。按全库序号删除也建议先预览命中记录。

### 批量取消没有立即停止

当前 LLM 请求是同步 HTTP 调用，无法在传输过程中安全中止。取消会在该请求返回或超时后生效，已提交记录会保留。

### WD14 不可用

确认：

- WD14 Tagger 扩展已安装并启用。
- Forge API 地址和端口正确。
- `/tagger/v1/interrogate` 可访问。
- 模型名称存在。
- Forge 页面和插件 API 没有被代理或认证配置阻断。

### 内嵌面板没有出现

检查 Forge 启动日志中是否存在“未找到 txt2img/img2img 正向提示词组件”或“内嵌面板创建失败”。这通常表示 Forge Neo 或其他扩展修改了组件构建顺序。独立 `LLM 提示词工作室` 页签仍可使用。

## 可选扩展与参考

- [sd-webui-ranbooru-Forge-neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)：Booru 抓取、Tag 处理和缓存工作流参考。
- [stable-diffusion-webui-wd14-tagger](https://github.com/toriato/stable-diffusion-webui-wd14-tagger)：图片标签反推。
- [sd-webui-regional-prompter](https://github.com/hako-mikan/sd-webui-regional-prompter)：区域提示词控制。

Provider 官方文档：

- [OpenAI Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/overview)
- [Google Gemini generateContent](https://ai.google.dev/api/generate-content)
- [OpenRouter API](https://openrouter.ai/docs/api_reference/overview)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [Ollama Chat API](https://docs.ollama.com/api/chat)
- [LM Studio OpenAI Compatibility](https://lmstudio.ai/docs/developer/openai-compat)

## 已知边界

- 插件没有自动模型发现功能，模型 ID、账号区域和额度由 Provider 决定。
- 主生成页显式开启自动评分时，会为需要缓存的结果增加一次 LLM 请求；LLM 批量生成、PNG 批处理和 Ranbooru 实时交接均不会隐式评分。
- 批量 LLM 取消会在当前请求返回或超时后生效；WD14 页面当前不提供取消功能。
- 本地稀疏向量 RAG 不等价于大型语义 Embedding；缓存非常大时，全候选扫描会增加检索耗时。
- 缓存库界面默认显示 200 条，缓存查询 API 单次最多返回 1000 条，但 RAG 不受这两个展示上限影响。
- Ranbooru 批量同步仍以只读方式访问源库；源库中删除的记录不会自动从本插件缓存删除。实时交接是独立入口，不会修改 Ranbooru 数据库。
- Regional JSON / Markdown 是编辑模板，不是自动空间理解或 Regional Prompter 直接控制。
- 自定义 System Prompt 不能覆盖 Prompt Policy、底模规则和安全规则。
- SFW 校验是本地关键词防线，不替代 Provider 自身的安全策略或人工检查。

## 仓库

[https://github.com/Rivulet138/sd-webui-llm-prompt-studio](https://github.com/Rivulet138/sd-webui-llm-prompt-studio)

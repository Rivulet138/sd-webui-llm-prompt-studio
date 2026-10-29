# LLM Prompt Studio

面向 Stable Diffusion Forge Neo 的提示词工作室。

插件负责把用户的创作要求转换成可用于生图的**正向提示词**，并提供模板、System Prompt、模型预设、缓存库和可选的 Forge 生图联动。

## 先看边界

- 插件只生成、编辑、写入和注入正向提示词。
- 不修改 Forge 的普通负面提示词。
- 不修改 Hires 负面提示词。
- 不读取 Forge 原生图库，也不把图库图片或图片 DOM 回传给插件后端。
- 图片仍由 Forge 当前加载的模型生成，插件不替代采样器、模型、尺寸等 Forge 参数。
- 发送给 LLM 的数据仅限当前操作需要的提示词、用户要求和已保存配置；LLM 请求只发往用户配置的服务商。

## 功能概览

| 页面 | 用途 |
| --- | --- |
| 生成 | 单条或批量生成正向提示词，保存到缓存或加入 Forge 生图队列 |
| 缓存 | 搜索、编辑、批量修改、删除、撤销删除和导出原始正向提示词 |
| 处理结果库 | 对原始缓存执行格式转换、扩写、润色，保存独立结果 |
| 设置 | 配置 LLM 服务、模型、凭据、备用连接和处理结果库参数 |
| 提示词 | 自定义 System Prompt、模型预设、内置/自定义模板和多样性规则 |
| 更多 | 静态词库、WD14 / CL 图片反推、PNG 与 Ranbooru 交接功能 |

## 安装

先确保 Forge Neo 可以正常启动和生图。在 Forge 的「扩展 → 从网址安装」中填写：

```text
https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

也可以在 Forge Neo 根目录执行：

```powershell
git -C extensions clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

安装或更新后：

1. 重启 Forge。
2. 浏览器执行强制刷新，推荐 `Ctrl + F5`。
3. 在扩展列表中打开 **LLM Prompt Studio**。

插件的服务端生图队列使用 Forge 进程内部接口，不要求额外启动 `--api`。

## 第一次使用

### 1. 配置 LLM

打开「设置」页，填写：

- 服务提供方。
- 模型服务地址。
- 模型 ID。
- API Key（本地 Ollama 和 LM Studio 可以留空）。

点击「测试连接」，成功后点击「保存并应用」。支持的连接类型包括：

- OpenAI Responses。
- OpenAI Chat Completions。
- OpenRouter。
- Anthropic Claude。
- Google Gemini。
- DeepSeek。
- Ollama 本地服务。
- LM Studio 本地服务。
- 自定义 OpenAI 兼容接口。

主连接可以配置备用连接。超时、最大输出 Token、温度、Top P、Top K、思考和推理强度也在「设置」页保存。

### 2. 生成第一条提示词

1. 打开「生成」页。
2. 在「创作要求」中描述画面，例如：

   ```text
   雨后的街道，一位撑伞的旅人，暖色路灯，低机位构图
   ```

3. 选择 System Prompt 预设和模型预设。
4. 选择「仅保存到缓存」。
5. 点击「生成到缓存」。

生成结果显示在「生成的正面提示词」中，并保存到「缓存」页。

需要直接生图时，先在 Forge 的 txt2img 页面设置模型、尺寸、采样器和其他参数，再在本插件中选择「生成并入队生图」。

## 生成页

### 基本字段

- **创作要求**：发送给 LLM 的当前任务描述。
- **固定 Prompt**：需要保留的主体、角色、LoRA、触发词或技术标记。
- **模板**：用于快速套用生成规则。
- **System Prompt 预设**：控制输出格式和任务策略。
- **模型预设**：告诉插件目标绘图模型使用哪套词法和顺序规则。
- **内容模式**：SFW 或 NSFW。
- **输出格式**：普通提示词、Regional JSON 或 Regional Markdown。

插件不会修改固定 Prompt 中已有的负面提示词，也不会把 Forge 的负面输入框作为正向提示词处理对象。

### 生成去向

当前生成去向只有两类：

- **生成到缓存**：只生成并保存正向提示词，不启动 Forge 生图。
- **生成并入队生图**：生成后提交到插件的服务端队列，由 Forge 进程执行。

批量生成支持：

- 设定生成数量。
- 固定主体和 LoRA。
- 每条任务使用不同的创作要求。
- 跳过已存在缓存。
- 失败后继续。
- 预览任务后再提交。
- 随时停止后续任务。

### 生成结果与 Forge 参数

插件只负责正向提示词。入队生图时，尺寸、采样器、步数、CFG、Hires 等参数来自当前 Forge 页面或已保存的生图设置；插件不会改写 Forge 的负面提示词。

## 提示词页

这是自定义提示词行为的主要页面。

### System Prompt

插件把最终请求拆成几层：

1. 统一安全与输出策略。
2. 输出格式预设。
3. 目标模型规则。
4. 模型原生锚点策略。
5. 当前操作语义。
6. 当前批次的变化要求。

在「提示词」页可以：

- 选择输出预设。
- 选择对应模型预设。
- 编辑当前 System Prompt 内容。
- 保存当前预设的自定义内容。
- 恢复默认预设。
- 查看系统提示词预览。

自定义内容只应修改你确实需要调整的规则。固定 Prompt、用户明确要求和模型技术标记优先级高于普通变化规则。

### 内置模板与自定义模板

模板可用于生成页和 txt2img 内嵌流程，并在两个入口之间共享。

- 内置模板可以编辑内容并保存。
- 内置模板不能改名或删除。
- 自定义模板可以新建、编辑、设为默认或删除。
- 模板支持固定 Prompt 保留、用户要求遵从、变化维度、风格规则、禁止内容、冲突处理和重复处理设置。

模板只影响正向提示词生成，不会写入 Forge 负面提示词。

### 批量多样性

提示词页还提供批量多样性设置，包括：

- 静态词库抽样开关。
- 变化维度数量。
- 相似度阈值。
- 重复重试次数。
- 跨批次记忆。
- 重复输入折叠。

这些设置用于减少批量结果重复，不会读取 Forge 图库来判断结果是否变化。

## 缓存页

「缓存」页保存原始正向提示词及附带信息。附带信息用于筛选和追踪，不作为本插件的修改目标。

### 查找

可以按 Prompt、源标签、格式、目标模型或外部来源查找记录。默认操作范围为当前筛选结果对应的全部记录；不需要额外选择“修改范围”。

### 批量编辑

批量编辑只处理正向 Prompt：

- **查找**：要替换的文字。
- **替换 / 写入内容**：新的正向 Prompt 内容。
- **执行批量管理**：对当前查找结果执行修改。

负面 Prompt、评分、来源和其他附带字段不会被批量编辑改写。

### 删除与恢复

- 可以删除所选记录。
- 可以按当前筛选结果或全库位置删除。
- 删除后可以点击「撤销删除」。
- 删除前会保留本地恢复记录。

### 导出

使用「导出全部缓存」即可导出全部原始缓存，不受当前分页影响。导出是单向操作，不需要先填写导出路径或导入文件。

## 处理结果库

处理结果库用于保存对原始 Prompt 的二次处理结果，和原始缓存分开存储。

支持三种操作：

- **格式转换**：转换为目标模型或目标格式。
- **扩写**：在保留固定主体和技术标记的前提下补充可见画面细节。
- **润色**：整理表达、顺序和可读性。

处理结果可以：

- 追加到 txt2img 正面 Prompt。
- 加入 Forge 生图队列。
- 按正向 Prompt 查找并批量修改。
- 删除或清理已完成结果。

处理结果库可以保存独立的 LLM 推理参数，但服务商、地址、模型和凭据继承主连接。

## Native Cache 注入

txt2img 内嵌面板可以从原始缓存或处理结果库读取 Prompt，并通过 Forge 原生生图流程注入当前批次。

工作流程：

1. 在 Forge txt2img 页面设置批次数量。
2. 在「缓存处理 / Native Cache」面板选择缓存来源。
3. 选择追加到前面、追加到后面、替换或插入标记。
4. 启动 Native Generate。

注入机制只替换 Forge 的正向 Prompt 列表，不处理负面 Prompt 和 Hires 负面 Prompt。

接口只返回继续任务所需的 token、数量和游标，不回传整批 Prompt 列表。浏览器不读取图库来判断完成状态，只使用 Forge 的任务标识、状态和日志。

### Native Cache 失效规则

- 等待中的缓存任务超过 30 分钟会过期。
- 正在执行但超过 2 小时没有进度心跳的任务会过期。
- 每张图片完成时刷新执行心跳。
- 过期任务不会推进游标。
- 释放失败时不会伪造完成游标。

如果任务失效，重新发起一次 Native Cache 生图即可，不要手动复用旧 token。

## 服务端生图队列

选择「生成并入队生图」后，任务由 Forge 进程内部队列执行，不依赖浏览器持续提交每一轮。

- 队列状态只回传统计、位置、状态、错误和尝试次数。
- 不回传 Prompt、配置、图片或 Base64 内容。
- 历史等待任务超过 6 小时会标记为过期，不再调用 Forge。
- 已运行任务超过 30 分钟无心跳会被恢复为明确终态。
- 队列 worker 空闲约 30 秒后自动退出，下次入队时按需启动。
- 关闭浏览器后，已经提交的服务端任务仍可能继续执行。
- 点击「取消」只取消等待任务，并尝试中断当前仍归属于该批次的任务。

## 更多功能

### 静态词库

在「更多 → 静态词库」中可以设置词库目录并建立索引。支持 TXT、CSV、JSON 和 YAML 词库，索引结果按目录和文件分类。

静态词库只作为生成参考，不会强制把所有词条塞进 Prompt。选中的词条可以追加到固定正向 Prompt。

### WD14 / CL 反推

在「更多 → WD14 / CL 反推」中使用本地 ONNX 模型从图片估计标签。

需要的配套文件：

| 类型 | 文件 |
| --- | --- |
| WD14 | `.onnx` + `selected_tags.csv` |
| CL Tagger 1.01 | `.onnx` + `tag_mapping.json` |

支持单图和文件夹批量反推。反推结果可以编辑、保存到缓存、送入 txt2img 正面 Prompt，或交给 LLM 做格式转换、扩写和润色。

打开面板和检测模型不会自动下载模型。只有点击下载按钮时才会访问模型仓库。

### 外部交接

PNG Prompt Collector、Ranbooru 等扩展可以通过交接接口提交原始 Prompt。

推荐使用显式 schema：

```http
POST /llm-prompt-studio/v1/handoff
Content-Type: application/json
```

```json
{
  "schema": "prompt_studio.handoff.v1",
  "source_kind": "example",
  "source_ref": "record-001",
  "prompt": "1girl, standing, ...",
  "metadata": {}
}
```

交接数据最终仍只进入正向 Prompt 工作流。负面 Prompt 可以作为附带信息保留，但不会被本插件修改。

## 数据和隐私

插件数据位于扩展目录的 `user/`，该目录不会提交到 Git：

| 路径 | 内容 |
| --- | --- |
| `user/prompt_studio.db` | 原始缓存、设置、模板、词库索引和队列记录 |
| `user/processed_prompt_cache.db` | 处理结果库 |
| `user/credentials/llm_credentials.json` | 已保存的 API 凭据 |
| `user/backups/` | 删除操作的本地恢复数据 |

备份或迁移前关闭 Forge，再复制 `user/`。凭据文件不要上传到 GitHub 或分享给其他人。

## 排错

### 页面没有出现

重启 Forge，然后在浏览器执行 `Ctrl + F5`。确认扩展目录位于：

```text
<Forge 根目录>/extensions/sd-webui-llm-prompt-studio
```

### LLM 连接失败

检查服务商、地址、模型 ID 和 API Key。自定义 OpenAI 兼容服务通常需要填写正确的 `/v1` 路径，并确认服务端允许当前请求格式。

### 提示词生成成功但没有生图

确认生成去向不是「仅保存到缓存」。如果使用 Native Cache，确认 Forge 的 Script 为 `None`，并且批次数量与缓存数量一致。

### 缓存注入失败或游标没有推进

旧 token 可能已经过期，或者 Forge 任务没有完成。重新发起 Native Cache 生图；不要手动修改游标来伪造完成状态。

### 队列很久没有变化

刷新队列状态。超过过期时间的旧任务会自动标记为 `expired`，不会继续占用 Forge。新任务会在下一次入队时按需启动 worker。

### 反推模型不可用

确认 ONNX 模型与标签文件成对存在，并点击「重新检测模型」。缺少运行依赖时，在 Forge 使用的 Python 环境安装错误信息中指定的依赖，例如 `onnxruntime`、`numpy` 和 `Pillow`。

## 更新

在 Forge 根目录执行：

```powershell
git -C extensions/sd-webui-llm-prompt-studio pull --ff-only
```

然后重启 Forge 并强制刷新浏览器。

## 开发

主要代码入口：

| 文件 | 职责 |
| --- | --- |
| `scripts/llm_prompt_studio.py` | Forge 扩展入口和回调注册 |
| `scripts/prompt_studio_ui.py` | Gradio 页面、接口和队列 |
| `scripts/prompt_studio_core.py` | SQLite、LLM 请求、模板和状态持久化 |
| `scripts/prompt_studio_native.py` | Forge Native Cache 注入钩子 |
| `scripts/prompt_studio_wd14.py` | WD14 / CL ONNX 反推 |
| `javascript/llm_prompt_studio_auto_loop.js` | txt2img 内嵌流程和 Native Cache 生命周期 |
| `javascript/llm_prompt_studio_png_batch.js` | PNG 批次处理桥接 |
| `style.css` | 页面和内嵌面板样式 |

基本检查：

```powershell
python -m py_compile scripts/prompt_studio_core.py scripts/prompt_studio_ui.py
node --check javascript/llm_prompt_studio_auto_loop.js
git diff --check
```

完整开发约定见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## 相关项目

- [PNG Prompt Collector](https://github.com/Rivulet138/sd-webui-png-prompt-collector)
- [Ranbooru Forge Neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)

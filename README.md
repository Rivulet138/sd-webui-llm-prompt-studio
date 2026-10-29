# LLM Prompt Studio

Stable Diffusion Forge Neo 的 LLM 提示词工作室。

用于生成、整理、批量修改和管理提示词，并把结果交给 Forge 继续生图。

## 大纲

- [功能](#功能)
- [安装](#安装)
- [使用方式](#使用方式)
  - [配置模型服务](#配置模型服务)
  - [生成提示词](#生成提示词)
  - [管理缓存](#管理缓存)
  - [处理结果库](#处理结果库)
  - [模板和 System Prompt](#模板和-system-prompt)
  - [Forge 生图](#forge-生图)
  - [静态词库](#静态词库)
  - [图片反推](#图片反推)
  - [外部插件交接](#外部插件交接)
- [常见问题](#常见问题)

## 功能

### 提示词生成

- 根据自然语言创作要求生成提示词。
- 支持单条生成和批量生成。
- 支持固定主体、角色、服装、LoRA、触发词和技术标记。
- 支持连续生成、失败后继续、跳过已有缓存和任务预览。
- 支持多种输出格式和模型预设。

### 模板和提示词规则

- 提供内置模板。
- 支持修改内置模板内容。
- 支持新建、编辑、设为默认和删除自定义模板。
- 支持自定义 System Prompt。
- 支持保存不同输出预设和模型预设的配置。
- 支持调整批量变化维度、去重和跨批次记忆。

### 缓存管理

- 保存生成的提示词。
- 按关键词、格式、模型和来源查找。
- 查看、编辑和删除记录。
- 按查找结果批量修改提示词。
- 按位置批量删除记录。
- 支持撤销删除。
- 一键导出全部缓存。

### 处理结果库

- 对缓存提示词执行格式转换、扩写和润色。
- 保存独立的处理结果。
- 继续处理未完成任务。
- 选择结果追加到 Forge Prompt。
- 将结果加入生图队列。
- 对处理结果进行查找、批量修改和删除。

### Forge 生图联动

- 使用当前 Forge txt2img 参数生图。
- 从缓存库连续读取提示词。
- 支持追加到前面、追加到后面、替换和标记插入。
- 支持服务端生图队列。
- 支持 Native Cache 注入。
- 支持取消等待中的任务和停止后续生成。

### 静态词库

- 索引 TXT、CSV、JSON 和 YAML 词库。
- 按目录和文件分类。
- 搜索已索引词条。
- 将选中的词条追加到固定 Prompt。
- 词库内容可作为批量生成参考。

### 图片反推

- 支持 WD14 和 CL Tagger 1.01 ONNX 模型。
- 支持单图反推。
- 支持文件夹批量反推。
- 支持停止、继续未完成和重试失败。
- 反推结果可以编辑、保存、送入 Forge 或交给 LLM 继续处理。

### 外部插件交接

- 支持 PNG Prompt Collector。
- 支持 Ranbooru Forge Neo。
- 支持通过 HTTP 接口提交 Prompt 批次。
- 支持对外部 Prompt 执行转换、扩写和润色。

## 安装

在 Forge 的「扩展 → 从网址安装」中填写：

```text
https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

或在 Forge Neo 根目录执行：

```powershell
git -C extensions clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

安装后重启 Forge，并使用 `Ctrl + F5` 刷新浏览器。

## 使用方式

### 配置模型服务

打开 **LLM Prompt Studio → 设置**：

1. 选择服务提供方。
2. 填写模型服务地址。
3. 填写模型 ID。
4. 填写 API Key。
5. 点击「测试连接」。
6. 点击「保存并应用」。

支持的服务类型：

- OpenAI Responses。
- OpenAI Chat Completions。
- OpenRouter。
- Anthropic Claude。
- Google Gemini。
- DeepSeek。
- Ollama。
- LM Studio。
- 自定义 OpenAI 兼容接口。

Ollama 和 LM Studio 可以不填写 API Key。设置页还可以配置超时、输出 Token、温度、Top P、Top K、思考和备用连接。

### 生成提示词

1. 打开「生成」页。
2. 在「创作要求」中描述画面。
3. 填写需要固定的 Prompt、角色、LoRA 或触发词。
4. 选择模板、System Prompt 预设和模型预设。
5. 设置生成数量和批量选项。
6. 点击「生成到缓存」或「生成并入队生图」。

常用批量选项：

- 变化维度。
- 固定主体和技术标记。
- 静态词库抽样。
- 跳过已有缓存。
- 失败后继续。
- 任务预览。

### 管理缓存

打开「缓存」页：

1. 使用搜索框和筛选条件查找记录。
2. 点击记录查看详细内容。
3. 需要修改时，使用编辑区域保存单条记录。
4. 批量修改时填写「正向 Prompt 查找」和替换内容，再执行批量管理。
5. 删除记录时选择记录或填写位置范围。
6. 误删后点击「撤销删除」。
7. 需要备份时点击「导出全部缓存」。

### 处理结果库

打开「处理结果库」：

1. 选择原始缓存或筛选结果。
2. 选择操作：格式转换、扩写或润色。
3. 设置目标格式和模型预设。
4. 点击开始处理。
5. 在结果表中查看完成、失败和未完成数量。
6. 将结果追加到 txt2img，或加入生图队列。

处理结果库可以在「设置」中保存独立的推理参数。

### 模板和 System Prompt

打开「提示词」页：

1. 选择要编辑的内置模板或自定义模板。
2. 修改模板内容、固定 Prompt 规则和变化维度。
3. 点击「保存模板」。
4. 需要默认使用时点击「设为默认模板」。
5. 在 System Prompt 编辑区修改当前预设。
6. 保存当前预设，或恢复默认内容。

生成页和 txt2img 内嵌面板共用模板配置。

### Forge 生图

#### 服务端生图队列

在「生成」页选择「生成并入队生图」：

1. 先在 Forge 设置好模型、尺寸、采样器和步数。
2. 提交生成任务。
3. 在「生图队列」中查看进度。
4. 需要停止时点击「取消等待中的生图」。

#### Native Cache

在 Forge txt2img 页面打开插件内嵌面板：

1. 选择原始缓存库或处理结果库。
2. 设置批次数量。
3. 选择 Prompt 写入方式。
4. 点击 Forge 原生 Generate。

支持的写入方式：

- 追加到前面。
- 追加到后面。
- 替换当前 Prompt。
- 插入到标记位置。

使用 Native Cache 时，Forge Script 设为 `None`，缓存数量应与本次批次数量一致。

### 静态词库

打开「更多 → 静态词库」：

1. 设置词库目录。
2. 点击「索引静态词库」。
3. 按分类或关键词搜索词条。
4. 选择词条。
5. 点击「追加到固定 Prompt」。

### 图片反推

打开「更多 → WD14 / CL 反推」：

1. 选择已检测到的本地模型。
2. 上传图片，或切换到文件夹批量模式。
3. 设置标签阈值。
4. 点击「开始反推」。
5. 编辑结果后保存到缓存、送入 txt2img，或交给 LLM 继续处理。

模型文件要求：

| 类型 | 文件 |
| --- | --- |
| WD14 | `.onnx` 和 `selected_tags.csv` |
| CL Tagger 1.01 | `.onnx` 和 `tag_mapping.json` |

### 外部插件交接

其他插件可以向以下接口提交 Prompt：

```http
POST /llm-prompt-studio/v1/handoff
Content-Type: application/json
```

示例：

```json
{
  "schema": "prompt_studio.handoff.v1",
  "source_kind": "example",
  "source_ref": "record-001",
  "prompt": "1girl, standing, ...",
  "metadata": {}
}
```

## 常见问题

### 页面没有出现

重启 Forge，然后执行 `Ctrl + F5`。确认扩展目录为：

```text
<Forge 根目录>/extensions/sd-webui-llm-prompt-studio
```

### LLM 连接失败

检查服务商、地址、模型 ID 和 API Key。自定义 OpenAI 兼容服务通常需要填写正确的 `/v1` 路径。

### 生成成功但没有生图

确认生成去向选择的是「生成并入队生图」，而不是「生成到缓存」。

### Native Cache 注入失败

确认 Forge Script 为 `None`，批次数量与缓存数量一致，然后重新发起 Native Cache 任务。

### 找不到反推模型

确认 ONNX 模型与标签文件在同一模型目录中，点击「重新检测模型」。

### 更新插件

在 Forge 根目录执行：

```powershell
git -C extensions/sd-webui-llm-prompt-studio pull --ff-only
```

重启 Forge 后刷新浏览器即可。

## 相关项目

- [PNG Prompt Collector](https://github.com/Rivulet138/sd-webui-png-prompt-collector)
- [Ranbooru Forge Neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)

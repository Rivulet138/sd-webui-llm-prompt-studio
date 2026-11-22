# LLM Prompt Studio

面向 Stable Diffusion Forge Neo 的提示词扩展。通过 LLM 生成、整理、转换和润色 Prompt，并将结果送入 Forge 生图。

## 功能

- **生成**：单条或批量创作，支持固定 Prompt、模板、LoRA、触发词和技术标记。
- **批次多样性**：变化维度、静态词库配方、相似度重试和跨批次记忆。
- **缓存**：按关键词、格式、模型和来源筛选，编辑、批量替换、删除、撤销和导出。
- **处理结果库**：独立保存转换、润色和扩写结果，支持“转换后润色”或“转换后扩写”，可续跑未完成任务。
- **静态词库**：索引 TXT、CSV、JSON、YAML，按目录和文件分类，搜索并追加词条或用于批次抽样。
- **图片反推**：WD14 / CL Tagger 单图和文件夹批量识别，可编辑结果并送入缓存、Forge 或 LLM。
- **Forge 生图**：服务端生图队列，以及 txt2img 缓存注入。
- **外部交接**：支持 PNG Prompt Collector、Ranbooru Forge Neo 和 HTTP Prompt 接口。

## 安装

在 Forge「扩展 → 从网址安装」中填写：

```text
https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

或在 Forge Neo 根目录运行：

```powershell
git -C extensions clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

安装后重启 Forge 并刷新浏览器。

## 快速开始

1. 打开「设置」，选择服务并填写地址、模型 ID 和 API Key，测试后保存。
2. 打开「生成」，填写创作要求，选择输出预设并生成到缓存或生图队列。
3. 在「缓存」查找和管理 Prompt，或在「处理结果库」执行转换、润色和扩写。
4. 在 txt2img 面板选择缓存来源、批次数量和 Prompt 写入方式，再点击 Forge 的 Generate。

支持 OpenAI Responses、OpenAI Chat Completions、OpenRouter、Anthropic Claude、Google Gemini、DeepSeek、Ollama、LM Studio 和 OpenAI 兼容接口。Ollama 与 LM Studio 可连接本地服务。

## 生成与输出预设

批量生成可设置数量、变化维度、静态词库抽样、跳过已有缓存、失败后继续和任务预览。可在「提示词」页的“批量多样性”中启用词库配方，并配置相似度重试和跨批次记忆。

通用输出预设：Danbooru 标签、Danbooru + 自然语言、自然语言。模型预设：Pony / Illustrious、NoobAI、Flux、Anima、Krea 2。

## 缓存处理

「处理结果库」支持格式转换、润色、扩写、转换后润色和转换后扩写。处理连接与生成连接分别配置，结果独立于原始缓存保存，可继续未完成任务。

## 词库与图片反推

词库按 TXT、CSV、JSON、YAML 索引。修改文件后重新索引。词条可追加到固定 Prompt，也可用于批量配方。

本地反推模型需包含模型 `.onnx` 文件和标签映射文件：WD14 使用 `selected_tags.csv`，CL Tagger 使用 `tag_mapping.json`。

## Forge 生图

- **服务端队列**：生成时选择「生成并入队生图」，使用当前 Forge txt2img 参数，并在队列页查看或取消任务。
- **txt2img 缓存注入**：选择原始缓存或处理结果库，设置数量和写入方式。支持前置、后置、替换和标记插入。

## 文档

- [提示词规则与模板参考](docs/PROMPTS_REFERENCE_CN.md)
- [开发与测试说明](docs/DEVELOPMENT.md)
- [更新记录](CHANGELOG.md)

## 外部 Prompt 交接

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

## 更新

在 Forge 根目录运行：

```powershell
git -C extensions/sd-webui-llm-prompt-studio pull --ff-only
```

## 相关项目

- [PNG Prompt Collector](https://github.com/Rivulet138/sd-webui-png-prompt-collector)
- [Ranbooru Forge Neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)

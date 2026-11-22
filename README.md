# LLM Prompt Studio

Stable Diffusion Forge Neo 扩展，用于生成、整理和处理提示词，并连接 Forge 生图流程。

## 功能

- LLM 单条与批量生成，支持固定 Prompt、模板、模型预设和批次多样性。
- 提示词缓存、搜索、编辑、批量替换、导出与撤销删除。
- 格式转换、润色、扩写，支持转换后继续润色或扩写。
- 静态词库索引、搜索和批量抽样。
- WD14 / CL Tagger 单图及文件夹反推。
- Forge txt2img 注入与生图队列。
- PNG Prompt Collector、Ranbooru 及 HTTP Prompt 交接。

## 安装

Forge「扩展 → 从网址安装」：

```text
https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

或在 Forge Neo 根目录运行：

```powershell
git -C extensions clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

重启 Forge 并刷新浏览器。

## 快速开始

1. 在「设置」配置模型服务、模型 ID 和 API Key，测试并保存。
2. 在「生成」填写创作要求，选择输出预设并生成。
3. 在「处理结果库」转换、润色或扩写缓存提示词。
4. 将提示词加入生图队列，或在 txt2img 使用缓存注入。

支持 OpenAI Responses、OpenAI Chat Completions、OpenRouter、Anthropic Claude、Google Gemini、DeepSeek、Ollama、LM Studio 和 OpenAI 兼容接口。Ollama 与 LM Studio 可使用本地服务。

内置输出预设包括 Danbooru 标签、Danbooru + 自然语言、自然语言，以及 Pony / Illustrious、NoobAI、Flux、Anima 和 Krea 2 模型预设。

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

# About LLM Prompt Studio

LLM Prompt Studio 是面向 Stable Diffusion Forge Neo 的提示词工作台。它把模型连接、模型适配模板、提示词生成/转换/扩写/润色、批处理和缓存放在同一界面，并通过稳定的 `prompt_batch.v1` 数据格式与 PNG Prompt Collector、Ranbooru 交接。

模板按目标底模和操作分别维护：标签型模型保留其训练语法，自然语言模型不会被套用 SD 标签质量串。模板依据模型维护者资料及官方提示词工程原则整理，不代表服务商发布的固定 system prompt。

设计目标是可扫描的工作流：先选操作和底模，再输入要求，最后查看状态和结果。所有主控件支持键盘焦点，界面在窄屏下自动换行。

PNG Prompt Collector 的批次和 Ranbooru 的缓存都可以进入同一个 JSON 批处理入口。Ranbooru 联动区的“载入到 LLM 批处理”会按筛选条件建立批次，之后使用对应底模的转换、扩写或润色模板逐条处理，并可追加到 txt2img/img2img 或导出结果。

参考：

- [OpenAI Prompt Engineering](https://platform.openai.com/docs/guides/prompt-engineering)
- [Anthropic Prompt Engineering](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [Gemini Prompting Strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies)

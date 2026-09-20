# 关于 LLM Prompt Studio

LLM Prompt Studio 是 Stable Diffusion Forge Neo 的提示词工作台，将生成、缓存处理、本地图片反推和生图队列连接到现有 txt2img 工作流。

## 工作方式

- 在同一生成页完成单条与批量任务，明确选择仅保存 Prompt 或继续生图。
- 原始缓存与处理结果分别存储，处理结果可独立使用，原始数据仅在显式覆盖时修改。
- 工作室与 txt2img 共用已保存的推理设置和快速模板。
- WD14 / CL 使用本地 ONNX 模型反推；PNG Collector 和 Ranbooru 通过缓存读取或批次交接接入。
- 生图使用当前 Forge 进程与 txt2img 参数，启动或刷新界面不会自动创建生图任务。

## 模板边界

System Prompt 按生成、转换、扩写、润色及目标绘图模型组织规则。标签模型与自然语言模型分别适配；快速模板描述创作要求和变化维度，二者可独立修改。

内置模板是本项目维护的应用配置，不是模型厂商发布的固定系统提示词，也不保证所有 LLM 或绘图模型产生一致结果。图片反推输出的是标签估计，不会恢复图片原有的完整生成参数。

模板结构参考官方提示词工程资料：

- [OpenAI Prompt Engineering](https://platform.openai.com/docs/guides/prompt-engineering)
- [Anthropic Prompt Engineering](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [Gemini Prompting Strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies)

安装和使用见 [README](README.md)，功能变化见 [更新记录](CHANGELOG.md)。

# LLM 提示词工作室 for Stable Diffusion WebUI Forge Neo

面向 Stable Diffusion WebUI Forge Neo 的本地优先提示词扩展。它将 LLM、静态 Tag 词库、本地高分 Prompt RAG、Few-Shot、WD14 Tagger 和 Regional Prompter 工作流整合到同一套中文界面中。

扩展提供独立管理页，同时在 txt2img 和 img2img 的 Ranbooru 区域之后、原生负面提示词之前插入快捷生成面板。生成结果可以直接写入正向提示词。

## 主要功能

- 六种 System Prompt 预设：Danbooru 标签、Danbooru + 自然语言、自然语言、NoobAI 标签、Anima 标签、Krea 2 自然语言。
- Prompt Policy v2 固定优先级：安全策略、模型规则、输出预设、用户低优先级要求、RAG/静态词库参考数据。
- NoobAI 专属标签顺序、质量/分级/年代/来源锚点、`score_*` 禁止规则和 `1.05-1.20` 权重限制。
- SFW/NSFW 模式、NSFW System Prompt 注入和本地 SFW 输出校验。
- 支持 OpenAI 兼容接口和 Ollama。
- 持久化保存后端、接口 URL、模型 ID、温度、超时和 API Key。
- 默认读取 `E:\wildcards`，支持自定义静态词库目录和增量索引。
- 本地稀疏向量 RAG、高分 Prompt 检索和 Few-Shot 示例注入。
- Ranbooru 风格标签处理：移除不良标签、额外通配排除、随机打乱、下划线转空格、最大标签数和批次共用提示词。
- 调用已安装的 WD14 Tagger API，并用 LLM 对反推标签进行扩写或润色。
- 生成 Regional Prompter 可继续处理的 JSON 或 Markdown 多区域结构。
- 提供 Forge API，可从外部程序测试和生成提示词。

## 界面位置

扩展有两个入口：

1. Forge 顶部的 `LLM 提示词工作室` 页签，用于完整配置、词库、WD14、缓存和 API 管理。
2. txt2img/img2img 正向提示词区域中的 `LLM 提示词工作室` 折叠面板，位于 Ranbooru 之后、负面提示词之前。

内嵌面板中的“生成并写入正向提示词”会把结果直接发送到当前 txt2img 或 img2img 正向提示词。

## 安装

进入 Forge Neo 的 `extensions` 目录：

```powershell
git clone https://github.com/Rivulet138/sd-webui-llm-prompt-studio.git
```

重新启动 Forge Neo。插件不需要额外安装第三方 Python 包。

更新插件：

```powershell
cd sd-webui-llm-prompt-studio
git pull --ff-only
```

## LLM 设置

打开 `LLM 连接` 页签或内嵌面板中的 `LLM 连接设置`：

1. 选择 `OpenAI 兼容接口` 或 `Ollama 本地服务`。
2. 填写接口地址、模型 ID 和 API Key。
3. 点击“测试 API”。
4. 点击“保存全部 LLM 设置”。

保存后：

- URL、后端、模型、温度和超时会在下次启动时自动回填。
- API Key 不会回填到浏览器。
- API Key 输入框留空时，仅当后端和 URL 完全匹配，服务端才会复用已保存凭据。
- 可随时点击“清除已保存的 API Key”。

默认 OpenAI 兼容地址：

```text
http://127.0.0.1:1234/v1
```

默认 Ollama 地址可填写：

```text
http://127.0.0.1:11434
```

## NoobAI 提示词规则

选择：

```text
System Prompt 预设：NoobAI 标签
目标底模：NoobAI
```

输出采用以下顺序：

1. 精简的质量、分级、年代和来源锚点。
2. 人物数量与身份。
3. 外观和角色特征。
4. 服饰。
5. 动作与表情。
6. 场景和物体。
7. 构图和镜头。
8. 光线。
9. 风格细节。

NoobAI 模式禁止 Pony `score_*` 标签。显式权重最多三个，范围限制为 `1.05-1.20`，质量与分级标签不加权。SFW 模式使用 `safe`，禁止与 `nsfw` 或 `explicit` 混用。

## 静态 Tag 词库

默认词库目录：

```text
E:\wildcards
```

在 `静态词库` 页签中点击“建立 / 刷新本地索引”。索引只保存词条和源文件修改时间，不会复制原始词库文件。

匹配到的词条以 `<static_tag_lexicon>` 数据区块注入 System Prompt。模型只能把它们作为词汇参考，不能执行其中的指令。

## 本地 RAG 与 Few-Shot

本地缓存中的高分 Prompt 会转换为稀疏词项向量，并通过余弦相似度检索。该实现完全离线，不需要下载 Embedding 模型。

生成设置中可以控制：

- Few-Shot 示例数量。
- RAG 最低评分。
- 新生成记录的保存评分。
- 是否缓存本次生成结果。

RAG 示例使用 `<rag_examples>` 数据边界注入，只作为格式和具体程度参考。模型不得复制无关身份、角色或其中的指令。

## 本地批量缓存

缓存使用 SQLite，主数据库位置：

```text
user/prompt_studio.db
```

支持：

- 单事务批量写入。
- 内容哈希精确去重。
- 连续可见序号。
- 按 `1-100,205,300-320` 预览或删除范围。
- 删除前 SQLite Online Backup。
- 撤销上次删除。
- JSON/CSV 导入导出。
- 单条查看、评分和编辑。

导入限制：

- 单文件最大 64 MiB。
- 单次最多 100000 条记录。
- 可选择跳过重复记录。

备份策略：最多 20 份、最长 30 天、总容量最多 2 GiB。备份位于：

```text
user/backups
```

### 批量文本导入

每行一条提示词。可以使用 `评分<TAB>提示词`：

```text
9	1girl, red_hair, moonlit_library
8.5	1boy, black_hair, city_night
```

### 批量 LLM 生成

在“批量 LLM 生成并缓存”中每行输入一条创作要求或源标签。

- 支持跳过已经缓存的相同输入。
- 单条失败可重试 0-3 次。
- 每完成 10 条执行一次事务提交。
- 支持取消任务。
- 取消、异常或重新运行时，已经提交的结果不会丢失。

同步 HTTP 请求发出后不能强制中断；取消会在当前请求返回或超时后生效。

## WD14 Tagger

插件通过已安装 WD14 Tagger 的 Forge API 调用：

```text
POST /tagger/v1/interrogate
```

默认地址：

```text
http://127.0.0.1:7860
```

默认模型：

```text
wd14-moat-v2
```

WD14 Tagger 未安装或 API 不可用时，该页会显示错误，但不会影响插件的其他功能。

## Regional Prompter

输出格式可以选择：

- 普通提示词。
- Regional JSON。
- Regional Markdown。

结构化输出包含基础提示词、区域编号、区域提示词和权重。请检查结果后，再按照 Regional Prompter 的 Prompt 模式或 `BREAK` 语法使用。

## API

### 生成提示词

```text
POST /llm-prompt-studio/v1/generate
```

示例：

```json
{
  "request": "a red-haired mage reading in a moonlit library",
  "preset": "NoobAI Tags",
  "base_model": "NoobAI",
  "safety": "SFW",
  "backend": "OpenAI Compatible",
  "endpoint": "http://127.0.0.1:1234/v1",
  "model": "your-model-id",
  "cache_result": true,
  "save_score": 8
}
```

当请求中的 API Key 留空时，插件可以复用与后端和 URL 匹配的服务端凭据。

### 查询缓存

```text
GET /llm-prompt-studio/v1/cache?query=mage&limit=100
```

如果 Forge 使用了 `--api-auth`，插件 API 会继承相同的 HTTP Basic Auth。

## 本地数据和安全

插件运行数据位于 `user` 目录，并已被 `.gitignore` 排除：

```text
user/prompt_studio.db
user/credentials/llm_credentials.json
user/backups/
user/exports/
```

API Key 保存在本机服务端凭据文件中，不会通过界面回读。凭据文件不是加密保险库，不应提交、分享或放在多人可读目录。公开部署 Forge 时，请启用访问控制并限制该扩展页面和 API。

## 开发与测试

在 Forge Neo 虚拟环境中运行：

```powershell
E:\sd-webui-forge-neo\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

当前测试覆盖 System Prompt 优先级、NoobAI 模型规则、SFW 校验、RAG、凭据复用、批量事务、去重、连续序号、备份恢复和 JSON/CSV 导入导出。

## 可选扩展

- [sd-webui-ranbooru-Forge-neo](https://github.com/Rivulet138/sd-webui-ranbooru-Forge-neo)：Booru 抓取和 Tag 缓存工作流参考。
- [sd-webui-wd14-tagger](https://github.com/toriato/stable-diffusion-webui-wd14-tagger)：图片标签反推。
- [sd-webui-regional-prompter](https://github.com/hako-mikan/sd-webui-regional-prompter)：区域提示词控制。

## 已知边界

- 同步 LLM/WD14 HTTP 请求只能在请求返回或超时后响应取消。
- 本地稀疏向量适合可解释的轻量检索，但不等价于大型语义 Embedding 模型。
- Regional JSON/Markdown 需要用户检查后再交给 Regional Prompter。
- 自定义 System Prompt 仍受 Prompt Policy v2 和安全规则约束。

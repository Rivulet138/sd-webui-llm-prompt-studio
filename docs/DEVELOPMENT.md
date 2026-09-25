# 开发与验证

## 代码入口

| 文件 | 职责 |
| --- | --- |
| `scripts/llm_prompt_studio.py` | 注册 Forge 回调，设置扩展模块搜索路径 |
| `scripts/prompt_studio_ui.py` | Gradio 页面、工作流、内部接口与 Forge 生图队列 |
| `scripts/prompt_studio_core.py` | SQLite、凭据、LLM 请求、模板及数据处理 |
| `scripts/prompt_studio_wd14.py` | 本地 WD14 / CL 模型发现与 ONNX 推理 |
| `scripts/prompt_studio_tagger_models.py` | 可选模型目录、固定版本下载与本地复用 |
| `scripts/prompt_studio_image_batch.py` | 图片目录扫描、批量任务、停止和续跑 |
| `javascript/llm_prompt_studio_auto_loop.js` | txt2img 内嵌流程、连续生成、队列提交与状态 |
| `javascript/llm_prompt_studio_png_batch.js` | PNG 批次桥接与结果写入 |
| `style.css` | 工作室及内嵌面板样式 |

## 修改边界

- 入口名称、页面职责和操作流程以 [README](../README.md) 为准；调整工作流时同步更新。
- 单条与批量生成共用入口，工作室与 txt2img 共用模板和已保存的推理设置；明确区分仅缓存、写入 Prompt 和入队生图。
- 高级选项默认收起，文本框随内容增长；避免重复摘要和控件。新增界面检查桌面及窄屏布局、唯一元素 ID、文字状态、键盘焦点和减少动画偏好，以 WCAG 2.1 AA 为可访问性目标。
- 缓存处理默认另存结果，覆盖原始记录必须显式操作；空选不扩大为全部记录。进度区显示完成、未完成和失败数量，并提供重试入口。
- 仅标明 `0 = 无限` 的数量控件允许持续执行，始终保留停止入口；有限集合的读取范围单独说明。
- UI 事件与后端接口成对修改，检查可见性、空选、停止、失败及重复提交。
- 浏览器生产下一条 Prompt 与服务端执行已入队任务是不同生命周期；不要将页面关闭和最小化混为一谈。
- 启动、查询状态、读取缓存和模板选择不得启动生图任务；启动修复需有回归覆盖。
- `user/` 包含数据库与凭据；下载模型、运行日志、截图和本地审查笔记不提交。公开测试使用临时目录和虚构凭据。
- 模型发现保持离线；只有用户点击下载才访问模型仓库，模型与标签文件需成对。

跨插件批次使用 `prompt_batch.v1`。修改格式时同时检查 PNG Collector 与 Ranbooru 的读取、写回及错误处理；插件未安装或未提供缓存时，应返回可理解的状态。

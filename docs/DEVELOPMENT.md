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

Native Cache 在成功注入 Forge 的整批 Prompt 时即持久化完成状态并提交整批游标，不等待实际产图。注入前取消或失败不提交；成功注入后取消或异常不回滚，重复 release 保持已提交结果，不增加 Forge 轮询或逐图回调追踪。

Native Cache 的 claim 只在原生 `submit` / `submit_img2img` 返回的 Prompt 参数中附加，不写入可见文本框，不触发 Prompt 的 input/change 联动。原生启动必须等到本轮 submit 捕获，按钮或日志变化不能代替该确认；没有准备上下文的普通生图 submit 在缓存注入启用时应报错，不得静默退回基础 Prompt 生图；图库 Upscale 的独立参数布局保持原生行为。浏览器停止等待后，仍存在的 Forge task ID 与中断控件继续代表忙碌，不得伪造完成状态。

资源约束：Native Cache prepare 返回 `cache_state: {config, cursors}`，前端每批只发 prepare，保留面板初始化和用户编辑保存请求。等待已捕获的 Forge 任务期间只检查任务标识，终态再读取日志；无任务标识的兼容路径保留原状态检测。控件绑定记录 host 与 input/button 身份，稳定页面更新仅检查连接与包含关系，控件重新挂载时恢复设置，submit 包装独立按函数引用检查。内存上下文字典只保存 `created_at/state`，完整记录保存在数据库中。队列视图最多显示 100 条，未变化内容不重复写 DOM；任务完成判断始终使用完整 `counts`。

## 后台调度回归

```powershell
node --test tests/test_auto_loop.cjs
python -m unittest discover -s tests -p 'test_*.py'
# Forge 根目录（同时应用保活补丁时）
node --test tests/test_keep_alive.cjs
```

插件的 Worker 等待与 Forge 的动画帧保活分别验证：前者覆盖前台启动等待后隐藏页面，后者覆盖隐藏切换时已排队回调的迁移、取消和一次性执行。不要将本插件的修复描述为全局计时器替换。

浏览器验证必须读取 `document.hidden` / `document.visibilityState`。Playwright 默认焦点模拟和后台限流关闭参数会掩盖问题；独立 Chrome 经原生 CDP 最小化并确认 `hidden === true` 后，再检查 Gradio 输入更新、事件链和 Native Cache 注入。不能用前台自动化成功代替后台验证，也不能把最小化支持等同于浏览器冻结或电脑睡眠支持。

## 模型预设与补充指令

- 最终模型对应的输出预设与操作指令决定格式、语言、长度和结构；通用 System Prompt 不另设相互冲突的格式合同。
- 用户请求和用户输出要求是需要执行的授权指令，不标记为低优先级，也不归入 inert reference data；按目标预设呈现内容并遵守当前安全模式。词库、示例与待处理的源文本不能提升为指令。
- System Prompt 编辑区只保存当前预设/模型的兼容补充要求，不替换内置输出预设。请求构造时剔除旧版整块输出预设及重复规则；不直接迁移或覆盖用户数据库。
- 运行入口先对齐最终预设与模型，再读取该目标的补充配置；无匹配配置时不继承其他目标的 System Prompt。读取兼容同一最终目标的旧版 profile 键，缺少归属字段的旧 workflow 按原保存目标保留补充内容；显式清空时同时清理该目标的旧键，避免再次加载已清空内容。
- Krea 2 / Anima 的 Polish 使用专用三段式操作预设，保留标签锚点、分层细节、精炼主体与 Master Description；共用构造器接入用户要求与模式注入，不叠加普通生成的单段/标签行格式或长度预算。分层结果按操作契约校验和保留，不能对整篇执行标签清理；其他操作仍使用普通预设。
- 批次内容与本次重试纠错分别传递、分别限制长度，重试不得丢失稳定的批次要求。

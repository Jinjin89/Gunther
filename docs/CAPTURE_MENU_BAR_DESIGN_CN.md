# Gunther Capture 独立窗口与 macOS 菜单栏设计

## 1. 产品原则

**Capture 是独立的采集工具，Library 是内容的长期归宿。**

文档、网页、图片、笔记、表格和录音都是平级的来源。录音之所以在菜单栏多一组控制动作，是因为它具有持续时间和后台状态，并不代表它高于其他采集方式。

Gunther 只维护一个 Capture 实例。主界面、知识库页和菜单栏都只负责唤起这个实例；已有录音时，任何新入口都应回到当前会话，不能覆盖或并行创建第二场录音。

## 2. 入口与窗口

### 主界面入口

- 顶部 `Capture`：打开独立 Capture 窗口，显示全部采集方式。
- 知识库内 `Add`：打开同一窗口，并预选当前知识库为归档目标。
- 知识库内 `Record`：打开同一窗口，直接进入录音，并预选课程、会议或语音备忘场景。
- 浏览器开发模式保留页面内面板作为兼容回退；macOS 应用始终优先使用独立窗口。

### 独立窗口

- 默认尺寸约 `760 × 760`，最小尺寸 `560 × 560`。
- 可移动、可缩放、可使用系统最小化和最大化；完整窗口默认不强制置顶。
- 标题区域可拖动窗口，输入框、按钮、选择器不会误触拖动。
- 关闭 Capture 窗口等于隐藏到菜单栏，不卸载录音组件，也不停止录音。
- 关闭主窗口后 Gunther 继续在菜单栏运行；从菜单栏可重新打开主界面或 Capture。

## 3. 两套互不干扰的状态

采集状态：

`Ready → Requesting → Recording ⇄ Paused → Finalizing → Review ready → Saving → Saved`

显示状态：

`Visible ⇄ Minimized ⇄ Hidden`

因此，移动、最小化、隐藏或关闭主窗口都不能改变采集状态。只有 `Finish Recording` 可以结束录制；只有用户确认保存后，内容才进入 Inbox 或指定 Library。

## 4. 菜单栏图标

菜单栏使用专门的 Capture glyph，而不是缩小版应用图标：四个取景框角标表示“从外部采集”，中央符号表示当前采集状态。空闲态作为 macOS Template Image 自动适配浅色和深色菜单栏；活动态用形状与文字共同表达，不能只依赖颜色。

| 状态 | 中央图形 | 菜单栏文字 |
|---|---|---|
| Ready | 空心圆点 | 无计时 |
| Draft open | 中性实心小圆点 | `Capture draft open` |
| Requesting / Importing | 环形或忙碌点 | `Preparing` |
| Recording | 红色实心圆 | 经过时间，如 `12:48` |
| Paused | 双竖线 / 琥珀提示 | 暂停时间，如 `Paused · 12:48` |
| Review ready | 对勾 | `Review` |
| Attention | 感叹号 | `Needs attention` |

图标按约 20 px 的 RGBA 像素生成，不依赖额外图片文件；即使颜色不可见，形状和菜单状态行也能区分状态。

## 5. 菜单动作

### 空闲时

- `Gunther · Ready`（不可点击的状态行）
- `Open Capture…`
- `New Recording…`
- `Quick Note…`
- `Open Gunther`
- `Quit Gunther`

### 录音时

- `● Recording · 12:48`（不可点击的状态行）
- `Open Capture…`
- `Pause Recording` / `Resume Recording`
- `Mark Moment`
- `Finish Recording`
- `Open Gunther`
- `Quit Gunther…`

单击菜单栏图标会打开动作菜单，第一项 `Open Capture…` 负责显示现有窗口；Pause、Mark 和 Finish 因而不依赖难发现的右键操作。`Finish Recording` 只进入 Review，不自动将未经确认的内容写入知识库。菜单中不提供高风险的 `Discard`。

## 6. 关闭与退出

- 关闭空闲 Capture：隐藏窗口。
- 关闭正在录音的 Capture：隐藏窗口，录音继续。
- 关闭主窗口：隐藏主窗口，菜单栏继续运行。
- 录音、请求权限、导入、存在未完成 Review，或 Note/Link/File 等采集已有未保存输入时选择退出：阻止退出并显示 Capture，提示用户先保存、完成或保留草稿。
- 用户完成并保存后，可再次选择 `Quit Gunther` 正常退出。

非录音内容也必须参与同一条 singleton 保护：一旦标题、正文、网址或文件发生变化，新的 Capture 入口只能显示现有窗口，不能重置表单。只有保存、明确返回并放弃，或将内容转为可恢复草稿后，状态才可以回到 Ready。

这里不保留一个隐式“保存后自动退出”标记，避免用户选择继续工作后，未来某次普通保存意外终止应用。

## 7. 失败与安全

- SenseVoice 暂时断线：音频继续保存，转写显示重连状态。
- 后端暂时不可用：录音分片和草稿保存在本机，恢复连接后重试。
- 麦克风或系统睡眠中断：保留已写入的音频，标记为 interrupted，允许下次恢复。
- 保存失败：保持 Review 内容和本地重试副本，不静默关闭窗口。
- 首次麦克风权限必须由用户本人确认；拒绝后应允许导入已有音频。

## 8. 平台边界

当前录音仍由 Tauri WebView 内的浏览器音频栈持有。macOS 14 及以上可关闭隐藏 WebView 的后台节流，适合本版的独立窗口与菜单栏后台体验；macOS 11–13 虽仍可运行 Gunther，但不能把隐藏窗口下持续数小时的稳定性表述为已经保证。若要对这些系统提供同等级的长时录音承诺，后续需把音频采集迁移到原生进程，并让 WebView 只负责显示和控制。

## 9. 核心验收场景

1. 从主界面打开 Capture，并把窗口拖到另一块屏幕。
2. 开始录音，看到菜单栏图标和计时变为 Recording。
3. 隐藏 Capture、关闭主窗口，确认录音、计时、音频落盘和转写仍继续。
4. 从菜单栏标记、暂停、继续并重新打开同一场录音。
5. 从菜单栏结束录音，进入 Review；保存后内容出现在 Inbox 或预选 Library。
6. 活动录音时尝试退出，确认应用阻止退出且没有丢失会话。
7. 重新启动后，确认未保存草稿或 interrupted 录音仍可恢复。

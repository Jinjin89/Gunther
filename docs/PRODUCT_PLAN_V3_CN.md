# Gunther 产品规划 v3

更新时间：2026-08-30  
事实基线：当前源码，数据库 schema v10

## 1. 一页结论

### 一句话定位

Gunther 是一个**本地优先的多来源知识工作区**：把笔记、文件、图片、网页、搜索结果和录音可靠收进来，在证据范围内理解材料，由用户确认长期知识，并把它们生成可追溯、可版本化的成果。

### 用户承诺

> 先把材料安全留下，再决定放哪里；每个结论都能回到来源，每份成果都能回到当时采用的知识版本。

### 产品主线

```text
Capture first
   ↓
Inbox：归类、补信息、处理失败、审核建议
   ↓
Libraries：围绕长期主题组织 Sources、Sessions、Knowledge
   ↓
Outputs：从已接受知识生成不可变成果版本
```

录音很重要，但不是产品的唯一入口。它和文件、照片、Link、Quick note、Web search 一样，都是统一 Capture 中的专门流程；录音只是因为持续时间长、状态多、失败成本高，需要独立工作区和恢复设计。

## 2. 要解决的核心问题

传统知识工具常要求用户在采集前回答太多问题：先选数据库、先建目录、先命名、先决定永久归属。真实工作恰好相反：课程正在讲、会议正在进行、手机刚收到一个文件，用户只想先保存。

Gunther 因此解决四件事：

1. **捕获成本低**：任何页面和系统分享都能进入同一个 Capture 体系。
2. **原件可靠**：上传、录音或离线失败不会把未确认内容伪装成成功。
3. **理解有依据**：问答、摘要、知识建议和 Outputs 都能追溯到 Source / revision。
4. **信任由用户决定**：AI 建议与长期知识之间有明确 approval boundary。

## 3. 用户只需要理解的对象

| 对象 | 用户语言 | 生命周期 |
| --- | --- | --- |
| Capture | “把这个收进来” | 一个动作；成功后进入 Note、Source 或录音恢复态 |
| Inbox | “还没整理完的东西” | 可归类、重试、暂存或审核，不是永久分类系统 |
| Library | “我长期研究的一个领域” | 汇聚不同来源，并承载问答、知识和 Outputs |
| Source | “一份被保存、可引用的材料” | 指向原件/快照与提取正文，保留 provenance |
| Knowledge | “我确认愿意长期采用的结论” | 由 Proposal 经用户接受后形成不可变 revision |
| Output | “我要拿出去使用的成果” | 持久 Artifact lineage，每次再生成都是新版本 |

### Notebook 到底是什么

Notebook / Note 是**快速记录与编辑界面**，不是临时知识库，也不是所有内容的上级容器。

- 一条新 Note 可以在不知道 Library 的情况下创建；
- 未归类 Note 出现在 Inbox；
- 用户归类后，它被提升为对应 Library 的 Source；
- Note 原始编辑状态与 Source 的保存快照是不同阶段。

第一层导航不需要把 Notebook 做成与 Libraries 并列的“第二套世界”。入口可以是快捷键、Home 的 Quick note 或 Inbox 中打开编辑。

## 4. 信息架构

### 桌面端

一级导航保持精简：

- **Home**：继续最近工作、查看 Inbox、最近 Libraries 和录音恢复；
- **Libraries**：浏览或新建长期主题空间；
- **Inbox**：未归类材料、失败重试、待审核建议；
- **Search**：搜索本地知识，必要时切换 Web research；
- **Capture**：固定在标题栏的全局动作，不作为另一套导航。

Library 内部模式：

- Overview：主题、材料与进度；
- Sources：原件、网页快照、OCR 与 provenance；
- Ask：限定材料范围的 Session；
- Review：Assertions / Proposals / KnowledgeUnits；
- Outputs：持久 Artifact 历史、详情与新版本。

### 移动端

底部导航只保留：

- Home；
- Libraries；
- Inbox。

Capture 使用全局按钮或 bottom sheet 打开。系统 Share Sheet 是另一个同等级入口：从浏览器、相册、文件或其他 App 分享到 Gunther 后，先进入 native staging，再进入 durable outbox。

移动端负责“随时捕获、查看状态、轻量归类与继续录音”；桌面负责“长时间阅读、审核、问答、组织与产出”。两端共享同一 workspace 与后端事实，不建立两套数据库。

## 5. 统一 Capture 设计

### 入口

Capture 面板按用户意图呈现，而不是按技术实现分组：

- Quick note；
- Document / paper；
- Photo；
- Link；
- Recording；
- Import audio；
- Web research。

每个入口都先问最少信息：标题可自动推断，Library 默认是“稍后整理”。用户若从某个 Library 内发起 Capture，则预填该 Library，但仍允许改为 Inbox。

### 完成语义

界面必须区分：

- **正在保存本地副本**；
- **已安全进入本地队列**；
- **正在上传/处理**；
- **已由服务器确认**；
- **处理 degraded，但原件安全**；
- **需要用户操作**。

不能只显示一个模糊的“成功”。OCR 失败、STT 失败、正文抽取受限和上传失败是不同状态，且都不应自动删除原件。

### 当前已实现

- Quick note、文本、文件、图片、Link、Web research、实时录音和已有音频；
- 文件原件 SHA-256、Asset / Source 分离、路径边界和有界流式上传；
- 图片与扫描 PDF 的离线 OCR，包含页码/区域/Provider/置信度 provenance；
- Link 不可变网页响应快照与 SSRF / DNS rebinding 防护；
- 移动 quick note/source/link/web/document/photo/audio durable outbox；
- Android/iOS Share Sheet 原生接入并汇入 outbox；
- workspace/profile 目标绑定、幂等重试、服务确认后清理；
- Android 拍照进程中断结果恢复。

## 6. 录音体验

### 录音是一个可回到的工作区

点击 Recording 后进入专门页面，而不是在普通弹窗里塞一个计时器：

- 清晰的录制/暂停/恢复/停止；
- 实时转写和 Provider 状态；
- 可编辑 transcript；
- lecture / meeting / memo 场景；
- 重点时刻；
- 当前保存状态与恢复说明；
- 最小化为 dock，继续应用内工作，再次点击可展开原会话；
- 完成后保存 Source，可进入 Inbox 或指定 Library。

“应用内最小化”不是系统后台录音。进入 iOS/Android 后台或锁屏后的持续录制当前不承诺。

### 可靠性模型

```text
麦克风 / 既有文件
  → app-owned durable chunks / WAV
  → RecordingSession ledger
  → 已确认 offset 后清理本地 chunk
  → complete + Source

同一音频流
  → live transcription client
  → 本机后端
  → SenseVoice 或配置的在线回退
```

音频保存与实时转写是独立失败边界。转写掉线时仍保存音频；音频持久化不可用时则停止录音，不回退到易失内存。

macOS 首次录音必须由用户主动点击并响应系统 TCC 授权。包内用途说明和 audio-input entitlement 是必要前提，不等于用户已授权；授权等待有 15 秒上限、清理、系统设置指引与 Retry。应用不能绕过或替用户点击系统授权。

### 导入已有音频

桌面按固定块把文件复制到 IndexedDB，记录每块 SHA-256、大小、序号、workspace 和来源类型。初始完整本地复制结束后，上传即使崩溃也可从服务端 `nextExpectedSequence` 继续，不要求用户再次选择原文件。

边界必须写清：如果进程在**初始本地复制尚未完成**时退出，浏览器没有永久文件句柄，用户仍需重选原文件重新开始；已复制的残片会保留供诊断，不会被当成完整音频。

## 7. OCR、网页与搜索的角色

### OCR

OCR 的目标是“让扫描材料进入同一证据链”，不是把图片偷偷替换成纯文本。

- 原图/扫描 PDF 始终是权威 Asset；
- OCR 是派生处理结果；
- Capture 返回 completed / degraded 状态与 Provider，Source 正文保留 OCR provenance 与处理说明；
- 证据 locator 保存页码、region 与行信息；
- 超时、页数/大小上限或 Provider 缺失时保留原件和明确限制。

当前实现是同步且有界；多百页扫描书籍应后续迁移到可恢复后台 Job。

### Link 与 Web search

- **Link**：用户已经知道要保存哪个 URL；Gunther 抓取当时响应并形成不可变快照。
- **Web search**：用户还在寻找答案；Provider 返回带 URL 的研究结果，用户选择是否保存。

Web search 结果中的每个 URL 不会自动全部转为网页快照。需要长期引用时，应由用户进一步 Capture 对应页面。

## 8. 从材料到可信知识

### 问答

Session 必须绑定一个 Library，并可进一步固定 Source scope。每条回答保存 citation snapshot。优先检索匹配的结构化 Assertion；若范围内没有可用 Assertion，则从保存的原始 Source text 中检索少量相关段落，作为 `assertionId=null` 的 provisional 来源引用。结构化 Assertion 与原文段落都不足时返回缺口，而不是跨 Library 借用材料。

### Approval boundary

```text
AI answer / extraction
  → Proposal（候选）
  → 用户 accept / hold / reject
  → accepted KnowledgeUnit revision（可信长期知识）
```

“加入 Library”只表示归类；“接受 Proposal”才表示信任。界面文案和数据模型不能混淆二者。

## 9. Outputs / Artifact

Outputs 是产品闭环的最后一步：用户不是为了积累 Source，而是为了形成讲义、路径、决策简报或现场指南。

当前 v10 Artifact history 已实现：

- 只使用同一 Library 中 `trusted` KnowledgeUnits；
- 固定 accepted unit IDs 与当时的 head revision；
- 保存 provenance、evidence count、content/manifest hash；
- 每次生成创建不可变版本；
- 只有 lineage head 能产生下一版；冲突后刷新再继续；
- 可查看历史、重新打开、复制、下载 Markdown，并随 workbook 导出。

下一步不是再做一个临时编辑器，而是：

- 为 Artifact 增加归档/隐藏而不删除历史；
- 加入全局 Search 与跨 Library 的明确发现入口；
- 增加经过验证的 PDF/DOCX 渲染，同时保留 Markdown 交换格式；
- 对“采用了哪些证据”提供更直观的版本差异视图。

## 10. 当前完成与外部验证边界

### 已进入代码与自动化的能力

- Capture first、Inbox、Libraries 的主流程；
- 多格式文件保存、抽取、OCR 与网页快照；
- 实时录音、持久分片、检查点、恢复和 SenseVoice client；
- 移动 Share Sheet、durable outbox、workspace/profile 绑定；
- 有引用的问答、Proposal approval、KnowledgeUnit revisions；
- v10 不可变 Artifact history；
- 私有 desktop sidecar、安全 mobile gateway；
- 原子 backup / verify / safe restore，包含 Asset、Recording 与 Artifact 完整性检查。

### 仍需外部或发布级验证

- Android/iOS 真机或模拟器安装、权限、分享、拍照、录音、网络切换和恢复；
- iOS 在完整 Xcode 环境中的 build/archive/sign；
- Apple Developer ID、notarization、Android release keystore 与 AAB；
- 安全 gateway 跨物理手机的配对、证书变更、撤销和离线验收；
- 真实连续长时麦克风、休眠/锁屏、强杀、断网、磁盘压力与故障注入；
- 系统后台/锁屏持续录音；
- 大规模 OCR 后台 Job、Artifact 全局 Search、归档和 PDF/DOCX 输出。

源码自动化不能被描述成真机验收，也不能据此承诺“零丢失”或“正式发布”。

## 11. 路线图

### P0：把当前闭环验收成可用产品

- 在专用 Android/iOS 设备上走完 Capture、Share、配对、断网恢复和录音；
- 做真实长时录音与故障矩阵；
- 完成正式签名与平台分发门槛；
- 建立周期性真实备份恢复演练。

### P1：提高日常可发现性

- Artifact 加入 Search，并支持归档；
- Inbox 为 degraded OCR、失败 outbox 和未绑定分享提供更明确的统一任务卡；
- Library 首页直接显示“最近 Source / Session / Knowledge / Output”；
- 增加版本差异与 evidence coverage 提示。

### P2：扩展而不破坏本地优先

- 可恢复后台 OCR / indexing Jobs；
- 可选的加密跨设备同步；
- 经过权限设计的团队 Library；
- 结构化模板与正式文档渲染。

## 12. 成功指标

- 任一种 Capture 都能在最少步骤内进入明确的安全状态；
- 离线或服务中断后，用户能看见保留内容、目标 workspace 和下一步动作；
- 用户能从回答/Knowledge/Output 回到对应 Source 与 revision；
- 用户能清楚区分“已归类”“AI 建议”“已接受知识”和“已发布成果”；
- 一周后仍能从 Library 或 Search 找回材料和 Output，而不是依赖记住当时入口；
- 备份可以被验证并恢复到新目录，且不覆盖当前数据。

本轮工程候选已完成回归：Backend 177 passed/1 skipped、Desktop 74 passed、Rust 10 passed、Flutter 108 passed且 analyze 0 issues、Chrome UI→backend E2E 32/32。候选大小、SHA-256 与平台边界以 [`artifacts/releases/README_CN.md`](../artifacts/releases/README_CN.md) 和 [`artifacts/releases/SHA256SUMS`](../artifacts/releases/SHA256SUMS) 为准。

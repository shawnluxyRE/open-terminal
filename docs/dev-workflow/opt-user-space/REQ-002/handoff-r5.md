# opt-user-space / REQ-002 R5 验收交接

- stage：R5
- result：passed
- 修订：v1.3，2026-10-30（Asia/Shanghai）；独立复验 R4 v1.3，R5-002 中文归档 500 已关闭，690 项通过；按用户确认范围验收通过，保留两项非阻碍兼容性建议。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-002`
- 需求依据：[requirements.md](../requirements.md) v1.10 基线及 2026-10-30 用户直接确认的 [Office 预览验收范围](acceptance-scope.md)，REQ-002 / AC-005～009。
- 验收依据：[plan.md](plan.md) v1.0、[file-access.md](../../../design/file-access.md) v1.0 及用户范围补充；当前修复交付为 [handoff-r4.md](handoff-r4.md) v1.3，初审为 v1.1，v1.2 为用户不修复 R5-001 的历史回复。
- 审查对象：`codex/opt-req002` / HEAD `59eac68b4c0314ab633c376de82a539af3c98d52` 加全部未提交 REQ-002 交付；Open Terminal 0.14.5。开始快照见 [r5-source-start.json](evidence/r5-source-start.json)。
- next_role：R0

## 产物

初审新增 20 项测试，二次审查又追加 5 项。Office 内部引用的两项历史测试按用户决定排除运行，其他原断言和业务实现保持：

| 文件 | 新增覆盖 |
| --- | --- |
| [test_path_regressions.py](../../../../tests/req002/test_path_regressions.py) | 7 项：相对软链与 `..` 的实际 I/O 顺序、缺失中间项/断链不可经规范化绕过、公共根配置不能掩盖软链祖先。 |
| [test_search_regressions.py](../../../../tests/req002/test_search_regressions.py) | 初审 11 项公共目录/技能过滤；二次新增 3 项正常 Git 搜索排序/分页/UTF-16 列号/隐藏和 ignore（真实 rg、禁用 rg 两分支）及本人合法技能链接，当前 14 项均通过。 |
| [test_auxiliary_regressions.py](../../../../tests/req002/test_auxiliary_regressions.py) | 2 项 Office 外部图片引用历史回归，关联 R5-001。保留原代码、断言及初审失败证据；用户已将该场景排除，本次不运行，不作为验收阻碍。 |
| [test_file_auxiliary.py](../../../../tests/req002/test_file_auxiliary.py) | 二次追加 2 项本人中文文件/目录归档用例，原因 500 失败；v1.3 保持原断言独立复验通过，R5-002 已关闭。 |

R4 v1.3 另新增 [test_archive_download.py](../../../../tests/req002/test_archive_download.py) 四项标准下载头/ZIP 回归及 [integration_archive.py](../../../../tests/req002/integration_archive.py)。本次 R5 v1.3 没有修改业务或测试代码，仅记录独立复验及客户端兼容性观察。

本交接、[初审命令](evidence/validation-r5.md)、[二次审查命令与边界](evidence/validation-r5-second.md)、[用户范围决定](acceptance-scope.md) 和 `evidence/r5-*` 为记录。依用户直接指令在需求和设计中追加例外引用，没有修改其他契约或业务代码；既有测试未添加 skip/xfail，未执行 Git 提交。

## 验证证据

以下原始日志为 v1.0 初审的实际执行记录。v1.1 仅登记 R4 回复，没有重测；v1.2 的新执行与范围排除在下方“二次审查”单独记录，不改写历史结果。

- R4 最终快照的 54 个文件全部相符：[比较](evidence/r5-r4-source-comparison.json)。部署中 8 个相关业务模块与工作区按 LF 规范化后的 SHA256 全部相符：[运行源码比较](evidence/r5-runtime-source-comparison.json)。
- 当前源码重建独立测试镜像，原套件实际重跑 **663 passed、1 warning**：[构建](evidence/r5-build-tests-start.txt)、[测试](evidence/r5-pytest-baseline.txt)。包含真实 UID1000 服务、LibreOffice、提取及临时文件清理。
- 真实不同文件系统的移动/删除探针通过：[r5-cross-device.txt](evidence/r5-cross-device.txt)。
- 实际 WebUI v0.12.3 双普通账号系统连接复验通过：[r5-webui-integration.txt](evidence/r5-webui-integration.txt)。使用独立 `req002-r5-20261030` 目录和聊天保留原 R4 资料。该旧脚本末尾静态输出的 LLM 未验证/历史用户决定不作为本轮判据，真实 LLM 另行执行并记录。
- 新增路径/搜索/技能定向用例 **18 passed、1 warning，退出 0**：[输出](evidence/r5-pytest-path-search.txt)。
- 新增 Office 专项 **2 failed、1 warning，退出 1**：[定向输出](evidence/r5-pytest-auxiliary.txt)。两次失败都因 PDF 内含 B 图片像素；正常内嵌图成功，实际服务 UID1000、B 源 UID/GID=10002、直接读取 403。独立以 `--user user` 启动容器的同一场景也复现：[原始输出](evidence/r5-office-external-reference-service.txt)。
- 最终当前源码及全部新增测试联合运行：**681 passed、2 failed、1 warning，55.65 秒，退出 1**，共 683 项（REQ-002 294 项、REQ-001 389 项），无 skip/xfail。仅上述两项失败，原 663 项保持通过：[最终输出](evidence/r5-pytest-final.txt)、[最终构建](evidence/r5-build-tests-final.txt)。`sudo` 主机名解析提示未阻止实际转换，失败原因为泄漏断言；既有 Starlette/httpx warning 单独保留。
- 真实模型 `qwen3.8-flash-next` 经 WebUI 普通 A/B 会话完成 **8 场景、35 次实际工具调用**，全部场景断言通过：28 completed、7 failed（5 次预期 403、1 次他人技能不可见、1 次技能资源工具能力限制后自行改用 read_file）。验证 call_id、工具结果后回答、保存聊天 output、Socket.IO 展示事件、精确文件内容；无注入工具结果。[原始日志](evidence/r5-llm-run.jsonl)、[汇总](evidence/r5-llm-summary.json)、[独立文件/属主核对](evidence/r5-llm-files-final.json)。这些不计入 pytest 数量。
- 本轮开始时的 61 个生产/测试/需求/设计输入全部保持：[输入保留](evidence/r5-input-preservation.json)。[最终源码快照](evidence/r5-source-final.json)、[Git 检查](evidence/r5-diff-check.json)；`git diff --check` 退出 0。

### 二次审查（v1.2 实际执行）

- 与初审快照相比，二次审查开始时只有 R4 交接文档变化，生产代码与测试一致：[输入比较](evidence/r5-second-input-comparison.json)。审查结束时 27 个生产及用户既有文件摘要仍一致：[保留核对](evidence/r5-second-preservation.json)、[本轮源码/测试快照](evidence/r5-second-source-final.json)。
- Windows 追加 5 项测试后重建一次性镜像 `webui-lab/open-terminal:req002-r5-second-tests` / `sha256:4f0511bfba67e0f6cbf63997c27d31ee9f51c929dc132835384f89c45e412c50`：[构建](evidence/r5-second-build-final.txt)。
- 按用户范围使用 `--ignore=tests/req002/test_auxiliary_regressions.py`，**未运行两项 Office 内部引用测试**。实际执行其余 REQ-002 与 REQ-001 共 **686 项：684 passed、2 failed、1 warning，52.85 秒，退出 1**，无运行期 skip/xfail。此次两失败均为新发现的中文归档 R5-002，不能与初审 Office 的两失败混淆。[联合输出](evidence/r5-second-pytest-final.txt)。
- 中文单文件/目录归档定向两项均失败：[HTTP 证据](evidence/r5-second-archive-unicode.txt)；[实际堆栈](evidence/r5-second-archive-trace-final.txt) 为响应头 Latin-1 编码触发 `UnicodeEncodeError`。原 HEAD 亦有该表达式：[基线行](evidence/r5-second-archive-baseline.txt)，属于当前验收范围的既有问题，不声称由 R4 引入。
- 二次核对路径/cwd/别名/配置、move/delete/跨设备/公共只读等未发现新问题；正常 Git 搜索/技能的 3 项补测通过。实际 UID1000 常规 Office、提取与清理仍由联合套件执行通过。
- Git 间接布局回退时忽略规则变化与 HEAD 对照见 [兼容性记录](evidence/r5-second-git-review.md)，仅列非阻碍建议。二次没有重跑 WebUI/真实 LLM，初审 8 场景结果作为同一生产实现的已有证据保留。

### R5-002 修复复验（v1.3 实际执行）

- 当前生产修复仅归档响应构造一行，使用框架 `filename` 参数生成合法响应头。上轮快照中仅 main.py 与 R4 交接变化，原测试及范围保持；另新增 R4 下载头测试/代理探针：[输入对照](evidence/r5-r5-002-input-comparison.json)、[本次源码/测试快照](evidence/r5-r5-002-source-final.json)。
- R5 从当前源码重新构建独立镜像 `webui-lab/open-terminal:req002-r5-r5-002-tests` / `sha256:34940b79193d8e1e8a6c35942a720485b5a0c79e5a730f50d6b426c459fe2b89`，按既定 `--ignore=tests/req002/test_auxiliary_regressions.py` 运行：**690 passed、1 warning，52.63 秒，退出 0**。原中文两项、新下载头四项及真实 UID1000/Office/清理回归均通过；无运行期 skip/xfail，Office 内部引用两项未收集执行。[构建](evidence/r5-r5-002-build.txt)、[实际输出](evidence/r5-r5-002-pytest-current.txt)。
- 实际部署 Terminal 为 `sha256:1510082c82ece0c5f818da711d0e596e9f981e3415ae9acc9c25dd7e6a144e74`，WebUI 仍固定 v0.12.3，两服务 healthy；当前 8 个相关模块摘要与工作区全部相符：[环境](evidence/r5-r5-002-images.txt)、[源码比较](evidence/r5-r5-002-runtime-comparison.json)。本 R5 没有重建业务服务或改部署。
- 实际重新执行 WebUI 普通 A/B 归档代理探针，中文文件/目录、引号、emoji 共 **8 场景 HTTP 200**，ZIP 条目/内容全部正确：[输出](evidence/r5-r5-002-webui-archive.json)。新增专用合成目录 `req002-r5-002-a8de1c89`，没有新 LLM 调用。
- **下载名证据纠正：** R4 两份新增测试和上述代理探针使用标准邮件头解析器，输出的 `download_name` 证明标准解码正确，不能代表固定 WebUI 的最终保存名。R5 以实际响应加 Node 执行固定客户端提取表达式，确认 Unicode/空格等 `filename*=` 在该 UI 回退成 `download.zip`；作为 R5-S02 / P3 非阻碍记录，不把它夸大为归档内容失败。[当前/修复前对照](evidence/validation-r5-r5-002.md)。

### 环境与来源区分

- 当前运行 Terminal 镜像：`sha256:27e758343a7ac295391ad9ce095b692f4709d8a32fa35853c45f748a86a25804`，包版本 0.14.5；WebUI 为 v0.12.3 / `sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def`。两服务 healthy，启动时间保持：[环境](evidence/r5-images.txt)。
- 最终独立测试镜像 `webui-lab/open-terminal:req002-r5-tests`：`sha256:9c9dd690fcaf84b30207c385538a2143f6da9a307e7e1474f9a03ca696dccaac`，不挂部署卷/宿主源码、不发布端口，运行禁用网络。
- API/LLM 联调复用原普通测试账号，新增专用合成文件、技能与保存聊天供复验。前后 5 个账号绑定、UID/GID/home 与原标记哈希保持，Notebook 仍关闭：[保留核对](evidence/r5-runtime-preservation.json)。
- R4 的 663 项历史测试、真实模型记录为输入证据；本节标注的 R5 日志是实际重新执行。UI 编辑与右侧实时显示引用并已查看 [R4 UI 截图](evidence/r4-webui-a.png)、[R4 模型展示截图](evidence/r4-llm-display.png)，本轮未新拍浏览器截图，不能把 Socket.IO 事件称为新一次视觉验收。真实网络断线未另行抓包，清理证据为正常响应及受控 ASGI 故障/取消。

### AC 结论

| AC | 本轮结论 |
| --- | --- |
| AC-005 | 双普通账号同名文件操作、跨聊天、真实模型文件工具均通过。 |
| AC-006 | 直接文件入口的绝对/别名/`..`/软链拒绝及无写副作用通过，新增解析顺序用例通过；辅助依赖绕过单列 AC-008。 |
| AC-007 | 单文件/递归搜索、metadata、固定拒绝错误、公共目录按请求者过滤通过。 |
| AC-008 | **按已确认范围通过。** R5-002 中文单文件/目录归档 500 已修复，独立测试和真实代理验证通过，ZIP 名称/内容完整。Office 内部引用为用户范围例外；其他比较、静态、技能、主文档直接授权、常规转换与清理通过。默认 ZIP 保存名的客户端限制见非阻碍 R5-S02。 |
| AC-009 | 公共声明/启动校验、双账号只读、OS 可写公共根的产品拒写、公共链接按用户授权通过。 |

## 本轮决策

v1.0 初审结论为 `needs_fix`、接收方 R4，原因是 R5-001 已复现且影响 AC-008；18 项补漏测试和其他回归通过不抵消该缺陷。

v1.1 处置为 `needs_input`、接收方 R0。R4 v1.2 转交用户“不修复”决定后，R5 登记已知未修复，停止返修等待，但当时尚无正式范围调整。

v1.2 处置为 `needs_fix`、接收方 R4，仅由 R5-002 阻碍。用户此前已在本 R5 聊天直接决定放宽 Office 预览内部引用其他用户资料的限制并允许不测试，明确理由是避免影响工具和 Agent 能力。该正式范围调整已记录于 [acceptance-scope.md](acceptance-scope.md)，并在需求和设计追加引用；不再等待确认或要求修复 R5-001。

**v1.3 当前结论：result=passed、next_role=R0。** R5-002 的原 500 及 ZIP 内容要求已独立复验通过并关闭，当前约定范围的必要验证完成，没有未解决的验收阻碍。R5-S01（Git 回退忽略规则）和 R5-S02（固定客户端默认下载名）保留为非阻碍兼容性建议，不提高为未约定的新验收门槛。

R5-001 的原复现、历史 P1 和初审 **681 passed / 2 failed** 保留，按用户接受的范围例外解除阻碍，不改称技术修复通过。当前通过结论限于用户已确认的 REQ-002 范围，不把默认 ZIP 外壳文件名在固定客户端完全保真称作已验证通过。

Windows 负责编辑测试、范围记录和证据，WSL 仅构建、运行和测试。业务代码保持，Open WebUI 源码只读，没有重建业务容器或修改部署/数据卷。任意用户代码/命令对抗隔离、恶意并发换链、端口预览、个人依赖、完整升级持久性和 Notebook 功能未扩入本次审查。

## R4 修订汇总

### R5-001 Office 预览外部图片依赖（原 P1，已接受范围例外）

- 状态：**用户接受的范围例外，不再阻碍本轮验收**；已知行为未改动，原 P1 作为历史保留，不按“已修复”或“复验通过”关闭。
- 关联需求/依据：REQ-002 / AC-008；T-004/T-005；[file-access.md](../../../design/file-access.md) 的“服务进程能读取文件，不代表产品请求有权读取”和“辅助处理不得绕过请求用户路径与归属检查”。
- 位置：[main.py:797](../../../../open_terminal/main.py) 的 `view_file` 仅对主 DOCX 调用 `require_path`，随后 `_preview_pdf`（162～175 行）以服务身份启动 LibreOffice，未把文档内被解引用的文件纳入请求者授权。
- 触发：A 上传/保存自己的合法 DOCX，图片关系使用 `r:link`、`TargetMode="External"`，引用 `file:///home/<B>/private-photo.png`；调用 `GET /files/view?path=<A 的 DOCX>&preview=true`。不需要脚本、宏、用户命令或并发换链，故不属于 D01 的排除项。
- 初审预期（历史，现已放宽此项）：B 图片不可经 A 预览响应读取，转换应拒绝未授权依赖。当前按用户决定不测试该引用场景；主文档直接授权和常规 Office 功能要求保持。
- 实际：A 直接 view B 图片为固定 **403**；本人 DOCX 预览为 **200/PDF**，提取 PDF 图像得到 B 合成图特有像素 `(243,17,99)`。真实服务 UID1000、B 文件属主 UID/GID10002、服务拥有 B 组；正常内嵌白图转换通过。B 源图没有改变，发生的是内容泄漏。
- 历史复现：[Office 专项记录](evidence/r5-office-external-reference.md) 和 [初审失败证据](evidence/r5-pytest-auxiliary.txt)。两项原断言反映当时契约，源码保留；本次按用户决定排除该文件，不重跑、不弱化、不把旧失败改为通过。
- 影响：普通账号可通过产品自有转换器读取其他用户的图片；测试明确证明该 DOCX 形式。PPTX 及其他外部引用形式本轮没有逐一证明可利用；此限制保持，不再据此启动额外测试。
- 原处理请求（v1.0 历史，已停止推进）：R4 将 Office 转换的外部资源读取纳入请求授权，阻止未审核依赖自行读取，保留正常预览和清理。仅依赖 OS 权限或更换 worker UID 不能替代产品路径策略；该技术建议保留作为历史，不再作为当前返修任务。
- R4 回复：v1.2 认可缺陷事实和 AC-008 依据，并转交用户原话“回复R5，不改这个问题了”。据 R4 记录，此前仅讨论方案，没有实施；生产代码仍与 R5 开始快照一致，拟议的 `office_preview.py`、`test_office_dependencies.py` 未创建，没有构建或部署修复。这些是本次读取的 R4 回复，不冒充 R5 新一次源码/环境核验。
- R5 最新登记：2026-10-30 用户在本聊天正式放宽 Office 内部引用验收限制，按 [范围决定](acceptance-scope.md) 解除该项验收阻碍；保留历史证据和源码，不要求 R4 修复，不增加预览/工具/Agent 限制。无需再次确认此例外。

### R5-002 [P2] 单个中文文件或目录归档返回 500（已修复，复验通过）

- 状态：**已关闭：R4 v1.3 修复，R5 v1.3 独立复验通过**。
- 关联：REQ-002 / AC-008 的本人归档可用性，T-004/T-005；正常文件名未约定仅允许 ASCII，与 Office 内部引用例外无关。
- 位置：[main.py:1439](../../../../open_terminal/main.py) 的归档下载响应构造。`archive_name` 直接插入 `Content-Disposition`，中文不符合 Starlette 的 Latin-1 原始响应头编码。
- 复现：在本人 home 创建 `项目说明.txt`，或目录 `项目资料` 下创建 `说明.txt`；分别以该文件/目录为唯一 `paths` 项调用 `POST /files/archive`。无需其他用户资料、Office、软链或命令执行。
- 预期：HTTP 200，返回 ZIP，条目名称和文件内容保持；正常中文文件应可通过现有工具归档下载。
- 原实际（历史）：两种输入都返回 **500 Internal Server Error**，堆栈显示下载响应头触发 `UnicodeEncodeError`。[原定向证据](evidence/r5-second-archive-unicode.txt)、[原异常堆栈](evidence/r5-second-archive-trace-final.txt)、[原联合结果](evidence/r5-second-pytest-final.txt)。当前已恢复 HTTP 200。
- 测试：[test_file_auxiliary.py](../../../../tests/req002/test_file_auxiliary.py) 的 `test_archive_supports_owned_unicode_file_and_directory_names[file/directory]`，原 200/ZIP 内容断言保持，两项现已通过。
- 基线与影响：原 HEAD 同样存在该响应头表达式，属于本轮范围中的既有缺陷，不声称 R4 新改动引入；影响单独下载中文文件或中文目录的归档功能。一般 ASCII 归档通过，未推断所有下载失效。
- R4 回复：v1.3 将归档响应改为 `TemporaryFileResponse(..., filename=f"{archive_name}.zip")`；仅此生产变更，由框架进行合法编码，未扩大 Office 或 Agent 限制。新增标准下载头测试及双账号代理验证。
- R5 复验：从当前源码独立构建，**690 passed**；实际部署 8 模块一致，双普通账号 **8 个归档场景通过**，确认中文 500 已修复和 ZIP 内容完整。[完整记录](evidence/validation-r5-r5-002.md)。标准头解码与实际 UI 默认保存名的差异单列 R5-S02，不冒充本问题未修复，也不忽略兼容性限制。

## 非阻碍兼容性建议

R5-S01：全部元数据位于本人 home 的 `git init --separate-git-dir`，以及本人无害 `include.path` 配置，也会触发安全回退，导致 `.gitignore` 中的文件进入搜索结果。已与 HEAD 同条件对照；普通 Git 布局保持原行为，正常排序/分页/技能新增测试通过。[证据与命令](evidence/r5-second-git-review.md)。考虑现有设计/R4 已允许这类间接控制 fallback，且未逐项约定其 ignore 语义，本项仅供 R4 后续改善正常项目兼容性，**不作为验收阻碍或必修项**，没有追加争议失败用例。

R5-S02 / P3：固定 WebUI v0.12.3 的 `archiveFromTerminal` 只提取 `filename=`，不识别框架为 Unicode/空格/引号生成的 `filename*=`；因此 UI 保存名回退 `download.zip`。实际前后对照中 `project notes` 从 `project notes.zip` 变为 `download.zip`，中文则从 500 变为成功下载但默认名为 `download.zip`。ZIP 条目和内容保持，原 AC-008/R5-002 未将 ZIP 外壳默认名保真列为独立门槛，故 **P3 非阻碍**。R4 的“客户端仍原名”结论应限缩为“标准头解析器解码原名”；后续可考虑兼容旧 `filename=` 回退，保持常规 ASCII 名称。详见 [客户端对照和判定](evidence/validation-r5-r5-002.md)。Open WebUI 源码未改，不新增争议失败验收用例。

## 下一步

**接收角色：用户/R0。处理结论：REQ-002 按用户确认范围验收通过，R5-002 已关闭，result=passed。** 可由用户/R0 记录阶段完成并决定后续工作；R5-S01/R5-S02 供后续兼容改进，不要求继续返修。本轮未自动向其他角色聊天发送消息或推进下一需求。

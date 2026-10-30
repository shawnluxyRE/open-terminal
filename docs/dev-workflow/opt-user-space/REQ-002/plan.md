# opt-user-space / REQ-002 概要开发计划

- 项目：`D:\Documents\synowave\WebUI\open-terminal`
- 工作目录：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- 需求目录：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-002`
- 修订与状态：v1.0，2026-10-30（Asia/Shanghai）；stage=`R3`，result=`ready`，next_role=`R4`。表示规划具备开发交接条件，业务实现与验收尚未完成。
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-002 / AC-005～009；共同约束 D01～D06、D08～D10。
- 设计依据：[file-access.md](../../../design/file-access.md) v1.0、[handoff-r2.md](handoff-r2.md) v1.0；依赖已完成的 REQ-001 身份/home，见 [workspace-identity.md](../../../design/workspace-identity.md) 与 [REQ-001 R5 交接](../REQ-001/handoff-r5.md) v1.3。
- 接口与测试依据：[file_access.py](../../../../open_terminal/utils/file_access.py)、[fs.py](../../../../open_terminal/utils/fs.py) 的新增契约，以及 [tests/req002](../../../../tests/req002) 五个测试模块、fixture 和 Dockerfile；保留 [tests/req001](../../../../tests/req001) 回归。
- 规划读取基线：分支 `codex/opt-req002`，HEAD `59eac68b4c0314ab633c376de82a539af3c98d52`，Open Terminal 0.14.5；包括 R2 尚未提交的设计、接口与测试。12 个交付文件与 [R2 摘要](evidence/r2-source-snapshot.json) 全部一致。

## 范围与完成条件

本计划只覆盖 REQ-002 的产品文件访问及辅助处理，沿用 R2 的权限、配置和错误契约。身份、唯一 home、UID/GID 与账号准备复用 REQ-001；不重新设计账号体系。具体路径解析算法、私有函数、逐文件实现步骤和 subagents 分工由 R4 确定。

完成条件为：AC-005～009 均有正反向证据；新增契约测试通过且 REQ-001 回归保持；真实服务权限和 WebUI v0.12.3 双普通账号链路完成相应验证。218 项测试全绿不能单独替代真实联调。管理员通过产品入口同样遵守归属规则，普通个人资料不得因服务进程可读而变为公共资料。

本次预览范围是文件/Office 预览与静态文件服务；用户自建服务的端口归属继续由 REQ-004 处理。个人依赖环境、完整卷重建/升级持久性和整体接入验收分别由 REQ-005、REQ-007、REQ-008 负责，可引用本次证据。Notebook 保留并关闭，不纳入功能验收；不扩展到任意命令/代码对抗隔离、恶意并发换链、资源配额或历史迁移。Open WebUI 源码只读。

## 主要任务

前置依赖只列启动所需条件。T-001～T-004 可按已确定接口并行开发，运行验证与集成次序见下一节。

| 任务编号与标题 | 交付结果、关联需求与验证依据 | 大致修改范围及设计依据 | 前置依赖 |
| --- | --- | --- | --- |
| T-001 统一授权策略与公共配置 | `FileAccessPolicy`、配置解析及真实启动接线可用；本人 home 读写、显式公共根只读、其他路径拒绝，软链各跳与最终目标遵守同一规则；非法配置在多用户启动时失败，单用户兼容。对应 AC-006/007/009；通过 `test_file_access_policy.py`、`test_public_config.py`，并在 HTTP 验证配置实际传入。 | `utils/file_access.py`、`env.py`、`dependencies.py`，现有管理员配置说明；依据设计“路径与权限契约”“公共只读资料配置”。保持 `UserFS` 的两个位置参数兼容，不引入新的配置或授权平台。 | 无；R2 接口与配置格式已确定。 |
| T-002 用户文件操作完整接线 | `UserFS` 读写、metadata、列表/遍历及基础文件 HTTP 入口均在 I/O 前授权；使用获准路径，写入口明确请求写权限，拒绝无副作用；保留 home 别名与聊天 cwd。移动/删除满足链接条目、home 根、已有目标冲突和目标父目录契约。对应 AC-005～007/009；通过 `test_userfs_contract.py` 与 `test_file_http.py` 的基础文件、上传、移动/删除和公共只读用例。 | `utils/fs.py`、`main.py` 的 cwd、list、display、write、mkdir、delete、move、replace、upload 及统一错误处理；依据设计“路径与权限契约”和逐入口矩阵。 | 无；可按 `require_path`/策略契约开发；完整验证需 T-001 接入。 |
| T-003 搜索与遍历结果一致授权 | grep 单文件与递归、glob、matches/search 的显式起点在探测前校验；候选项在读取、metadata 和返回前过滤，不泄露非法名称或内容。Git、rg、fallback 保持相同权限结果与既有限制。对应 AC-005～007/009；通过 `test_file_http.py` 的起点/递归/Git 用例及 `test_userfs_contract.py` 的 fallback 用例。 | `main.py` 的四类搜索路由、`utils/search.py`，复用 T-002 的列表/遍历接口；依据设计“现有入口接入与边界”。 | 无；按已定的 `UserFS` 授权接口开发；完整验证需 T-001/T-002。 |
| T-004 辅助处理与技能访问闭环 | 读取/下载、Office 提取与预览、静态服务、比较、归档和技能发现/资源读取均保留正常能力并拒绝越权；比较两输入、归档显式全列表先完成授权；枚举过滤非法项，技能过滤不影响合法项。服务临时产物保持请求内响应与清理。对应 AC-006～009；通过 `test_file_auxiliary.py`、HTTP 归档递归与下载用例。 | `main.py` 的 read/view/serve/compare/archive/skills，`utils/file_compare.py`、`utils/skills.py` 及相关响应/worker 接入；依据设计逐入口矩阵与辅助产物约束。 | 无；接口足以并行开发；完整验证需 T-001/T-002，相关搜索/遍历能力集成后共同回归。 |
| T-005 容器回归与补充契约验证 | 当前 REQ-002、REQ-001 套件通过；补齐非 root 服务实际 I/O、链接目录修改/跨文件系统移动、直接 cwd 授权、真实转换及取消清理、错误语义与单用户兼容等证据。记录源码、镜像、命令、结果及未覆盖项。对应 AC-005～009 与既有身份回归。 | `tests/req002`、必要测试运行配置、现有回归及本需求 `evidence/`；按下文验证矩阵补测，既有断言不弱化。 | T-001～T-004 已集成后完成验证；用例和测试环境可提前准备。 |
| T-006 WebUI 双账号联调与交付 | 当前源码重建的 Terminal 与固定 WebUI v0.12.3 下，A/B 普通账号经管理员系统连接完成文件、AI 调用、预览/静态、技能和公共只读正反向验证；逐条给出五项 AC 的结论，形成 `handoff-r4.md`。 | 现有联调环境、必要 Open Terminal 接入修复、配置说明及本需求证据；不修改 Open WebUI。 | T-005 容器验证通过；可用系统连接、A/B 普通账号、公共资料目录与实际重建镜像。环境准备可提前。 |

## 开发顺序与并行条件

1. R4 开始先核对工作区与 R2 输入，保留已有未提交修改。T-001 的策略/配置和 T-002～T-004 的调用点可依据固定接口并行；建议先集成 T-001 与 T-002，形成本人读写、公共只读和越权拒绝的完整文件链路，再合入搜索、辅助与技能入口。
2. `main.py` 是 T-002～T-004 的共同修改点，`fs.py` 的授权、遍历与临时响应也被多项任务复用。R4 统一安排写入与集成边界；每个入口只使用同一套策略，不各自重写权限判断。错误转译在共同入口统一核对，避免 `PermissionError` 被 `OSError`/通用 catch 转成含路径的 400 或回退到原文件。
3. 接线复核以 [设计逐入口矩阵](../../../design/file-access.md#现有入口接入与边界) 为清单，覆盖直接 `open/stat`、上传流、压缩、比较/提取 worker 和流式响应。只修改 `UserFS._check_path` 不能视为全部入口完成；`resolve_path` 不得提前抹掉软链后的 `..`，普通 I/O 使用授权结果，移动/删除另保持最终链接条目语义。
4. T-005 的场景和环境准备可与实现并行。各交付先运行相关定向用例；全部实现集成后，在同一份重建源码上运行 REQ-002 和 REQ-001 完整回归，再验证标准镜像的服务 UID1000、真实转换与清理，最后执行 T-006 上游联调。新修复须重新构建对应镜像并验证受影响路径。
5. 补验失败先区分业务缺陷、设计/测试矛盾与环境不足。业务缺陷由 R4 修复；需修改设计/测试契约时保留问题编号交 R2，规划调整交 R3，范围或验收变化交用户决定。缺失证据须标明受影响 AC 与恢复条件，不以健康检查或历史结果代替。

## 验证安排

### 基线与环境

本轮 R3 做静态核对与交付摘要核验，未重新运行测试或部署服务。引用 R2 最终记录：REQ-002 **218 项，207 failed、11 passed、1 warning**，无收集/fixture 环境错误；其中 91 项为未实现骨架、17 项为配置未接线、99 项为既有行为差距。11 项已通过能力须保留。既有 REQ-001 记录为 **389 passed、1 warning**，来自 R2 较早构建，不能冒充最终合并后的回归。详见 [最终 REQ-002 输出](evidence/r2-pytest-req002-final.txt)、[REQ-001 输出](evidence/r2-pytest-req001.txt) 与 [R2 运行说明](handoff-r2.md#验证证据)。

源码、测试、文档、证据与 Git 操作均在 Windows 侧完成。WSL Ubuntu-24.04 仅用于构建、运行和测试；在 `~/projects/webui/open-terminal` 重新构建当前源码的独立测试镜像。沿用 [tests/req002/Dockerfile](../../../../tests/req002/Dockerfile) 与 [R2 命令](handoff-r2.md#实际运行环境与命令)，使用 R4 独立 tag，记录实际基底/镜像 ID、源码摘要、版本、命令和退出码。一次性 root 测试镜像不挂载部署卷或宿主源码、不发布端口，运行禁用网络，保留测试环境标记；不能以 root fixture 替代真实服务权限验证。

业务镜像也须重建并核实实际运行内容，版本仍为 0.14.5 不表示源码相同。联调固定 Open WebUI v0.12.3，仓库 `D:\Documents\synowave\WebUI\open-webui` 只读；部署目录为 `~/projects/deploy/webui`。保留 `webui-lab-open-webui-data` 与 `webui-lab-open-terminal-data`，含完整 `/home/.open-terminal` 状态。Notebook 保持关闭；若调整 `.env`，使用 `docker compose up -d` 加载新值。记录不得包含管理员密码、实例 key 或用户私有正文。

### 验收与补验矩阵

| 验收/契约 | 既有自动化依据 | R4 补充验证及完成判定 |
| --- | --- | --- |
| AC-005 本人文件与跨聊天复用 | `test_file_http.py` 的双用户同名上传/编辑/搜索/删除、别名、正常移动与下载。 | 真实 WebUI A/B 经文件界面及 AI 文件工具完成同名文件操作；A 另一个聊天继续读同一文件。核对非 root 服务实际读写、文件属主和 B 内容保持；管理员账号的产品请求同样受归属约束。 |
| AC-006 路径与写入拒绝 | `test_file_access_policy.py` 的入口/软链各跳/缺失尾部规则；HTTP 的绝对路径、别名、`link/..`、上传最终文件链接、home 根保护；`test_userfs_contract.py` 的直接调用。 | 补验直接 cwd 设置、指向 home 根的链接、目标父目录缺失及两端拒绝的无副作用；本人目录含越权链接时，递归删除不触碰外部目标，跨文件系统移动保持链接而不读取外部内容。本人已获准但不存在的目标仍遵守原接口错误语义。上游请求 B 路径/软链被拒绝且 B 文件保持。 |
| AC-007 搜索、metadata 与错误 | HTTP 起点/递归/Git、UserFS metadata/walk/fallback 及纯策略拒绝用例。 | 在实际可用的 Git、rg 与 fallback 分支核对结果一致；覆盖公共起点、非法链接、单文件搜索和不存在的越权路径。显式越权统一 403 `Access denied`，结果/错误不泄露非法项名称、路径或内容，公共项 `writable=false`；区分请求结构 422 与授权结果。 |
| AC-008 辅助与技能 | `test_file_auxiliary.py`：真实比较、DOCX 提取、ZIP、受控预览转换、静态资源与技能过滤；HTTP 归档递归用例。 | 使用实际服务账号运行真实 LibreOffice，验证获准文件可预览、非法输入在处理器启动前拒绝且转换失败不绕过授权；正常响应、失败与请求中断后临时 JSON/PDF/ZIP 均清理且不能被另一用户请求。经 WebUI 验证本人预览/静态/技能资源可用、他人无权访问；归档任一显式输入非法时无部分输出。 |
| AC-009 公共只读与个人资料不公开 | 策略与 `test_public_config.py` 的解析、优先级、保护根、启动失败、单用户忽略；HTTP/直接调用的公共读写和外部私有文件拒绝。 | 真实管理员配置/挂载独立公共目录后，A/B 均可读，所有修改类入口拒绝；在 OS 可写的受控目录中验证产品只读独立成立。公共链接仍按请求者授权；普通个人文件和服务临时产物不自动公开。配置说明包含默认值、格式、环境优先级和重载方式。 |
| 共享接口兼容与身份回归 | `tests/req001`、REQ-002 单用户入口/启动配置用例。 | 完整套件通过，错误 key/缺身份仍先于资源操作；`UserFS` 构造、成功响应、聊天 cwd 分组、搜索限制、正常命令/终端使用本人 cwd 保持。补验显式单用户完整 app 启动与代表性文件请求，不启用多用户公共限制；Notebook 能力仍关闭。 |

补测围绕已有需求/设计的外部行为组织，不固定私有算法；不通过 skip、xfail、弱化断言或模拟业务授权结果达成通过。真实服务 UID、真实转换和上游账号的证据分别记录，避免将隔离 fixture 的结果混用。

### 交付记录

R4 在本需求 `evidence/` 与 `handoff-r4.md` 记录 AC-005～009 的证据和结论，区分自动化、真实运行、受控模拟与未覆盖项；注明公共配置/挂载、身份、服务权限及版本条件。保留 R2 历史日志。涉及 REQ-001/004/005/007/008 的结果只引用本目录记录，不复制独立结论。R5 据此独立验收。

## 修订记录

| 修订 | 日期 | 内容 |
| --- | --- | --- |
| v1.0 | 2026-10-30 | 根据需求 v1.10、R2 设计/交接 v1.0 及当前接口/测试制定六项概要任务；明确共同文件修改、红基线、真实权限/辅助处理/双账号补验及其他需求边界。未发现阻塞开发的设计矛盾，无待确认业务决定。 |

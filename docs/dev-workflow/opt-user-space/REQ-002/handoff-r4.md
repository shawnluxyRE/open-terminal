# opt-user-space / REQ-002 R4 交接

- stage：R4
- result：ready
- 修订：v1.3，2026-10-30（Asia/Shanghai）。R5-002 中文归档已修复并验证，交 R5 复验；R5-001 按用户正式范围决定排除，不再返修或测试。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-002`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-002 / AC-005～009。
- 计划依据：[plan.md](plan.md) v1.0；[file-access.md](../../../design/file-access.md) v1.0。
- 当前验收范围另含用户确认的 [acceptance-scope.md](acceptance-scope.md)，Office 内部引用场景不纳入本轮。
- 源码基线：`codex/opt-req002` / `59eac68b4c0314ab633c376de82a539af3c98d52`，Open Terminal 0.14.5；本轮未提交 Git。
- next_role：R5

## 产物

| 任务 | 完成结果与文件 |
| --- | --- |
| T-001 | [file_access.py](../../../../open_terminal/utils/file_access.py) 实现逐组件/软链各跳授权及公共根解析；[env.py](../../../../open_terminal/env.py) 启动校验、[dependencies.py](../../../../open_terminal/dependencies.py) 接线；[README.md](../../../../README.md) 说明默认值、格式、环境优先级与重载方式。 |
| T-002 | [fs.py](../../../../open_terminal/utils/fs.py) 统一实际 I/O 授权、metadata/遍历过滤、显式写权限、链接条目操作、home 保护及移动预检；[main.py](../../../../open_terminal/main.py) 接入基础文件、上传、cwd 和固定 403。 |
| T-003 | main.py 起点/候选先授权；[search.py](../../../../open_terminal/utils/search.py) 保留 Git/rg/fallback，不递归目录链接，检查内容及辅助控制文件，使用获准 canonical I/O 路径。 |
| T-004 | main.py 接入读取/Office 预览/静态/归档/技能；[file_compare.py](../../../../open_terminal/utils/file_compare.py) 两输入先授权与 worker 权限错误脱敏；[skills.py](../../../../open_terminal/utils/skills.py) 过滤非法资源。既有临时响应清理实现经正常/失败/取消补验通过。 |
| T-005 | v1.0 原 218 项契约加 56 项为 REQ-002 274 项；R5 补测及 v1.3 新增下载头 4 项后，按当前范围实际执行 REQ-002 301 项 + REQ-001 389 项，合计 690 passed。Office 内部引用两项显式排除，原断言保持。测试见 [tests/req002](../../../../tests/req002)。 |
| T-006 | 当前源码重建 Terminal，固定 WebUI v0.12.3 双普通账号经系统连接完成文件、公共只读、预览/静态、归档/比较、技能联调；A 另有界面编辑保存证据。[API 联调](../../../../tests/req002/integration_webui.py)。v1.1 新增 [真实 LLM 联调](../../../../tests/req002/integration_llm.py)，8 场景通过，原 AI 未验证项关闭。 |

主线程维护共享 main.py、集成、部署和本交接；三个 subagents 分别负责策略/配置、UserFS、搜索/辅助及非 root 补测。同一文件只由指定代理修改。全部开发前检查通过后才实施；未修改 R2 公共接口、需求/设计或 Open WebUI 源码。

## 验证证据

| 实际验证 | 结果与证据 |
| --- | --- |
| 最终联合套件 | **663 passed，1 warning，50.44 秒，退出 0**，无 skip/xfail。[输出](evidence/r4-pytest-final.txt)、[构建](evidence/r4-build-tests-final.txt)。warning 为既有 Starlette TestClient/httpx 弃用提示。 |
| 真实服务权限/Office | 真实 UID1000 python3-ot、A/B 账号与文件属主、OS 可写公共根仍被 API 拒写、真实 DOCX 提取/LibreOffice PDF 均通过；最终套件包含。较早独立详细 [输出](evidence/r4-runtime-pytest.txt) 仅该历史输出使用 rf_，最终套件已统一 rt_。 |
| 临时产物清理 | 正常 JSON/TXT/PDF/ZIP 响应清理；受控 ASGI 发送错误/取消、旧协议 receive 断连及提取/JSON/ZIP 构建失败/取消均通过。没有把故障注入称作真实客户端网络断线测试。 |
| 真实跨设备 | 一次性容器内 home 下挂 tmpfs，实际 st_dev 不同；移动目录/最终目录链接、保留外部链接、外部目标不变、读取拒绝与递归删除不跟随均通过。[结果](evidence/r4-cross-device.txt)。 |
| 搜索三分支 | 测试镜像真实 rg 14.1.1、真实 Git、工具不可用 fallback，以及非法 Git/ignore 控制文件均通过。部署原标准环境没有 rg，实际联调使用既有 fallback，没有声称部署执行了 rg。 |
| WebUI 双账号 | [输出](evidence/r4-webui-integration.txt)：同名 CRUD/搜索、跨聊天、越权/软链拒绝、管理员同样受限、公共根全部修改入口拒绝、真实 Office/静态/归档/比较/技能均通过；数据库系统连接与环境 key/url 一致，凭据不写日志。 |
| 界面 | 普通 A 文件面板打开并编辑 same.txt 后保存，显示 edited A UI。[截图](evidence/r4-webui-a.png)；[复核](evidence/r4-webui-ui-recheck.json) 确认 A 第二聊天可读 UI 修改，B 同名文件不变。B 本轮用真实代理 API 验证，未另拍界面。 |
| 源码与保留 | [开始摘要](evidence/r4-source-start.json)、[最终摘要](evidence/r4-source-final.json)、[运行包八模块比较](evidence/r4-runtime-source-comparison.json)、[输入保留](evidence/r4-input-preservation.json)。原 R2 五份测试、fixture 和用户已有 AGENTS.md 修改保持；Dockerfile 仅完善测试环境。`git diff --check` 通过。 |

### 运行环境与保留数据

- 当前运行镜像 `webui-lab/open-terminal:req002-r4` / `:local`：`sha256:27e758343a7ac295391ad9ce095b692f4709d8a32fa35853c45f748a86a25804`。
- 最终测试镜像 `webui-lab/open-terminal:req002-r4-tests`：`sha256:bc13b86f1f8372b372d08452c310aaf77352adce8cfd1db546c7bca6718287ff`。
- WebUI v0.12.3 镜像仍为 `sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def`，容器 ID/启动时间未变。两服务 healthy，Notebook 关闭。[镜像](evidence/r4-images-final.txt)、[部署前](evidence/r4-containers-before.txt)、[部署后](evidence/r4-containers-after.txt)。
- 运行构建复用既有标准环境及 [Dockerfile.runtime](../../../../tests/req001/Dockerfile.runtime)，重新安装本轮包；没有重做系统工具升级。原镜像 `sha256:d30964f7047ed9b6375b7f1d5538fb5810d25467c695111e132a79e6ba57c1ab` 保留为 `webui-lab/open-terminal:req002-r4-base`。
- Windows 通过 `\\wsl.localhost\Ubuntu-24.04\home\shawn\projects\deploy\webui` 新建 compose.override.yaml 和 req002-reference/manual.txt，设置 `OPEN_TERMINAL_PUBLIC_READ_ROOTS=["/opt/req002-reference"]`，只读挂载 `./req002-reference:/opt/req002-reference:ro`。原 compose.yaml/.env 未改；后续默认 Compose 自动加载 override。[部署](evidence/r4-deploy.txt)、[挂载](evidence/r4-mounts-final.txt)。
- 原两个命名持久卷保留。重建后准备已有绑定恢复容器账号；[前快照](evidence/r4-runtime-before.json)、[后快照](evidence/r4-runtime-after.json)、[比较](evidence/r4-runtime-preservation.json) 证明原四绑定、UID/GID/home 及 REQ-001 标记哈希不变。新增一个管理员工作空间来自管理员产品归属测试，没有创建新的 WebUI 账号。
- 复用 R4 A/B 普通账号，新增四个 REQ002 保存聊天；两用户 req002-r4 目录、A 的 `.agents/skills/req002-r4-demo`、公共资料保留供 R5 复核。测试容器均 `--rm`；镜像和证据保留。

### AC 结论

| AC | 当前结论 |
| --- | --- |
| AC-005 | 本人文件、跨聊天和双账号同名操作通过，A 界面编辑保存及 B 不变有实际证据；v1.1 真实模型创建/编辑/读取/搜索、双账号同名与跨聊天/cwd 补验通过，原 LLM 缺口关闭。 |
| AC-006 | 路径/软链各跳/link/../别名/写权限/home 根/移动两端/父目录/冲突通过；跨设备与递归删除不操作外部目标。 |
| AC-007 | 显式越权固定 403 Access denied，先于存在性/内容；搜索/枚举隐藏非法名称及 metadata；公共 writable=false；获准缺失路径保留原语义。 |
| AC-008 | 按 [acceptance-scope.md](acceptance-scope.md) 排除 Office 内部引用场景，R5-001 为用户接受的范围例外，不称修复。当前范围内常规提取/转换、静态、比较、技能与清理通过；R5-002 中文归档已在 v1.3 修复，定向、联合及真实双账号归档验证通过，待 R5 复验。 |
| AC-009 | 解析/保护根/启动失败/单用户忽略、公共链接按用户过滤通过；真实只读挂载双账号和独立 OS 可写目录中的产品拒写分别有证据。 |

复现命令见 [validation-r4.md](evidence/validation-r4.md)。

## 本轮决策

沿用统一授权及固定错误契约；Git/rg 的辅助控制文件也不能先读取越权内容再过滤输出。不安全控制使用授权遍历 fallback；rg 禁用树外 parent/global ignore，增加目录/ignore 控制预检，不新增全文件内容扫描或扫描 .git/objects。编辑、证据保存和 Git 检查均在 Windows；WSL 仅构建运行测试，Open WebUI 只读，没有跨角色聊天推进。

2026-10-30 初次实测普通账号 `/api/models` 返回空列表，用户当时允许保留 AI 未验证项。用户随后提供独立模型、填写 Key 并明确要求补测；v1.1 已执行并通过真实 LLM 链路，旧保留决定不再代表当前验证状态。未改 R2 接口或验收含义。

## R3 修订汇总

无待处理计划问题。T-006 模型调用缺口已按用户后续指令完成补验，请 R3/R5 采用 v1.1 结果。

## R2 修订汇总

### R4-TST-001 新增临时响应测试模拟错误（已修正，供 R2 知悉）

- 关联：T-005 / test_userfs_extended.py。[首次集成](evidence/r4-pytest-first.txt) 629 passed / 3 failed。
- 原因：新增测试省略 ASGI spec 且 receive 立即断连；Starlette 默认 2.0 在 body 发送前取消，未执行预期发送断言。
- 处理：发送错误/取消明确 ASGI 2.4，保留完整内容、异常类型及删除断言，另加旧协议 receive 断连用例。生产清理实现和原 R2 测试未改，最终全部通过。

### R4-TST-002 联合容器的服务配置/子进程源码（已修正，供 R2 知悉）

- 关联：T-005 / 新增 runtime_probe.py 与测试 Dockerfile。[联合首次补验](evidence/r4-pytest-joint-first.txt) 662 passed / 1 failed。
- 原因：新增探针 rf_ 与 REQ-001 rt_ 共用 registry，服务按设计拒绝配置不一致。
- 处理：新增探针沿用 rt_，保留随机独立身份和全部断言，不删除 registry/账号；测试镜像安装同一源码，避免 sudo 清理 PYTHONPATH 后误用旧基底包，并安装真实 rg。没有修改 REQ-001 或生产契约，最终 663 项通过。

## 下一步

### 2026-10-30 后续 LLM 连接配置

用户提供独立部署的 OpenAI 兼容服务，并要求 API Key 留空。已通过 WSL 中 WebUI 的管理员配置 API 持久化连接：Base URL `http://10.0.0.14:4000/v1`，手动模型 ID `qwen3.8-flash-next`，Key 为空；随后按用户明确选择开放给所有 WebUI 用户。管理员及两个普通测试账号均已确认模型可见，容器到服务 TCP 端口可达。[配置证据](evidence/r4-llm-connection.json)、[权限/可见性证据](evidence/r4-llm-model-visibility.json)。

用户可在 WebUI 管理员面板 → 设置 → 连接中，编辑此 OpenAI 兼容连接补填 API Key，保存立即生效。没有修改 .env 或全局持久配置策略，也没有重启容器。本版本已存配置优先于环境默认值，避免提供无效的 .env Key 占位。真实推理/AI 工具调用仍未验证；此后续配置不改变上文历史测试结论。

用户随后要求改为 deploy 配置文件管理。已在 Windows 侧写入 WSL 部署目录 `.env` 的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY=`，并在 compose.override.yaml 添加一次性 `llm-config` 服务及只读挂载的 `sync-llm.py`。配置同步通过管理员 API，保留其他连接和已有模型授权，以稳定 deployment_id 定位受管连接；不关闭全局持久配置。执行 `cd ~/projects/deploy/webui && docker compose run --rm --no-deps llm-config` 已退出 0，Key 仍为空、所有用户 read 权限保持：[实际输出](evidence/r4-llm-deploy-sync.txt)。以后此连接以 `.env` 为准，执行同步会覆盖管理界面对此连接的修改；无需重启两个业务容器。上述“仅管理界面填写”的建议由本段部署文件方式取代，历史验证状态保持未调用真实模型。

### v1.1 真实 LLM 补验（2026-10-30）

用户填好 Key 后再次同步成功，输出只记录 `api_key=set`：[同步](evidence/r4-llm-deploy-sync-key.txt)。普通账号通过 WebUI 请求 `qwen3.8-flash-next` 实际推理成功：[冒烟](evidence/r4-llm-smoke.txt)。测试只使用专门生成的账号测试文件和随机标记，不把真实个人资料发送给模型；凭据只在内存中使用。

本轮新增 [integration_llm.py](../../../../tests/req002/integration_llm.py)，不改生产代码、既有测试或 Open WebUI。实际普通用户 Socket.IO 会话配合 `stream=true`、保存的 chat/id 和 `terminal_id=open-terminal`，由 WebUI 执行模型产生的工具调用并把结果发回模型；没有注入 assistant/tool 响应。每个调用要求同 call_id 的真实结果；最终工具结果之后有模型回答，并与保存聊天中的 output 一致。路径/文件内容/属主再独立核对。

| 场景 | 实际结果 |
| --- | --- |
| A 文件流程 | 相对创建 same.txt、编辑、读取、精确 grep、返回只存在于文件中的 context 标记，展示事件成功；7 次调用。 |
| B 同名文件流程 | 同样的相对路径落在 B home，A/B 内容不同且属主正确；6 次调用。 |
| A 第二聊天 | 读取聊天1文件，相对读取/写入落在 alternate cwd，聊天1文件保持；4 次调用。 |
| 公共只读 | A 读取公共资料成功，实际写工具返回固定 403，文件不变；3 次调用。 |
| 私有路径/软链 | A 绝对读取 B、读取本人外链、写入 B 均实际 403，随后本人读取正常；4 次调用。 |
| 他人技能 | B 公共读取成功；A 技能 view_skill 返回 not found，资源读取实际 403，不返回隐藏资源标记；3 次调用。 |
| 本人技能 | A 实际 view_skill，读取资源，组合两枚未出现在用户提示里的随机标记并写入/读回；5 次调用。模型先误用 read_skill_file(data.txt)，收到仅支持 SKILL.md 的说明后自行改用正确 read_file，完整过程保留。 |
| 实时展示 | 模型实际 read_file + display_file，Socket.IO 路径与浏览器右侧展示内容一致；2 次调用。 |

**共 8 个场景通过，34 次真实工具调用：27 completed、7 failed。** failed 中 5 次为预期 403、1 次为预期技能 not found、1 次为上述技能工具能力限制及随后纠正。独立 subagent 复核保存证据和截图，确认所有场景满足判据；A 首轮后补的精确断言也对原输出核验满足。

证据：[A 流程](evidence/r4-llm-a-workflow.jsonl)、[其余六场景](evidence/r4-llm-remaining.jsonl)、[展示调用](evidence/r4-llm-display.jsonl)、[真实界面](evidence/r4-llm-display.png)、[汇总](evidence/r4-llm-summary.json)、[文件/属主复核](evidence/r4-llm-files-final.json)、[运行快照](evidence/r4-llm-runtime-after.json)。复现方法与边界见 [validation-llm-r4.md](evidence/validation-llm-r4.md)。

原生模型工具没有上传/删除/移动/cwd 设置接口，这些继续归原 API 验证；不声称由模型原生工具完成。上游在写入 403 时仍发刷新事件，本轮用实际错误及内容不变判定拒绝，不把事件当成功。模型在 cross-chat 回答中误猜了字节数/换行，实际工具和 API 内容精确匹配，没有追加换行；未依据模型自述替代断言。本轮没有重跑此前 663 项套件，原生产和测试源码摘要保持，新增真实模型证据独立计数。

新增测试第一次续跑按标题定位聊天失败（[记录](evidence/r4-llm-chat-lookup-first.txt)），原因是 WebUI 在首个用户消息后更新了空聊天标题；该次没有发起模型调用。已改用准备阶段得到的稳定 chat ID，不改变业务断言。三个新测试聊天、req002-llm/r4-20261030-8f2c 文件及 A/B 测试技能保留供 R5 复核。

## R5 返修回复

### R5-001 Office 预览外部依赖泄漏：用户决定不修复

- 接收依据：[handoff-r5.md](handoff-r5.md) v1.0 的 R5-001 / P1，关联 REQ-002 / AC-008。
- R4 核实：已阅读复现、原测试与代码，认可问题事实及其需求依据。此前只讨论了保守回退和合法依赖物化两种方案，没有实施。
- 用户决定：2026-10-30，在了解现有需求、改动范围和能力影响后，用户明确指示：**“回复R5，不改这个问题了”**。本项记为用户决定不修复（not fixed），停止后续实现、构建和部署。
- 当前状态：生产代码与 R5 开始审查的快照一致；拟议的 office_preview.py 与 test_office_dependencies.py 均未创建。原测试、R5 两个失败回归、原始证据和缺陷描述保持，不删除或改为 skip/xfail。本次没有运行新的测试或修改业务环境。
- 验证事实：引用 R5 实际联合结果 **681 passed、2 failed**，两失败对应此问题；不是本次重新运行的结果，也不作为修复通过证据。
- 处理边界：不把用户“不修复”的决定写成“缺陷已修复”或“技术验证通过”，不据此擅自修改 AC-008。R4 本轮仅更新交接并按用户授权回复对应 R5 聊天。
- 下一步请求：请 R5 在其交接中登记用户决定和已知未修复状态，基于该决定说明最终验收处置；不再等待 R4 提交本项修复。若需要正式调整需求或验收范围，保留待决记录交用户/R0，不自动推进其他角色。

上述 v1.2 回复之后，用户在 R5 聊天正式确认 [Office 验收范围例外](acceptance-scope.md)，R5 v1.2 已登记 R5-001 不再阻碍。本轮依该最新范围执行，不再等待确认或修复 R5-001，也不新增 Office/Agent 限制。

### R5-002 [P2] 中文文件/目录归档 500（R4 已修复，待 R5 复验）

- 接收依据：[handoff-r5.md](handoff-r5.md) v1.2；REQ-002 / AC-008，T-004/T-005。开发前由主线程和 subagent 核对原断言、框架实现、命名及清理契约，无需变更设计或计划。
- 原因与复现：归档响应将中文 archive_name 直接插入原始 Content-Disposition，Starlette 使用 Latin-1 编码时抛 UnicodeEncodeError。R4 在 R5 的独立镜像实际复现中文文件/目录 **2 failed**：[基线](evidence/r4-r5-002-baseline.txt)。这是原有表达式的问题，不归因于前次权限接线。
- 生产修复仅一行：[main.py](../../../../open_terminal/main.py) 的 archive_paths 改为 `TemporaryFileResponse(..., filename=f"{archive_name}.zip")`，由框架生成合法文件名响应头。普通 ASCII 保持原 quoted filename，Unicode/空格/引号使用 UTF-8 filename*，客户端解码后仍为原名。ZIP 条目、正文、授权、取消和临时清理代码未改变。
- 新增 [test_archive_download.py](../../../../tests/req002/test_archive_download.py) 四项：普通 ASCII、空格/双引号、中文/emoji 下载名，以及多输入 download.zip；以标准邮件头解析器解码后断言文件名，同时核对 ZIP 名称/内容。R5 原中文文件/目录两项不改。
- 定向验证 **26 passed、1 warning，4.21 秒，退出 0**：[输出](evidence/r4-r5-002-targeted.txt)。包括原辅助处理、中文归档、新下载头及真实服务/临时清理测试。
- 当前验收范围联合验证 **690 passed、1 warning，51.67 秒，退出 0**：[输出](evidence/r4-r5-002-pytest-final.txt)、[最终构建](evidence/r4-r5-002-build-tests-final.txt)。使用 `--ignore=tests/req002/test_auxiliary_regressions.py` 明确不运行两项已排除 Office 测试，未加入 skip/xfail；警告仍为既有 Starlette TestClient/httpx 弃用提示。
- 真实 WebUI v0.12.3 A/B 普通账号各验证中文文件、中文目录、双引号及 emoji 共 **8 个归档场景**，HTTP 200，下载名、ZIP 条目和内容均正确：[输出](evidence/r4-r5-002-webui-archive.json)、[脚本](../../../../tests/req002/integration_archive.py)。仅新增合成测试目录 req002-r5-002-e415cc05，复用现有账号和保存聊天，无新 LLM 调用。
- 已重建并部署 Terminal：`webui-lab/open-terminal:req002-r4-r5-002` / `:local`，ID `sha256:1510082c82ece0c5f818da711d0e596e9f981e3415ae9acc9c25dd7e6a144e74`。测试镜像 ID `sha256:8d07bf459cd4321680ede3945483e821b2bde0e415ae4579126a0681ab9f57c2`。[运行构建](evidence/r4-r5-002-build-runtime.txt)、[部署](evidence/r4-r5-002-deploy.txt)、[镜像](evidence/r4-r5-002-images.txt)、[八模块源码比较](evidence/r4-r5-002-runtime-source-comparison.json)。
- WebUI 容器 ID/启动时间不变，两服务 healthy；保留两原数据卷、5 个账号绑定及原标记，Notebook 关闭：[前环境](evidence/r4-r5-002-containers-before.txt)、[后环境](evidence/r4-r5-002-containers-after.txt)、[挂载](evidence/r4-r5-002-mounts.txt)、[保留核对](evidence/r4-r5-002-runtime-preservation.json)。本次没有修改部署配置或读取/更新 LLM Key。
- 已有 R5 测试、辅助响应实现、需求/设计及用户范围决定保持：[输入保留](evidence/r4-r5-002-input-preservation.json)。subagent 只读终审通过；`git diff --check` 通过。R5-S01 是非阻碍建议，本次未扩展处理。
- 复现命令和执行边界见 [repair-r5-002.md](evidence/repair-r5-002.md)。

**接收角色：R5。处理结论：R4 ready，R5-002 已修复，按用户确认的范围交回复验。** 请核验原中文归档两例、下载头及归档相关回归。R5-001 保持范围例外，不称修复通过。本轮未提交 Git，未自动向其他角色聊天发消息。

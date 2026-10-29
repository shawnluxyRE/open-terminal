# opt-user-space / REQ-001 R5 验收交接

- stage：R5
- result：passed
- 修订：v1.3，2026-10-29（Asia/Shanghai）；R5 独立复验 R4 修复通过，关闭 R5-001，并完成此前暂缓的清理。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033，关联 AC-023/034 的准备恢复契约。
- 验收依据：[plan.md](plan.md) v1.0、[workspace-identity.md](../../../design/workspace-identity.md) v1.2、[handoff-r4.md](handoff-r4.md) v1.1，以及接口注释、当前实现与测试。
- 审查对象：分支 `code/opt-req001`，比较基准 `7dab0d6ce15ad9277dc37a85a63f7c4b82213f18`（R2 设计/接口/测试提交）；Open Terminal `0.14.5`。包含 R4 未提交的四个业务文件修改及未跟踪测试、联调脚本和交付记录。开始时的源码摘要与 R4 清单全部一致，审查快照见 [r5-source-start.json](evidence/r5-source-start.json)，业务差异见 [r5-reviewed-production.patch](evidence/r5-reviewed-production.patch)。
- next_role：R0

## 产物

| 产物 | 内容 |
| --- | --- |
| [test_identity_regressions.py](../../../../tests/req001/test_identity_regressions.py) | 在原文件并入 37 项补测：已有任务/终端情况下的 key、缺身份、重复头拒绝顺序，以及 WS 首帧身份不能覆盖头部身份。 |
| [test_workspace_faults.py](../../../../tests/req001/test_workspace_faults.py) | 原并入 17 项补测保留；本次再追加 3 项持久化重试回归，验证 ready 重试不能绕过目录刷盘、planned 预留未确认持久化时不能创建系统资源。 |
| [integration_webui.py](../../../../tests/req001/integration_webui.py) | 真实 WebUI 双账号脚本统一在原文件维护；本次进一步要求 PTY 精确返回同账号命令得到的 UID 和用户名，保留原标记与 home 断言。 |
| [runtime_snapshot.py](../../../../tests/req001/runtime_snapshot.py) | 独立只读探针，核验实际运行模块摘要、版本、NSS 账号/组、元数据、home 和标记文件哈希；由原文件仅去除角色前缀，内容不变。 |
| [runtime_probe.py](../../../../tests/req001/runtime_probe.py) | 本次在原 UID 断言后补充完整 WS 关闭握手、关闭码 1000 及终端 404 检查，避免未等待服务端清理就退出 TestClient 上下文。 |
| [validation-r5.md](evidence/validation-r5.md) | 完整命令、环境、结果、账号资料及验证边界；其他日志和快照均在本需求 evidence 目录。 |
| [test-organization-r5.md](evidence/test-organization-r5.md) | 本次整理映射、用例保留校验、重新构建及复验记录。 |
| [recheck-r5.md](evidence/recheck-r5.md) | 历史复查缺陷、失败用例、PTY 复验和环境残留检查；原始失败证据保留。 |
| [retest-r5-001.md](evidence/retest-r5-001.md) | 本轮独立修复复验、问题关闭及清理记录；最新结论以本交接与该记录为准。 |

R5 修改限于测试、探针与验收文档/证据。初次验收新增独立测试文件，本次按用户更新的规则合入相关已有文件，移除已合并的重复文件，保留所有测试行为；未改业务代码或设计契约，未删除、跳过或削弱原用例，未提交 Git。所有编辑及证据保存均在 Windows；WSL Ubuntu-24.04 仅用于构建、运行和测试；Open WebUI 仓库只读。

## 验证证据

### v1.3 修复复验（最新）

- 当前源码/测试摘要与 R4 v1.1 返修交付全部一致，见 [起始快照](evidence/r5-r5-001-source-start.json)。只读复核确认：完整校验既有清单后，在同一准备锁内完成目录 fsync，随后才允许系统创建或 ready 返回；保存新状态仍遵循文件 fsync→replace→目录 fsync。故障按原错误链返回不可用，不依赖内存标记。
- R5 重新构建独立测试镜像，原 3 项失败回归与 R4 新增三状态用例 **6 passed、40 deselected、0.59 秒、退出 0**；全量 **389 passed、1 warning、45.86 秒、退出 0**，无 skip/xfail，见 [定向结果](evidence/r5-r5-001-targeted.txt)、[全量结果](evidence/r5-r5-001-pytest.txt)、[构建日志](evidence/r5-r5-001-build.txt)。
- 独立比较修复前后镜像中的 AST：原 76 个测试函数正文、参数全部不变，只追加 1 函数/3 状态用例，见 [保留校验](evidence/r5-r5-001-test-preservation.json)。
- 实际运行源码摘要匹配修复工作区，服务 UID1000，WebUI v0.12.3，Notebook 关闭。复用既有 A/B 普通账号和聊天，16 个并发 ready 复用、标记文件读写、命令、双方精确 UID/用户名 PTY 与跨用户/聊天拒绝均通过；没有新增账号或聊天，测试终端已清理，见 [WebUI 输出](evidence/r5-r5-001-webui.txt)。
- 删除此前确认的 3 个旧测试镜像和 4 个重复空聊天后，[运行前](evidence/r5-r5-001-runtime-before.json)与[运行后](evidence/r5-r5-001-runtime-after.json)快照完全一致；四个账号及 home/标记文件、原始验收聊天、两个持久卷和证据均保留。两个服务 healthy、无遗留测试容器、项目无临时垃圾，见 [清理核验](evidence/r5-r5-001-cleanup-summary.json)。

### v1.0～v1.2 历史实际执行

- 初次审查和文件整理时未发现验收阻碍；随后按用户要求再次检查故障持续与重入逻辑，确认 R5-001。下列 383 项通过记录是先前及已有测试结果，不能覆盖新发现的持久化缺口。
- 从当前源码和固定原运行基线重新构建一次性测试镜像。原有测试先独立重跑：**329 passed，1 warning，39.79 秒，退出 0**，见 [r5-pytest-initial.txt](evidence/r5-pytest-initial.txt)。
- 加入 54 项 R5 用例后重新构建并运行全套：**383 passed，1 warning，43.28 秒，退出 0**，无 skip/xfail，见 [r5-pytest.txt](evidence/r5-pytest.txt)、[构建日志](evidence/r5-build-tests.txt)。唯一 warning 为 Starlette TestClient 使用 httpx 的弃用提示，不影响测试结果。
- 一次性 root Docker 测试禁用网络，不挂载宿主源码/部署卷，不发布端口。全套包含真实 NSS 创建、线程/进程并发、失败重试和恢复，以及两个非 root 服务进程的预热 I/O、组刷新、命令/PTY 与单用户完整 app 回归。
- 固定 WebUI `v0.12.3` 的真实系统连接，新增专用普通账号 A/B、四个保存聊天，完成 16 个并发首次资源请求、不同文件、跨聊天读取、A 改显示名及重新登录、客户端伪造身份头覆盖、任务归属、终端 HTTP/WS 拒绝与正常 PTY：**全部通过，退出 0**，见 [r5-webui-integration.txt](evidence/r5-webui-integration.txt)。WS 跨用户拒绝使用 A 的真实聊天 ID，避免混淆用户与聊天两种检查。
- 普通账号请求只携带自己的 WebUI token；匿名 HTTP/WS 被拒绝，连接列表与配置/产品响应未出现实例 key，普通账号访问管理员连接配置返回 401。管理员凭据和实例 key 未写入日志或文件。
- [运行前快照](evidence/r5-runtime-before.json)只有原有 2 个 ready 绑定；[运行后快照](evidence/r5-runtime-after.json)增加恰好 2 个 R5 绑定。四个变更模块的运行摘要与审查源码一致；原有绑定、标记内容哈希、所有既有 UID/GID 编号及锁 inode 不变，见 [比较结果](evidence/r5-runtime-comparison.json)。两个持久卷保留，两个服务 healthy，Notebook 关闭，见 [环境记录](evidence/r5-environment.json)。
- 初次验收时 `git diff --check` 通过，开始快照内全部文件不变，见当时的 [r5-source-final.json](evidence/r5-source-final.json)。本次文件整理后的最新快照见 [r5-test-organization-source.json](evidence/r5-test-organization-source.json)，不覆盖原始历史快照。
- 按新规则整理后重新构建测试镜像，全量 **383 passed，1 warning，40.65 秒，退出 0**，见 [r5-test-organization-pytest.txt](evidence/r5-test-organization-pytest.txt)。AST 比对确认 74 个 pytest 测试函数（展开参数后 383 项）的正文及参数全部不变，见 [比较结果](evidence/r5-test-organization-comparison.json)。合并后的 WebUI 脚本复验通过，见 [r5-test-organization-webui.txt](evidence/r5-test-organization-webui.txt)；本次复用已有 R4 两账号，不作为首次准备证据。运行前后只读快照完全一致，原 R4/R5 标记内容和绑定保持不变。
- 本次补强的真实 PTY UID/用户名断言通过，实际为 UID10002、`owu_84a8cf57`；复用已有聊天，未新增聊天或账号，测试终端已清理，见 [r5-recheck-pty.txt](evidence/r5-recheck-pty.txt)。
- 新增持久化回归后的最终全量为 **3 failed、383 passed、1 warning，44.85 秒，退出 1**，三项失败均对应 R5-001，见 [r5-recheck-pytest-after-close-check.txt](evidence/r5-recheck-pytest-after-close-check.txt)。失败用例保留，不标记 skip/xfail；复现命令和证据性质见 [recheck-r5.md](evidence/recheck-r5.md)。
- 中途观察到一次单用户 TestClient WS 关闭时 CancelledError。补齐关闭握手及清理断言后，两运行用例连续三轮均通过，最终全量也通过这两项；没有吞掉异常，原失败输出保留在复查记录中。

### 验收矩阵

| 验收 | R5 验证与结论 |
| --- | --- |
| AC-001 稳定空间 | 真实 A/B home、工作账号和标记文件不同，跨聊天、改名和重新登录的正常流程通过。异常持久化重入 R5-001 已独立复验关闭；当前四个空间绑定与标记不变。通过。 |
| AC-002 特殊身份与冲突 | 全套覆盖短 ID、root、大小写/特殊合法字符、非法值、固定哈希碰撞、首选及随机名称占用、重试上限、已绑定账号/组/home 不一致；新增私有组主用户/附加成员/重复 GID 别名冲突和 ready home 属主冲突用例通过。通过。 |
| AC-003 可信身份与 key | 所有纳入范围的 HTTP/WS 语法、重复头、key 优先、固定错误转译通过；新增已有资源拒绝顺序及 WS 首帧伪造身份测试通过；真实 WebUI 匿名拒绝、后端身份覆盖、普通响应与连接配置不交付实例 key、正常 PTY 通过。界面视觉证据另引用 R4，见下节。通过。 |
| AC-004 并发唯一归属 | 全套同/异用户线程、进程与碰撞并发通过；非 root 多进程准备与复用通过；真实 16 请求只新增两个 ready 绑定，A/B 命令并发及任务/终端用户和聊天归属通过。通过。 |
| AC-032 UID/GID 闭区间 | 原用例实际创建并核验下界、独立跳号、上界；真实 R5 A/B 分别为 UID/GID 10003/10003、10004/10004，唯一且在范围内；运行前后所有既有账号/组编号相同。通过；上界占号是 NSS 快照。 |
| AC-033 占号与耗尽 | 独立 UID/GID 占号与耗尽、预留号被占后重试、半成品错误传播通过；新增满 GID 快照下可信区间内孤组复用通过；原有数据及已占编号对象不变。通过；未实际建立近万个账号或组。 |
| 关联 AC-023/034 模块契约 | 原恢复用例及持续目录故障下的 planned/group_ready/ready 重入均通过；故障持续时无提前系统副作用或成功发布，解除后先确认持久化再推进。R5-001 已关闭，本共用模块契约通过；完整生命周期仍按原范围另行验收。 |

### 引用 R4 的证据

[R4 验证记录](evidence/validation-r4.md)、[真实联调输出](evidence/r4-webui-integration.txt)作为交付背景。R5 本轮已独立重跑测试并使用新账号执行真实 API/WS 链路，不把 R4 报告当作本轮执行结果。

R5 阅读了 [R4 浏览器截图](evidence/r4-webui-a.png)，可见普通账号 A 的标记文件 `REQ001-A` 及其工作账号终端提示符；本轮未重新操作浏览器界面，此截图仍属于 R4 证据。实例 key 的结论另由本轮实际响应/权限测试及只读上游身份转发实现支撑，截图本身不证明所有响应内容。

## 本轮决策

**REQ-001 当前恢复 `passed`，R5-001 已关闭。** 读取清单后的持久化确认已在锁内补齐，原失败用例及三状态补测、389 项全量回归和实际服务回归均经 R5 独立执行通过，未发现新的验收阻碍。

复验对象为当前未提交工作区修复快照，最新摘要见 [r5-r5-001-source-final.json](evidence/r5-r5-001-source-final.json)。生产修复由 R4 完成，R5 本轮只读审查、验证并更新证据；实际运行镜像为 R4 部署的 `sha256:d30964f7047ed9b6375b7f1d5538fb5810d25467c695111e132a79e6ba57c1ab`，本轮未重新部署或修改 `.env`。

恢复仍以保留完整 `/home`（包含 `.open-terminal/registry.json`）和映射配置为前提；不将单独丢失归属清单后重建映射纳入已通过承诺。真实保留卷重建、服务器重启、兼容升级、个人依赖以及全面路径/符号链接授权分别留在 REQ-007、REQ-005、REQ-002 的范围，未据本轮结果宣称通过。Notebook 保留且关闭，配额与任意命令对抗隔离不在本轮范围。

两名 R5 普通测试账号、四个聊天和标记文件保留供后续复核，显示名已恢复，测试终端已清理；原有 R4 测试数据保持不变。

整理后曾创建的四个重复空聊天已于本轮复验通过后清理，原始 R4 与 R5 验收聊天保留。历史首次准备证据仍由初次 R5 联调记录提供。

## R4 修订汇总

### R5-001 [P2] 未确认持久化的清单状态在重试时继续使用（已关闭）

- 关联需求、验收或任务：REQ-001 的持久唯一归属及计划 T-003/T-005；关联 AC-023 的失败重试。设计 [workspace-identity.md](../../../design/workspace-identity.md) 第 132 行要求 replace 后 fsync 父目录、未落盘预留不能开始系统创建；第 143 行要求清单写入失败返回不可用并在下次锁内重试。
- 涉及位置与影响：`user_isolation.py:417–432` 在 replace 后目录 fsync 抛错时，可见清单已更新；`resolve:253–267` 对已有记录直接进入准备；`_prepare:565–570` 可先创建组，`:600–603` 可直接返回 ready。可见状态被当作已确认落盘状态，绕过约定的持久化顺序。此处没有实测断电或用户数据丢失；ready 之前的 group_ready 绑定可能已经持久化，不应声称整个映射必然丢失。
- 预期与实际行为：持续刷盘故障下，读取可见 planned 的重试不应先创建系统组；读取可见 ready 的重试不应跳过持久化确认直接成功。故障解除后须完成持久化确认再成功。实际分别出现组已创建、请求直接成功、重试没有目录 fsync 调用。
- 复现或验证依据：在真实一次性 root Docker 内，仅对归属清单父目录的外部 `os.fsync` 注入 ENOSPC；首次提交报 `workspace_unavailable` 后，以新 `WorkspaceProvisioner` 重试。新增 `test_visible_ready_after_failed_directory_fsync_requires_durable_retry` 两项和 `test_visible_planned_after_failed_directory_fsync_cannot_start_os_creation` 一项保留正确预期；三项均失败，详见 [失败日志](evidence/r5-recheck-pytest-after-close-check.txt)。
- 处理请求：R4 修复读取既有清单后的持久化确认与系统创建/成功发布顺序，同时检查 planned/group_ready/ready 的重入；保持完整 ID、账号名、UID/GID 和用户文件归属，不通过弱化用例掩盖问题。重新构建镜像，运行新增用例及全部相关回归，更新 `handoff-r4.md` 回复 R5-001。
- R4 回复与 R5 复验：R4 v1.1 确认问题并修复 `_load_registry`，在锁内完成校验和 `_sync_registry_directory` 后才使用状态，保存路径复用同一目录同步步骤。R5 核对实现、原测试未弱化，独立运行定向 6 passed、全量 389 passed 及真实 WebUI 回归；状态 **`closed`，2026-10-29**。历史失败日志及上述问题描述保留，当前已满足处理请求。

## 环境残留检查

项目未发现 `__pycache__`、pytest 缓存、构建目录、临时/备份文件；无遗留测试容器，两个部署容器健康。上轮列出的 `req001-r2-tests`、`req001-r4-tests`、`req001-r5-tests` 三个旧镜像已核对未被容器使用后删除；四个重复聊天在重新核对所属账号、标题、零消息后删除，并验证原始验收聊天仍可访问。详见 [镜像清理](evidence/r5-r5-001-image-cleanup.txt)、[聊天清理](evidence/r5-r5-001-chat-cleanup.json)、[最终核验](evidence/r5-r5-001-cleanup-summary.json)。当前修复/复现镜像、固定构建基线/回滚镜像、业务数据卷、账号/home、证据、部署备份与服务运行时文件保留，没有执行全局 prune。

## 下一步

**接收角色：R0。处理结论：REQ-001 `passed`，R5-001 `closed`，此前暂缓的明确残留已清理。** 请登记验收完成并决定后续需求或提交安排。本轮未自动向其他角色对话发送消息或提交 Git。

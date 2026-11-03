# opt-user-space / REQ-001 R5 验收交接

- stage：R5
- result：passed
- 修订：v1.6，2026-11-03（Asia/Shanghai）；R5 独立复验 Docker 可选组修复通过，关闭 R5-PERF-001；原 R5-001 维持 closed。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033，关联 AC-023/034 的准备恢复契约。
- 验收依据：[plan.md](plan.md) v1.1、[workspace-identity.md](../../../design/workspace-identity.md) v1.4、[handoff-r4.md](handoff-r4.md) v1.3，以及接口注释、当前实现与测试。本轮为 R5-PERF-001 返修复验，原需求与六条 AC 不变。
- 审查对象：分支 `codex/opt-req001-fix`，比较基准 `59eac68b4c0314ab633c376de82a539af3c98d52`；Open Terminal `0.14.5`。覆盖当前三份业务模块、R2/R3/R4 文档和全部未提交/未跟踪测试。开始时摘要与 R4 性能交付全部一致，见 [本轮起始快照](evidence/r5-perf-001-source-start.json)、[业务差异](evidence/r5-perf-001-reviewed.patch)。历史验收快照和失败证据保留。
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
| [test_agent_responsiveness.py](../../../../tests/req001/test_agent_responsiveness.py) | 本次追加 1 项真实路由测试：慢准备期间不分配 PTY；另一就绪用户占满唯一会话槽后，慢请求放闸只能返回 429，不能额外分配 PTY。 |
| [review-perf-001-r5.md](evidence/review-perf-001-r5.md) | 当前性能修订的源码审查、独立回归、三组同机性能、隔离 WebUI 链路及环境还原证据。 |
| [test_workspace_fast_path.py](../../../../tests/req001/test_workspace_fast_path.py) | 原 3 项 Docker 组维护失败回归已通过；R4 新增 9 项边界覆盖。本轮 R5 另补 1 项：观察期间 socket GID 持续变化时核心工作空间仍可返回，但不能缓存分歧观察，稳定后恢复零重操作暖路径。 |
| [test_workspace_recovery.py](../../../../tests/req001/test_workspace_recovery.py) | 本次补充区间外可信孤组正常恢复、groupdel 成功后失败再重试两项；均通过。 |
| [review-perf-second-r5.md](evidence/review-perf-second-r5.md) | 第二轮历史复查缺陷、6 项补测、旧版本差异验证与原始失败证据。 |
| [retest-docker-001-r5.md](evidence/retest-docker-001-r5.md) | 本轮独立修复复验、非 root 实际 socket 访问、暖路径及 442 项全量结果，当前结论以本记录与交接为准。 |

R5 修改限于测试、探针与验收文档/证据。初次验收新增独立测试文件，本次按用户更新的规则合入相关已有文件，移除已合并的重复文件，保留所有测试行为；未改业务代码或设计契约，未删除、跳过或削弱原用例，未提交 Git。所有编辑及证据保存均在 Windows；WSL Ubuntu-24.04 仅用于构建、运行和测试；Open WebUI 仓库只读。

## 验证证据

### v1.6 R5-PERF-001 修复复验（最新）

- 起始时全部源码/测试摘要与 R4 v1.3 返修交付一致，见 [起始快照](evidence/r5-docker-001-source-start.json)。只读交叉审查确认 socket 路径/跟随目标、GID 和成员完成状态参与失效；可选维护失败或观察分歧只禁止缓存，不拒绝核心工作空间，下一次访问继续维护。原版本/归属/目录 fsync 屏障保持。
- 固定原标准基线重新构建后，原三项与新增 Docker 边界、暖路径共 **13 passed、14 deselected、1.78 秒，退出 0**；R4 交付的原全量 **441 passed、8 warnings、95.56 秒，退出 0**，见 [定向](evidence/r5-docker-001-targeted.txt)、[原全量](evidence/r5-docker-001-pytest.txt)。
- R5 补充观察交错用例，单项通过；重新构建后最终全量 **442 passed、8 warnings、96.62 秒，退出 0**，无 skip/xfail，见 [新增用例](evidence/r5-docker-001-churn.txt)、[最终全量](evidence/r5-docker-001-pytest-final.txt)。修复前原 108 个测试函数正文/参数全部保持，见 [校验](evidence/r5-docker-001-test-preservation.json)。
- 独立标准运行镜像中以服务 UID1000 验证：第一次可选 usermod 失败后 UserFS 仍能读写，工作用户连接真实测试 Unix socket 被拒绝；第二次真实 sudo usermod 成功后，同一用户的新进程能连接 socket；随后 100 次访问不再 usermod。没有连接真实 Docker daemon，见 [非 root 输出](evidence/r5-docker-001-nonroot.txt)。
- 三次失败后组维护成功、主 GID 已满足、socket 不存在及观察交错解除后的稳定暖路径，100 次采样的九类重操作均为零。新补交错稳定后的 p50/p95=0.176/0.219ms；数值仅为本机证据，不是 SLA。
- 当前含 REQ-002 的正式测试部署未变；只读快照与 R4 保留部署一致，仍有 5 个空间，见 [部署比较](evidence/r5-docker-001-runtime-comparison.json)。本轮未重新执行 WebUI 链路，先前隔离联调保留为历史证据；本轮全量覆盖非 root、HTTP/WS、PTY 和单用户相关回归。
- 2026-11-03 追加有边界的回归/性能核查，未发现新的确定问题，passed 结论不变。相同依赖下 Docker 修复前后各做 3 轮×100 次暖访问：无 socket p95 中位数 0.137→0.155ms，已满足 socket 组 0.187→0.248ms，模拟 1000 个组成员 0.458→0.670ms；九类重操作均为零，慢用户闸门下仍独立完成。另一次 64 个新用户/32 worker 冷启动两版均 64 成功，当前总耗时 2.579s、基线 3.266s。方法、绝对差异及回退边界见 [补充核查](evidence/performance-followup-r5.md)；本次未改源码，也未无理由重复 442 项全量。

### v1.5 第二轮复查（历史）

- 本轮业务源码与 v1.4 的 426 项通过快照一致。重新从缓存未完成状态、外部环境变化、多次取消和少见恢复分支检查，发现 R5-PERF-001：缓存未观察 Docker socket，也未阻止可选加组未完成时发布缓存，导致后续访问跳过原有维护/重试。
- 在相关原测试文件追加 6 项，定向 **3 failed、3 passed、43 deselected、1 warning，1.66 秒，退出 1**：Docker 三场景失败；双取消持锁与区间外孤组正常/中断恢复三项通过。见 [定向结果](evidence/r5-perf-second-targeted.txt)。
- 同一镜像/依赖、相同新增 Docker 用例，仅在一次性容器内存加载 Windows `git show 59eac68:user_isolation.py` 的历史模块，结果 **3 passed、15 deselected、0.45 秒，退出 0**，见 [基线差异验证](evidence/r5-perf-second-docker-baseline.txt)。确认这是当前性能优化引入的兼容回归，未修改部署或历史源码。
- 完整回归为 **3 failed、429 passed、8 warnings，94.58 秒，退出 1**，仅 Docker 三项新增回归失败；见 [第二轮报告](evidence/review-perf-second-r5.md)、[全量输出](evidence/r5-perf-second-pytest.txt)。原 105 个 pytest 函数正文/参数保持，新增 3 函数展开为 6 项；保留失败预期，未修改业务代码或弱化原用例。

### v1.4 R2-PERF-001 首轮审查（历史）

- 独立核对异步接入、分层锁/合并提交、版本缓存/持久化三条路径，未发现确定的验收缺陷。慢准备由既有有界执行器承载；每用户锁和短全局事务顺序一致；缓存绑定已确认落盘版本，观察变化/未知 NSS/权限变化进入完整核验，原 R5-001 屏障保持。
- 使用固定原标准基线重新构建。R4 交付的原 425 项先独立重跑：**425 passed、7 warnings、92.07 秒，退出 0**；补充容量时序用例后全量：**426 passed、8 warnings、94.64 秒，退出 0**。新增单项也独立通过，无 skip/xfail；见 [初次全量](evidence/r5-perf-001-pytest-initial.txt)、[最终全量](evidence/r5-perf-001-pytest.txt)、[新增单项](evidence/r5-perf-001-targeted.txt)。
- 独立 AST 比较确认 R4 交付的原 104 个 pytest 函数正文/参数均保持，仅追加 1 个函数，见 [测试保留校验](evidence/r5-perf-001-test-preservation.json)。warnings 为已知 TestClient/httpx、真实 PTY 的 utcnow 及专门多线程 fork 回归提示。
- 三组同机独立测量：59eac68 暖访问 p50/p95=**2.761/4.106 ms**，当前实现=**0.121/0.187 ms**；当前连续 100 次暖访问九类观测重操作全部为零。慢 B 在 useradd 闸门暂停时，A 在放闸前约 **3.362 ms** 完成。旧无版本缓存仅作语义不同的延迟参考，见 [性能汇总](evidence/r5-perf-001-benchmark-summary.json)。
- 当前源码标准镜像在独立非 root 容器经真实 WebUI v0.12.3 验证 A/B 文件、命令、ports、PTY 精确身份及用户/聊天归属，全部通过；没有新增真实账号/聊天。临时连接与容器均已移除，原配置内容恢复，原部署 5 个空间及生产模块前后快照完全相同，见 [联调输出](evidence/r5-perf-001-webui.txt)、[运行态比较](evidence/r5-perf-001-runtime-comparison.json)、[连接清理核验](evidence/r5-perf-001-webui-cleanup.json)。
- 本轮生产代码未改，源码收尾校验见 [最终快照](evidence/r5-perf-001-source-final.json)。详细命令、镜像与验证边界见 [review-perf-001-r5.md](evidence/review-perf-001-r5.md)。

### v1.3 R5-001 修复复验（历史）

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

**当前性能 fix 恢复 `passed`，R5-PERF-001 已关闭；原 R5-001 维持关闭。** 可选组维护的失败重试和 socket 变化失效已修复，普通工作空间可用性与稳定暖路径性能保持。原失败用例、补充交错、442 项全量及独立非 root 实际权限验证均通过，未发现新的确定验收阻碍。

本结论覆盖 `codex/opt-req001-fix` 当前未提交返修快照，见 [本轮最终摘要](evidence/r5-docker-001-source-final.json)。R5 本轮只追加 1 项测试并维护交接/证据，业务实现未改。当前正式测试部署已含 REQ-002，**本性能分支尚未替换该部署**；本轮使用禁网、无正式卷/socket 挂载的一次性容器，后续发布仍需与当前部署统一集成。

恢复仍以保留完整 `/home`（包含 `.open-terminal/registry.json`）和映射配置为前提；不将单独丢失归属清单后重建映射纳入已通过承诺。真实保留卷重建、服务器重启、兼容升级、个人依赖以及全面路径/符号链接授权分别留在 REQ-007、REQ-005、REQ-002 的范围，未据本轮结果宣称通过。Notebook 保留且关闭，配额与任意命令对抗隔离不在本轮范围。

两名 R5 普通测试账号、四个聊天和标记文件保留供后续复核，显示名已恢复，测试终端已清理；原有 R4 测试数据保持不变。

整理后曾创建的四个重复空聊天已于本轮复验通过后清理，原始 R4 与 R5 验收聊天保留。历史首次准备证据仍由初次 R5 联调记录提供。

## R4 修订汇总

### R5-PERF-001 [P2] ready 缓存跳过可选 Docker 组维护及失败重试（已关闭）

- 关联需求/任务：R2-PERF-001 / T-009～T-010；设计 [workspace-identity.md](../../../design/workspace-identity.md) 第 174 行要求保留可选 Docker socket 组行为，第 160、190～194 行规定缓存发布与异常/变化后的重验。
- 涉及位置（修复前）：`user_isolation.py:308–333` 的 `_observe_ready` 未观察 `/var/run/docker.sock` 与相应组成员完成情况；`:336–347` 稳定命中直接返回；`:778–785` 可选附加组维护只在慢路径执行，失败仅警告后仍可发布 ready 缓存。
- 预期与实际：Docker socket 和组在首次准备前已存在时，只注入一次可选 `usermod` 失败；普通 workspace 仍可返回，故障解除后下一次访问应继续原有加组维护。当前实际两次 resolve 只尝试一次 usermod，用户仍不在 socket 组。socket 在预热后出现、或切换到另一预先存在的 GID 且 NSS 未变时，也被缓存漏掉。
- 复现与影响：新增 `test_ready_reuse_maintains_optional_docker_socket_access` 三参数用例使用真实 AF_UNIX socket、真实账号/组；当前三项失败，59eac68 同样三项通过。会使已配置的可选 Docker 用法持续无权限，直到其他事件触发完整重验；不影响普通 home 绑定，也未证明数据丢失或跨用户访问。
- 处理请求：R4 将可选 socket 变化和组维护未完成纳入重验/缓存失效条件，保留“可选失败不阻断普通工作空间”的既有语义，并保持稳定已完成状态的暖路径零重复重操作。不要通过每次无条件 usermod 或弱化回归规避缓存契约；修复后运行三项新回归、其余补测、原完整回归及必要的性能验证。
- R4 回复与 R5 复验：R4 v1.3 将 socket/目标/GID/成员完成状态纳入观察，未完成或观察失败不缓存；可选变化与核心确认分开，保持普通空间可用。R5 独立执行原失败回归、13 项定向、442 项全量、持续观察变化补测和 UID1000 的实际 UserFS/Unix socket 权限测试，全部通过；稳定暖路径九类重操作仍为零。**状态 closed，2026-11-03**，历史失败证据保留。

### R2-PERF-001 后台准备、分层锁与 ready 快速路径（验收通过）

- 关联需求/任务：REQ-001 并发唯一归属、Agent 正常可用性及 T-007～T-010，设计 v1.4 的性能/并发契约；不新增配额或命令隔离边界。
- R4 交付：异步手动入口使用既有 run_io；慢 OS/NSS 工作移出全局清单锁；按完整 ID 串行并只合并本用户记录；进程内缓存绑定确认版本并可靠失效；保留跨用户未确认提交屏障、fork 和非 root 访问修复。
- R5 复核/验证：独立只读交叉审查、425 项原测试复跑、1 项 PTY 容量补测及最终 426 项全量、三组性能比较、标准镜像隔离 WebUI 双用户回归全部通过，原 104 个测试函数未改。结果不依赖直接复述 R4 报告。
- 处理状态：首轮 426 项通过后，第二轮曾发现 R5-PERF-001；该兼容性问题现已修复并独立复验关闭，性能修订恢复通过。旧性能/联调数字仍作为其各轮实际证据，不冒充本次新测量。

### R5-001 [P2] 未确认持久化的清单状态在重试时继续使用（已关闭）

- 关联需求、验收或任务：REQ-001 的持久唯一归属及计划 T-003/T-005；关联 AC-023 的失败重试。设计 [workspace-identity.md](../../../design/workspace-identity.md) 第 132 行要求 replace 后 fsync 父目录、未落盘预留不能开始系统创建；第 143 行要求清单写入失败返回不可用并在下次锁内重试。
- 涉及位置与影响：`user_isolation.py:417–432` 在 replace 后目录 fsync 抛错时，可见清单已更新；`resolve:253–267` 对已有记录直接进入准备；`_prepare:565–570` 可先创建组，`:600–603` 可直接返回 ready。可见状态被当作已确认落盘状态，绕过约定的持久化顺序。此处没有实测断电或用户数据丢失；ready 之前的 group_ready 绑定可能已经持久化，不应声称整个映射必然丢失。
- 预期与实际行为：持续刷盘故障下，读取可见 planned 的重试不应先创建系统组；读取可见 ready 的重试不应跳过持久化确认直接成功。故障解除后须完成持久化确认再成功。实际分别出现组已创建、请求直接成功、重试没有目录 fsync 调用。
- 复现或验证依据：在真实一次性 root Docker 内，仅对归属清单父目录的外部 `os.fsync` 注入 ENOSPC；首次提交报 `workspace_unavailable` 后，以新 `WorkspaceProvisioner` 重试。新增 `test_visible_ready_after_failed_directory_fsync_requires_durable_retry` 两项和 `test_visible_planned_after_failed_directory_fsync_cannot_start_os_creation` 一项保留正确预期；三项均失败，详见 [失败日志](evidence/r5-recheck-pytest-after-close-check.txt)。
- 处理请求：R4 修复读取既有清单后的持久化确认与系统创建/成功发布顺序，同时检查 planned/group_ready/ready 的重入；保持完整 ID、账号名、UID/GID 和用户文件归属，不通过弱化用例掩盖问题。重新构建镜像，运行新增用例及全部相关回归，更新 `handoff-r4.md` 回复 R5-001。
- R4 回复与 R5 复验：R4 v1.1 确认问题并修复 `_load_registry`，在锁内完成校验和 `_sync_registry_directory` 后才使用状态，保存路径复用同一目录同步步骤。R5 核对实现、原测试未弱化，独立运行定向 6 passed、全量 389 passed 及真实 WebUI 回归；状态 **`closed`，2026-10-29**。历史失败日志及上述问题描述保留，当前已满足处理请求。

## 环境残留检查

2026-11-03 本轮仅使用一次性禁网容器，测试 Unix socket 由测试拥有并关闭/移除，测试结束容器自动删除；无正式 Docker socket 或数据卷挂载，没有更改部署、WebUI 配置、账号或聊天。

2026-11-02 本轮新增的临时服务 `req001-perf-r5-20261102` 和临时 WebUI 连接已清理；两个原服务健康、两个持久卷保留，原 5 个空间及生产模块完全不变。没有清理其他需求镜像或数据，也没有执行全局 prune。下文保留 2026-10-29 清理记录。

项目未发现 `__pycache__`、pytest 缓存、构建目录、临时/备份文件；无遗留测试容器，两个部署容器健康。上轮列出的 `req001-r2-tests`、`req001-r4-tests`、`req001-r5-tests` 三个旧镜像已核对未被容器使用后删除；四个重复聊天在重新核对所属账号、标题、零消息后删除，并验证原始验收聊天仍可访问。详见 [镜像清理](evidence/r5-r5-001-image-cleanup.txt)、[聊天清理](evidence/r5-r5-001-chat-cleanup.json)、[最终核验](evidence/r5-r5-001-cleanup-summary.json)。当前修复/复现镜像、固定构建基线/回滚镜像、业务数据卷、账号/home、证据、部署备份与服务运行时文件保留，没有执行全局 prune。

## 下一步

**接收角色：R0。处理结论：当前性能 fix `passed`，R5-PERF-001 `closed`，原 R5-001 维持 `closed`。** 请登记本轮修复验收完成；后续提交/发布由用户安排，并先与当前含 REQ-002 的版本统一集成。未提交、推送、修改正式部署或自动推进其他角色对话。

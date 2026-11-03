# opt-user-space / REQ-001 R4 交接

- stage：R4
- result：ready
- 修订：v1.3，2026-11-03；R5-PERF-001可选Docker附加组重试/缓存兼容性修复完成，441项通过，交R5独立复验。原R5-001维持closed。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033；关联 AC-023/034 的准备恢复契约。
- 计划依据：[plan.md](plan.md) v1.1；当前设计 [workspace-identity.md](../../../design/workspace-identity.md) v1.4；历史依据见下文。
- next_role：R5

## 产物

本轮接收[R5 v1.5](handoff-r5.md)的R5-PERF-001及用户明确要求：保留每次访问的可选Docker组维护语义，首次失败后下次仍重试，普通工作空间不因可选失败而不可用。先固定[源码/测试起始快照](evidence/r4-docker-001-source-before.json)，保留所有已有工作区及R5补测；独立重跑三场景均失败，[红基线](evidence/r4-docker-001-baseline.txt)。开发前检查通过后修复，主对话统一构建验证，另一subagent只读复核无阻碍。最终13项定向及441项全量通过，原108个测试函数AST保持；完整说明见[返修报告](evidence/repair-r4-docker-001.md)。

此前v1.2性能修订基线为`codex/opt-req001-fix` / `59eac68b4c0314ab633c376de82a539af3c98d52`，包含用户已有的R2 v1.4设计/注释/异步骨架/四份新测试及R3 v1.1计划，均先保存[起始摘要](evidence/r4-perf-001-source-before.json)。三个subagents的全任务可行性检查通过后开始：T-007由identity_integration负责异步接入，T-008/T-009由workspace_provision负责同一模块的锁与缓存，T-010由validation补测/性能探针，主对话统一构建、集成、联调与交接。当轮无阻塞设计或计划问题，原407项及R5-001回归保持。

| 本轮任务 | 交付结果 |
| --- | --- |
| T-007 | 完成：后台get_filesystem_async复用run_io和有界执行器，命令、终端、端口/代理await；准备结束后才分配PTY和检查容量，取消不提前释放工作线程的准备锁。 |
| T-008 | 完成：每用户线程/文件锁、短全局最新清单事务、比较旧记录后单条合并，慢OS与全NSS扫描移出全局锁；累计30秒等待预算及fork重建。 |
| T-009 | 完成：跨实例ready缓存绑定确认版本、前后稳定观察和失效；保留跨用户未确认提交屏障。补齐非root附加组刷新竞争、home_root和NSS来源边界。 |
| T-010 | 完成：425项、三组同机性能及未知NSS保守回退比较、非root实际访问、隔离WebUI链路及环境还原。新探针为[performance_probe.py](../../../../tests/req001/performance_probe.py)、[webui_connection_probe.py](../../../../tests/req001/webui_connection_probe.py)。 |

以下为初次交付及既有返修记录，当前状态以v1.3 R5-PERF-001记录为准。

从 `code/opt-req001` / `7dab0d6ce15ad9277dc37a85a63f7c4b82213f18` 开始，保留原有未跟踪的R3计划/交接文件。当前修改未提交Git，全部在Windows编辑，Open WebUI只读。

| 任务 | 状态与产物 |
| --- | --- |
| T-001 | 完成：[identity.py](../../../../open_terminal/utils/identity.py)、[dependencies.py](../../../../open_terminal/dependencies.py)，原样完整ID、唯一身份头、固定脱敏错误、单用户兼容。 |
| T-002 | 完成：[user_isolation.py](../../../../open_terminal/utils/user_isolation.py)，持久完整ID归属、8位候选/首次撞名最多32次随机换名、独立UID/GID池、线程+文件锁、原子预留与ready。 |
| T-003 | 完成：同模块提供planned/group_ready重入、失败恢复、冲突拒绝、可信home保留和账号重建；半成品不返回成功，缓存和旧同名复用不参与resolve_user决定。 |
| T-004 | 完成：[main.py](../../../../open_terminal/main.py)，HTTP key→身份→资源，WS首帧认证→身份→归属→存活；端口准备错误上送，任务/终端列表不建账号，元数据与成功协议保留。 |
| T-005 | 完成：保留原290项黑盒，新增[身份回归](../../../../tests/req001/test_identity_regressions.py)14项、[故障恢复](../../../../tests/req001/test_workspace_faults.py)23项、[运行集成](../../../../tests/req001/test_runtime_integration.py)2项及[探针](../../../../tests/req001/runtime_probe.py)。 |
| T-006 | 完成：[真实联调脚本](../../../../tests/req001/integration_webui.py)、[源码运行镜像配方](../../../../tests/req001/Dockerfile.runtime)、[验证记录](evidence/validation-r4.md)、[界面截图](evidence/r4-webui-a.png)。 |

## 验证证据

### v1.3 R5-PERF-001（最新）

- **441 passed、8 warnings、96.21秒，退出0**；[全量输出](evidence/r4-docker-001-pytest.txt)。原432项保留，新增9项覆盖连续失败后恢复、socket移除/再现、原子替换及symlink目标、未知GID、可选观察异常和主GID已满足；定向**13 passed**，[输出](evidence/r4-docker-001-targeted.txt)。
- 加组连续三次失败时各次仍返回同一可用工作空间，第四次成功；成功后100次暖访问9类重操作全为0，p50/p95为0.159/0.233ms。主GID已满足及无socket场景同样保持暖路径零重操作。实际非root服务进程第一次加组失败仍可UserFS读写，第二次真实sudo加组成功；[烟测输出](evidence/r4-docker-001-nonroot.txt)。
- 原108个测试函数正文/参数AST保持，R2/R3/R5文档未改；本轮生产变更仅user_isolation.py，测试仅追加至原fast_path文件：[源码校验](evidence/r4-docker-001-source-after.json)。安装到标准运行镜像的模块摘要与当前源码一致。
- 测试镜像`sha256:c018a77581218925ba3048345328d2638b2e8ac903d1b621d3e3fd88c0be0c4b`，标准运行镜像`sha256:5a72e8da1e45cdb350f6f958974cc466272f3813f864c8ebd4542212e1a6b28f`；固定原标准基础镜像重新构建。当前含REQ-002的部署未替换，原五个空间及运行快照完全保持、两个服务healthy：[运行比较](evidence/r4-docker-001-runtime-comparison.json)。
- 具体修复、实际命令、有限性能测量、真实socket与Docker daemon的验证边界及完整证据索引见[返修报告](evidence/repair-r4-docker-001.md)。所有本轮验证文件均在evidence目录，沿用Git忽略规则，未提交/推送。本轮未重新执行WebUI浏览器联调，下文v1.2为历史证据。

### v1.2 R2-PERF-001（历史）

- **425 passed、7 warnings、91.18秒，退出0**；[全量输出](evidence/r4-perf-001-pytest.txt)。原407项保留，新增18项覆盖取消/执行器上限、锁共享预算、持锁fork、NSS/版本/根变化及非root并发访问。原89个测试函数正文/参数AST保持，R2/R3/R5输入文档未改；[校验](evidence/r4-perf-001-source-after.json)。
- 同机暖100次：59eac68 p50/p95为2.456/3.620ms，新实现0.124/0.210ms，约19.8/17.2倍改善；新稳定暖路径9类观测重操作全部为零，慢B时A放闸前约3.107ms完成。旧无版本缓存为0.000104/0.000180ms，语义不同，仅作参考，不声称等时。[三组及回退详情](evidence/repair-r4-perf-001.md)。
- 修复测试镜像`sha256:5a749f71418dc5be26851e7c6466190870b056431b822442ade56063c866421e`，标准运行镜像`sha256:8d56aef20e71d7e3882a116d68b20ac09c6c89c5f378f39fef6959a62ccea144`；均基于固定原标准环境重新构建。
- 当前原部署已含REQ-002，未覆盖。独立临时容器复制A/B测试绑定/标记，经既有WebUI v0.12.3临时连接验证真实文件、命令、ports、PTY和用户/聊天归属通过，未新增真实账号/聊天；[输出](evidence/r4-perf-001-webui.txt)。临时连接和容器已移除，原配置内容恢复，原5空间及模块快照完全不变；[比较](evidence/r4-perf-001-runtime-comparison.json)。本性能分支尚未替换现有生产部署。
- 完整实现、实际命令、测量边界、NSS来源与本地证据索引见[本轮验证报告](evidence/repair-r4-perf-001.md)。evidence被现有Git规则忽略，日志实际可在本机工作区访问，未宣称已提交/推送。

### v1.1 R5-001返修

- 原三个失败用例已独立复现：3 failed；修复后原用例与新增三状态用例共6 passed。最终全量 **389 passed、1 warning、46.82秒，退出0**，见[定向结果](evidence/r4-r5-001-targeted.txt)、[全量结果](evidence/r4-r5-001-pytest.txt)。
- 原76个测试函数正文与参数AST完全保持，新增仅1函数/3用例；本轮生产变更仅user_isolation.py，见[源码/测试保留校验](evidence/r4-r5-001-source-after.json)。
- 已部署修复镜像`sha256:d30964f7047ed9b6375b7f1d5538fb5810d25467c695111e132a79e6ba57c1ab`。现有四个绑定、系统UID/GID、标记文件哈希和锁inode保持，运行模块摘要匹配工作区；[比较结果](evidence/r4-r5-001-comparison.json)。WebUI v0.12.3与两个持久卷保留，两个服务healthy。
- 复用现有A/B普通账号及聊天，实际文件读写、命令、PTY精确身份及跨用户/聊天拒绝通过，未新增账号/聊天；[真实WebUI输出](evidence/r4-r5-001-webui.txt)。
- 原因、修复边界、实际命令、镜像、全部证据与未验证项见[返修验证记录](evidence/repair-r4-r5-001.md)。未修改R5验收结论或执行其暂缓清理。

### v1.0历史验证

- 三个subagents完成全部任务开发前检查，无阻塞项，汇总确认后才实现。按文件独占分工，主对话统一构建、集成、联调。最终另做四个生产文件的只读交叉审查，未发现阻碍交付的具体问题。
- 最终一次性Docker回归：**329 passed，1 warning，38.51秒，退出0**；[输出](evidence/r4-pytest.txt)。包括两个非root服务进程真实文件API/组刷新、命令/PTY，以及单用户完整app。无skip/xfail，未修改原黑盒验收断言。
- 真实WebUI v0.12.3：两个普通账号、每账号两个已保存聊天、16个并发首次请求、各自文件、跨聊天读取、A改名/重新登录、后端身份覆盖、命令归属、终端HTTP/WS拒绝和正常PTY全部通过；[输出](evidence/r4-webui-integration.txt)。普通连接/配置响应无实例key，普通用户无法读取管理员连接配置。
- 普通账号A的浏览器实际显示自己的标记文件与终端工作账号；[截图](evidence/r4-webui-a.png)。测试浏览器已关闭，临时登录文件已移除。
- v1.0交付时部署的源码镜像为 `sha256:144cd575582848099d7de0d733f58d78cf93b9ea2186933c1c61319c465bd418`；该镜像保留，当前运行镜像以v1.1记录为准。服务UID1000、Notebook关闭，WebUI固定v0.12.3，两个原持久卷保留。
- [运行态核验](evidence/r4-runtime-evidence.json)与[源码摘要](evidence/r4-source-manifest.json)已核对四个生产模块一致；两组工作账号/home/UID/GID不同，清单与实际系统对象一致。
- 完整命令、镜像/依赖、六条AC及模拟/实际边界集中记录在[validation-r4.md](evidence/validation-r4.md)，供后续需求引用。

## 本轮决策

- 遵循R2 v1.2与R3 v1.0，无架构、公共接口、验收含义变更，无需R2/R3先行修订。局部实现包含有界命令、严格元数据模式、脱敏操作号/步骤日志，以及useradd写入账号但home失败后的重入处理。
- 运行镜像复用已验证的原标准环境、重新安装当前源码并保留python3-ot能力；实际版本及源码摘要均核验。原环境保留为`req001-r4-base`，构建条件见验证记录。
- REQ-001六条AC均有本轮通过证据；AC-023/034仅覆盖共用准备模块。真实卷重建/升级、个人包、全面路径授权及配额保持REQ-007/005/002等原边界。
- 两普通测试账号、四测试聊天和标记文件保留供R5复核；未执行账号删除同步或历史数据迁移。

## 下一步

R5-PERF-001已修复并完成定向、全量和必要性能验证，交R5独立复验并决定关闭。R4为ready，不代替R5修改其open状态。现有含REQ-002的部署继续保留，原R5-001维持closed；本轮无需R2/R3先行修订。

## R2 修订汇总

### R4-TEST-001 新增非root补测的临时父目录权限（已自行修正，供R2知悉）

- 关联T-010、test_workspace_fast_path.py中新追加的非root既有工作线程用例。首轮1 failed/421 passed，失败为测试临时root默认0700，真实工作账号的sudo mkdir不能穿越其祖先目录；不是产品权限契约失败。
- 仅将该测试临时root设置0711，与既有响应性fixture一致；state0700、home2770及实际读写/属主断言保持，原R2/R5用例未改。修后定向15 passed、全量425 passed，见本轮报告。无需改变设计或等待技术决策。

## R5 返修回复

### R5-PERF-001 ready缓存跳过可选Docker组维护及失败重试

- 来源：[handoff-r5.md](handoff-r5.md) v1.5；关联T-009/T-010及设计v1.4保留可选Docker组行为的契约。
- 核实：R5三场景独立复现3 failed，问题成立。可选加组失败不应阻断工作空间，但不能发布会跳过后续重试的缓存。
- 修复：观察socket及跟随目标、GID和实际成员状态；可选维护未完成或观察失败时只返回已验证工作空间，不发布缓存。变化使旧缓存失效，后续访问重试原追加组维护。核心版本/归属确认保持，不将可选变化计为核心确认失败，不删除旧成员关系。
- 验证：R5三项保持；追加9项，定向13 passed、全量441 passed。非root真实sudo失败后重试及工作空间读写通过，稳定成功后暖100次9类重操作均为0。原108个测试函数AST完整保持，[报告](evidence/repair-r4-docker-001.md)。
- 状态：R4已完成返修，等待R5独立复验；未修改R5验收结论、未覆盖现有含REQ-002的部署。

### R5-001 未确认持久化的清单在重试时继续使用

- 来源：[handoff-r5.md](handoff-r5.md) v1.2的“R4修订汇总”；关联T-003/T-005及AC-023共用模块契约。
- R4核实：问题成立；使用R5最终复现镜像独立重跑新增用例，3 failed、40 deselected、0.43秒，退出1，见[本轮红基线](evidence/r4-r5-001-baseline.txt)。仅对目录fsync注入故障，未实测断电或数据丢失。
- 开发前检查：实现和验证两个subagents均确认可按既定设计修复，无需R2/R3变更。检查汇总通过后才开始实现；完成后另一subagent只读复核无阻碍。
- 修复：_load_registry在锁内完整校验既有清单后调用_sync_registry_directory，成功后才允许状态驱动系统准备/返回ready；_save_registry在replace后复用同一步骤。故障持续时仍返回workspace_unavailable，故障解除后完成确认再按原绑定继续。不新增schema/状态或依赖内存标记。
- 测试：R5原3项保持不变；在原test_workspace_faults.py追加planned/group_ready/ready持续故障→解除重试共3项，只观察外部fsync与真实NSS/home。定向6 passed，全量389 passed；原76个测试函数AST未变。
- 实际服务：当前源码重新构建部署，四个已记录空间恢复后清单完全一致；现有A/B文件/命令/PTY与归属拒绝通过，全部原标记哈希、UID/GID和锁inode不变。
- 当前状态：R5已于2026-10-29在v1.3交接独立复验关闭；上述为当时R4修复回复，历史材料见[返修记录](evidence/repair-r4-r5-001.md)。本轮性能优化保留其回归保护。

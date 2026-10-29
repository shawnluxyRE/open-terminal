# opt-user-space / REQ-001 R4 交接

- stage：R4
- result：ready
- 修订：v1.1，2026-10-29；R5-001已修复并完成R4复验，待R5独立复验关闭。v1.0结果保留为历史证据。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033；关联 AC-023/034 的准备恢复契约。
- 计划依据：[plan.md](plan.md) v1.0；设计 [workspace-identity.md](../../../design/workspace-identity.md) v1.2。
- next_role：R5

## 产物

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

**接收角色：R5。处理结论：R5-001已修复并完成R4复验，恢复R4 ready。** 请由用户在独立R5对话指示复验，重点检查三个原失败用例、三状态补测及完整回归；由R5确认关闭R5-001后恢复其验收结论并决定后续清理。本轮未自动推进其他角色对话。

## R5 返修回复

### R5-001 未确认持久化的清单在重试时继续使用

- 来源：[handoff-r5.md](handoff-r5.md) v1.2的“R4修订汇总”；关联T-003/T-005及AC-023共用模块契约。
- R4核实：问题成立；使用R5最终复现镜像独立重跑新增用例，3 failed、40 deselected、0.43秒，退出1，见[本轮红基线](evidence/r4-r5-001-baseline.txt)。仅对目录fsync注入故障，未实测断电或数据丢失。
- 开发前检查：实现和验证两个subagents均确认可按既定设计修复，无需R2/R3变更。检查汇总通过后才开始实现；完成后另一subagent只读复核无阻碍。
- 修复：_load_registry在锁内完整校验既有清单后调用_sync_registry_directory，成功后才允许状态驱动系统准备/返回ready；_save_registry在replace后复用同一步骤。故障持续时仍返回workspace_unavailable，故障解除后完成确认再按原绑定继续。不新增schema/状态或依赖内存标记。
- 测试：R5原3项保持不变；在原test_workspace_faults.py追加planned/group_ready/ready持续故障→解除重试共3项，只观察外部fsync与真实NSS/home。定向6 passed，全量389 passed；原76个测试函数AST未变。
- 实际服务：当前源码重新构建部署，四个已记录空间恢复后清单完全一致；现有A/B文件/命令/PTY与归属拒绝通过，全部原标记哈希、UID/GID和锁inode不变。
- 当前状态：R4已修复、验证通过，提交R5复验；R5交接中的open由R5复验后关闭。完整材料见[返修记录](evidence/repair-r4-r5-001.md)。

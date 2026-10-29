# opt-user-space / REQ-001 R3 交接

- stage：`R3`
- result：`ready`
- next_role：`R4`
- 修订：v1.0，2026-10-28。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033；关联 REQ-007 / AC-023、AC-034 的准备恢复契约。
- 设计依据：[workspace-identity.md](../../../design/workspace-identity.md) v1.2、[handoff-r2.md](handoff-r2.md) v1.2。
- 核对源码：分支 `code/opt-req001`，HEAD `7dab0d6ce15ad9277dc37a85a63f7c4b82213f18`，业务版本 Open Terminal 0.14.5；本轮开始时工作区干净。

## 产物

| 产物 | 实际路径与内容 |
| --- | --- |
| 概要开发计划 v1.0 | [plan.md](plan.md)，实际位置 `D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001\plan.md`。六项任务覆盖身份校验、首次准备与复用、失败恢复、HTTP/WS 接线、容器回归和真实 WebUI A/B 联调；包含依赖、并行条件、验证矩阵和完成判定。 |
| R3 交接单 v1.0 | 本文件，实际位置 `D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001\handoff-r3.md`。记录规划依据、核对结果、证据限制与下一步请求。 |

## 验证证据

**本轮已核对：**

- REQ-001 的六条 AC、共同决定 D01～D11，以及关联 REQ-007/008 的边界；R2 v1.2 的模块设计、三份接口/注释文件和 `main.py` 的现有运行入口。
- [tests/req001](../../../../tests/req001) 的五个测试文件、测试 Dockerfile 与配置：测试目标是完整预期行为，新入口仍未实现，真实路由仍需接线。root OS fixture、HTTP 准备边界替身和 WS 拒绝路径均不足以证明非 root 服务或完整真实用户链路。
- [baseline-pytest.txt](baseline-pytest.txt) 末行与 R2 交接一致：`236 failed, 54 passed, 1 warning in 3.91s`。设计与测试对首选名、随机换名、完整 ID 绑定、UID/GID 独立区间和失败重试的一致性未发现阻塞项。
- 两份新增文档的本地链接均可解析，无模板占位符或行尾空白；`git diff --check` 无报错，工作区只新增本轮计划与交接单。交叉复核已将“不建账号”的列表/控制明确限定为任务与终端，文件和端口入口继续遵守各自准备契约。

**引用的运行结果：** R2 于 2026-10-28 执行的290项测试，54通过、236失败，其中141项入口未实现、95项路由契约待接入；无导入/收集/fixture 错误。镜像 ID、构建环境、依赖版本及复现命令见 [handoff-r2.md 的验证证据](handoff-r2.md#验证证据)。本轮没有重新运行 pytest，不将该记录描述为 R3 执行结果或业务验收。

**仍待 R4 验证：** 新实现全部黑盒回归、未覆盖的失败/持久化边界、非 root 服务进程实际访问、正常命令和 WS/PTY、显式单用户完整 app，以及真实 WebUI A/B 的稳定归属和 key 保管。真实保留卷重建/升级及个人包生命周期分别由 REQ-007/005 完成，不能从本计划的模块测试推定通过。

## 本轮决策

1. 采用 R2 v1.2，保留用户已确认的短名与首次占用后随机重选方案。已绑定异常继续明确失败，不用重新命名掩盖归属冲突；不重新讨论已确认业务边界。
2. 按可验证结果分六项任务。身份与 Linux 准备可按已定接口并行，恢复依托持久准备路径；真实路由接线可以提前开展，最终验证须使用完整新调用链。修改同一模块的冲突由 R4 统筹，不在概要计划中指定私有函数或 subagents 分工。
3. 将真实 A/B 登录、跨聊天、改显示名、上游身份覆盖及普通用户不取得实例 key 纳入 REQ-001 完成条件。其证据可供 REQ-008 引用，不能因属于联调就全部延后。
4. 现有290项全绿是必要回归依据，不是所有设计契约与 AC 已验证的充分条件。计划补列非 root、超时、写入失败/中断恢复、正常 PTY 和单用户完整运行的验证结果要求，具体实现与测试方法交 R4。
5. 规划范围不扩到其他需求。保留已有 `UserFS`、执行器、任务与聊天归属机制；完整文件授权、个人环境、资源配额和全生命周期另行处理。Open WebUI 只读，Notebook 关闭，Windows 编辑/Git 与 WSL 构建运行测试的约束继续有效。

## R2 修订汇总

本轮无需要 R2 先行修订的设计或测试问题。尚未实现造成的红基线与已声明的验证缺口已分配到开发/验证任务，不作为设计矛盾回交。后续若 R4 发现外部契约不一致，保留问题编号、受影响任务与证据，更新本记录后按流程交回 R2；不得为测试通过自行改变验收含义。

## 下一步

**接收角色：R4。处理结论：规划已就绪，可以按 [plan.md](plan.md) 开始 REQ-001 实现。** 无待人工澄清的业务决定或阻塞启动的设计问题。

请由用户在独立 R4 对话中指定使用本计划和 R2 v1.2 设计，安排具体实现与 subagents 分工，完成六项任务及证据记录，再提交本需求目录的 `handoff-r4.md`。联调账号或环境未具备时，注明受影响验收并继续独立工作，不提前宣称需求完成。若对规划有反馈，在 R4 交接单“R3 修订汇总”中给出原问题编号、依据与请求。

本轮产物仅为概要计划与交接记录，未修改业务代码、测试、R1/R2 产物、Open WebUI、部署配置或持久卷；未提交 Git 变更、创建或发送其他角色对话，也未启动 R4 实现。

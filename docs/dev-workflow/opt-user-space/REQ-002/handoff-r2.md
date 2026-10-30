# opt-user-space / REQ-002 R2 交接

- stage：R2
- result：ready
- 修订：v1.0，2026-10-29（Asia/Shanghai）。首次设计，交付用户 / R0，接收角色 R3。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-002`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-002 / AC-005～009；D01～D06、D08～D10。依赖 REQ-001 已完成实现与验收的身份/home，背景引用 [R5 交接](../REQ-001/handoff-r5.md)，本轮另有独立回归。
- 源码基线：分支 `codex/opt-req002`，HEAD `59eac68b4c0314ab633c376de82a539af3c98d52`，基于 `code/opt-req001`；Open Terminal 0.14.5，Open WebUI v0.12.3。
- next_role：R3

## 产物与交付状态

| 产物 | 内容与状态 |
| --- | --- |
| [file-access.md](../../../design/file-access.md) v1.0 | 共享文件访问模块：读写授权、软链/路径、公共只读配置、错误、递归过滤、辅助处理与逐入口接线矩阵；包含 UML 类图。 |
| [file_access.py](../../../../open_terminal/utils/file_access.py) | `FileAccessPolicy.require/allows`、`parse_public_read_roots` 与两个异常的接口骨架和契约注释；三个方法尚未实现。 |
| [fs.py](../../../../open_terminal/utils/fs.py) | 新增可选 `public_read_roots` 参数并保存输入、新增 `require_path` 骨架；保留现有两个位置参数与全部既有操作实现。新入口尚未被旧代码调用。 |
| [test_file_access_policy.py](../../../../tests/req002/test_file_access_policy.py) | 89 项纯策略/配置解析黑盒测试：真实路径、链接、缺失尾路径、读写差异、跨区域多跳、公共配置有效性。 |
| [test_public_config.py](../../../../tests/req002/test_public_config.py) | 17 项新进程启动测试：真实配置加载、环境优先、保护根、配置错误与显式单用户兼容。 |
| [test_file_http.py](../../../../tests/req002/test_file_http.py) | 74 项真实 HTTP 测试：A/B 同名文件、跨聊天、别名、读写/搜索、软链与 `..`、上传最终链接、公共只读、移动/删除。 |
| [test_file_auxiliary.py](../../../../tests/req002/test_file_auxiliary.py) | 19 项辅助入口测试：真实文本比较、ZIP、DOCX 文本提取，预览授权顺序、静态服务、技能根/清单/资源链接。 |
| [test_userfs_contract.py](../../../../tests/req002/test_userfs_contract.py) | 19 项 `UserFS` 公开行为与搜索 fallback 测试：新入口、metadata、walk、直接写、链接删除/移动、已有目标冲突。 |
| [conftest.py](../../../../tests/req002/conftest.py)、[Dockerfile](../../../../tests/req002/Dockerfile) | 一次性 Docker 运行配置，隔离账号准备和管理员配置输入，复用真实 app/UserFS/I/O；无业务授权结果 mock。 |

**ready 表示设计、接口与测试可供 R3 规划，不表示 REQ-002 已实现或通过验收。** 本轮没有实现业务逻辑、连接新授权入口、改 Open WebUI、改部署或提交 Git。开始时已有的 `AGENTS.md` 工作编号改动保持不动；R1 需求与 REQ-001 文档/测试未修改。

## 本轮设计决定

1. 在 `UserFS` 内复用既有 I/O，统一提供读/写授权入口。本人 home 读写；显式公共根只读；其他路径拒绝。服务进程 OS 权限不作为产品权限；不建设命令沙箱。
2. 默认 home 别名保留。路径检查兼顾词法入口、每次软链跳转和最终目标，不能先消除 `link/..`。新文件允许缺失尾路径；断链/环/不可判定路径拒绝。
3. 公共资料通过管理员 `OPEN_TERMINAL_PUBLIC_READ_ROOTS` JSON 数组声明，默认空；配置文件键 `public_read_roots` 沿用同格式。保护整个 `/home`、真实服务 home、日志、实际临时根和系统伪文件系统，启动时整份校验。无 UI/共享授权功能。
4. 显式越权统一 403 `{"detail":"Access denied"}`；在存在性、metadata、辅助处理及写副作用之前拒绝。递归子项过滤且不返回非法名称、内容或 metadata；非法技能条目过滤后不妨碍合法项，未知技能保持 404。
5. 移动要求源/目标均可写并拒绝已存在目标；删除/移动不得破坏 home 根。链接条目的删除/移动不改成目标删除/移动，递归操作与跨文件系统回退不能跟随外部链接。
6. Office/对比/归档复用现有处理器，先授权输入；保留服务临时结果的请求内响应和清理，不将其发布为公共路径。显式单用户模式保持旧访问能力。

内部审查指出的四项问题已落实：新增真实启动配置测试、HTTP `link/..` 测试、上传最终文件软链测试，并明确实际临时根保护；另补技能清单软链与独立布尔谓词测试，确保所有新增入口均可直接触达。无需要用户重新确认的业务范围事项。

## 验证证据

### 实际运行环境与命令

代码、测试、文档与输出日志均在 Windows 写入。WSL 实际发行版为 **Ubuntu-24.04**，仅用于 Docker 构建/运行/测试，源码目录 `~/projects/webui/open-terminal` 解析到 `/mnt/d/Documents/synowave/WebUI/open-terminal`。没有在 WSL 编辑源码或执行 Git 提交。

独立测试镜像使用已有 REQ-001 测试运行环境作为依赖基底，重新 COPY 当前 Windows 源码与测试。基底 ID `sha256:96658b5396590ed06d29e5ccbbe579e1e1df8b4bba495cad1f30105a613bcb49`，最终镜像 `webui-lab/open-terminal:req002-design-tests`，ID `sha256:71fd46981d4168d1ffb68bc3dcb7e2a64725773d9891a73a88e86b57a65f8e8f`。Python 3.12.13、pytest 9.0.2、Open Terminal 0.14.5，实际导入 `/opt/req002/open_terminal`。

在 WSL 项目目录执行如下构建；终端输出由 Windows PowerShell 保存至 evidence：

```bash
docker build --network=none \
  --build-arg BASE_IMAGE=webui-lab/open-terminal:req001-r5-001-tests \
  -f tests/req002/Dockerfile \
  -t webui-lab/open-terminal:req002-design-tests .
docker run --rm --network none webui-lab/open-terminal:req002-design-tests \
  tests/req002 -q -rA -p no:cacheprovider --tb=short
```

构建退出 0；测试 **207 failed、11 passed、1 warning，5.26 秒，pytest 退出 1**，无 skip/xfail、无收集或 fixture 环境错误。参见 [最终构建](evidence/r2-build-final.txt)、[最终 REQ-002 输出](evidence/r2-pytest-req002-final.txt)、[环境与镜像](evidence/r2-environment-final.txt)。

| 结果类别 | 数量 | 含义 |
| --- | --- | --- |
| 骨架尚未实现 | 91 failed | 89 项策略/解析/布尔谓词测试，以及 2 项 `UserFS.require_path` 测试，实际触达入口后抛 `NotImplementedError`。这是 R2 明确保留的未实现基线。 |
| 配置尚未接线 | 17 failed | 子进程成功加载现有 env 模块，但尚无 `PUBLIC_READ_ROOTS` 导出，探针记录 `AttributeError`，与期望启动行为不同；不是依赖缺失或模块导入环境故障。 |
| 既有行为与新契约差距 | 99 failed | HTTP/UserFS 的软链、搜索、公共写入、元数据泄漏、错误消息/状态、home根及链接操作等；包括当前删除目录软链抛 OSError。均为需 R4 修复的实际行为，不归为测试环境故障。 |
| 已有正常能力 | 11 passed | A/B 同名上传替换搜索删除及跨聊天读取、两个默认 home 别名、正常移动下载、真实比较/文档提取、受控转换器预览、静态与公共归档读取、本人目录链接移动。公共读取通过不能证明只读授权已完成。 |

AC-005 正常文件流程已有通过项；AC-006/007 的统一归属与遍历、AC-008 的辅助入口拒绝及技能过滤、AC-009 的公共只读和启动配置仍待实现。测试断言期待真实正确行为，没有把 `NotImplementedError` 视为通过，也没有为了基线变绿削弱越权断言。

另在本轮最初测试构建（已包含最终的 `fs.py` 结构改动，后续仅补测试和注释）执行既有回归：

```bash
docker run --rm --network none webui-lab/open-terminal:req002-design-tests \
  tests/req001 -q -p no:cacheprovider --tb=short
```

实际为 **389 passed、1 warning，53.29 秒，退出 0**，见 [REQ-001 回归输出](evidence/r2-pytest-req001.txt)与 [当时构建](evidence/r2-build.txt)。后续未修改 REQ-001 测试或已执行的生产方法体。两套测试的唯一 warning 均为既有 Starlette TestClient/httpx 弃用提示。`git diff --check` 通过；[交付文件摘要](evidence/r2-source-snapshot.json)用于复核当前未提交文件。

首次 REQ-002 探测为 190 项、179 failed/11 passed；其后补齐配置和审查用例，并精简拒绝断言的诊断输出，最终扩展为 218 项。历史输出 [r2-pytest-req002.txt](evidence/r2-pytest-req002.txt)保留，最终计数以上文为准。

### 运行隔离与未覆盖事项

所有测试容器禁用网络、无宿主源码写挂载、无部署卷、无对外端口，结束自动删除。真实运行服务保持 healthy，Open Terminal 运行镜像仍为 `sha256:d30964f7047ed9b6375b7f1d5538fb5810d25467c695111e132a79e6ba57c1ab`，WebUI 仍为 v0.12.3 / `sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def`；未重启、部署、读取凭据、创建真实账号或改动持久卷。

本轮 HTTP fixture 用 root 及独立 home 隔离已完成的账号准备边界，不证明服务 UID1000 的权限。真实 LibreOffice 渲染、请求中断临时文件清理、管理员公共目录挂载及固定 WebUI 的双普通账号文件/AI/预览/技能链路仍需 R4/R5 集成验证。Notebook 继续关闭，命令对抗隔离和资源配额不纳入本轮。

## 下一步请求

请用户 / R0 将本交接交给 **R3**，基于 `file-access.md` 和当前接口/测试编写本需求 `plan.md`：安排策略与配置、UserFS 适配、全部直接 I/O 入口接线、错误规范及集成验证，再由 R4 实现。验收应同时达到新增契约测试通过和既有回归保持，并补足上述真实环境验证。

无等待确认事项。本轮停在 R2 ready；没有自行创建或推进 R3/R4 聊天。

# opt-user-space / REQ-001 R2 交接

- stage：`R2`
- result：`ready`
- next_role：`R3`
- 修订：v1.4，2026-11-02；用户直接授权 R2-PERF-001 性能与并发设计修订，业务实现留给R4。
- work_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space`
- req_dir：`D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001`
- 需求依据：[requirements.md](../requirements.md) v1.10，REQ-001 / AC-001～004、AC-032～033，关联 REQ-007 / AC-023、AC-034 的恢复契约；既有 D01～D11 不变。
- 本轮分支：`codex/opt-req001-fix`，从 `codex/opt-req001` / `59eac68b4c0314ab633c376de82a539af3c98d52` 创建并已切换；Open Terminal 0.14.5。历史设计起点和证据保留于下文，不与本轮基线混用。
- 接收角色R3：先更新既有计划，再由R4实现、R5复验；本轮未推进其他对话。ready表示修订设计和测试可交接，不表示性能问题已修复，原R4/R5历史结论保持原样。

## v1.4 R2-PERF-001：后台准备、就绪快路径与分层锁

### 问题与处理结论

已确认59eac68中已有用户仍同步等待全局准备锁，重复读取/刷盘/扫描NSS并执行chmod；异步命令、建终端和端口/代理入口直接调用同步准备，慢初始化会阻塞同worker事件循环。本轮采用用户指定方案，继续保留完整ID归属、短名随机避碰、UID/GID范围、错误响应和R5-001持久化边界。

- 后台入口：新增 `get_filesystem_async(Request) -> UserFS` 骨架，R4用工作线程复用原同步准备与错误转译，接入三个手动准备位置；既有同步依赖、元数据和任务/终端控制策略保持。
- 分层锁：每用户线程/文件锁串行同用户准备；全局锁只做最新清单核验、预留与原子合并提交；慢NSS/系统命令在全局锁外。每次提交核对本用户预期旧记录，并合入最新清单，避免覆盖其他用户提交。
- 就绪快路径：跨实例共享进程内缓存，只缓存已确认持久化且当前服务访问正常的ready绑定。稳定命中不重读整份JSON、不fsync、不全扫NSS、不执行特权命令；版本、配置、NSS/home/权限、PID等变化均触发核验，不能用TTL或旧内存覆盖新证据。
- 持久性：B发布可见但目录fsync失败的新版本时，A旧缓存必须失配；在短全局事务确认新版本前不能成功。正常慢OS准备可独立推进，必要持久化屏障不能被“性能优化”绕过。
- Agent功能：保留所有旧测试和成功协议；新用例实际检查慢准备期间健康、任务列表和已连接PTY的新输入输出仍可完成。无旧目录迁移、配额或命令限制范围扩展。

详细状态、锁顺序、缓存观察边界、NSS回退条件及性能判定见[模块设计v1.4](../../../design/workspace-identity.md)。当前标准镜像的NSS配置实际为 `files systemd`，R4须验证账户来源和变化观察；不可简单禁用整个标准镜像快路径，也不可忽略额外可变来源。

### 本轮产物

| 产物 | 内容 |
| --- | --- |
| [workspace-identity.md](../../../design/workspace-identity.md) v1.4 | 一张UML核心接口图，后台准备、短全局事务、用户锁、快缓存及性能/并发契约。 |
| [dependencies.py](../../../../open_terminal/dependencies.py) | 新异步入口有完整简洁注释，函数体仅NotImplementedError，尚未接线。 |
| [user_isolation.py](../../../../open_terminal/utils/user_isolation.py) | 更新准备器目标契约注释；原业务实现不改。新注释不写需求/验收编号。 |
| [test_agent_responsiveness.py](../../../../tests/req001/test_agent_responsiveness.py) | 6项：4类实际HTTP入口与同worker真实PTY响应，2项新异步接口正确返回/身份错误。 |
| [test_workspace_concurrency.py](../../../../tests/req001/test_workspace_concurrency.py) | 5项：ready/新用户独立进展、同用户等待、跨进程进展和提交合并。 |
| [test_workspace_cache_durability.py](../../../../tests/req001/test_workspace_cache_durability.py) | 3项：另一用户planned/group_ready/ready发布后fsync失败时，已预热用户不能绕过持久化。 |
| [test_workspace_fast_path.py](../../../../tests/req001/test_workspace_fast_path.py) | 4项：100次预热性能观测、坏清单替换失效、home权限变化、fork后独立确认。 |

### 本轮验证证据

所有编辑及Git操作在Windows；WSL Ubuntu-24.04只构建、运行和测试。使用固定REQ-001原基线镜像构建新的独立测试镜像，避免当前部署可能含其他需求的源码影响判定：

```bash
cd ~/projects/webui/open-terminal
docker build --network host \
  --build-arg http_proxy --build-arg https_proxy --build-arg no_proxy \
  --build-arg BASE_IMAGE=webui-lab/open-terminal:req001-r4-base \
  -f tests/req001/Dockerfile -t webui-lab/open-terminal:req001-fix-r2-tests .
docker run --rm --network none webui-lab/open-terminal:req001-fix-r2-tests \
  tests/req001 -q -p no:cacheprovider --tb=short
```

- 最终测试镜像：`sha256:b8a9acd7773cc4038370815151f8e18a947a51a23ca5f4300fea4505239f6699`。基础 `req001-r4-base` 为 `sha256:1603db9c88492ac7d1e224799b3dd3e68692db4d6b2e5d3fb124ad842c9a69dd`。见[构建日志](evidence/r2-perf-001-build.txt)。
- 最终全量 **407项，396 passed、11 failed、6 warnings，98.60秒，退出1**，见[完整输出](evidence/r2-perf-001-pytest.txt)。原有389项全部通过；新增18项中7通过、11失败。没有导入/收集/fixture错误、skip或xfail。
- 11项红基线分类：2项异步骨架未实现；4项实际命令/终端/端口/代理请求阻塞同worker；4项跨用户线程/进程进展被长全局锁阻塞；1项稳定暖路径重复执行重活。这些是R4待实现的目标，不通过改断言放行。
- 7项新增通过：同用户等待不返回半成品1项、跨用户未确认版本持续故障保护3项、坏清单失效/home权限修复/fork独立确认3项。它们保护性能改造不能削弱原归属与持久性。
- 暖路径100次当前测量：p50 **2.639ms**、p95 **3.927ms**；观察到清单open/load各100次、目录fsync100次、passwd全扫200次、group全扫100次、特权命令100次。它说明重复准备仍存在，不是新快路径的性能结果；分位数含本轮观测开销，不当作端到端Agent延迟或跨机器SLA。
- [源码/测试保留校验](evidence/r2-perf-001-source-audit.json)：与59eac68比较，4个业务模块的既有AST在去除docstring和唯一新增异步stub后完全一致；14个原有测试/配置文件内容保持。校验脚本及基线输入均在同一evidence目录，Windows保存、Docker只读解析。
- 新测试先定向运行，修正了测试临时目录父级不可穿越导致命令cd失败的环境问题；最终仅临时目录补search权限并恢复。交叉审阅后将独立进展/watchdog防挂死上限放宽至5秒，并补充io.open/os.open/json.load/loads观测，防止暖路径读取清单绕过计数。以上最终全量输出为当前基线，初轮[定向输出](evidence/r2-perf-001-targeted.txt)只作过程记录。
- 警告为既有Starlette/httpx及真实PTY路径的datetime.utcnow弃用提示，不影响判定。本轮未为此修改依赖或业务代码。

测试容器为一次性root容器，无宿主/生产数据卷挂载、不发布端口、禁用网络，结束自动删除；真实PTY、线程/进程均有清理。未读取部署凭据，未改Open WebUI、部署配置、运行服务或原两个持久卷。`evidence/`已由现有.gitignore忽略，无新增忽略规则。没有Git提交、推送或新建其他角色对话。

### 本轮下一步

**接收角色：R3。处理结论：R2-PERF-001设计与测试ready，业务修复待实现。** 请更新本需求既有`plan.md`与R3交接，安排后台接入、缓存/锁/合并提交、原389项回归及18项新用例，并要求同机新旧暖路径/并发比较；随后由用户指令R4实现和R5复验。不得把旧R5通过结论当作本轮性能契约已验收，也不另起一份重复需求记录。

## v1.3 注释精简（历史记录）

- 修改范围：`identity.py`、`dependencies.py`、`user_isolation.py` 中12处docstring，由211行减为69行；最长的 `WorkspaceProvisioner.resolve` 由57行减为11行。
- 保留职责、输入/输出、身份原样校验、短名规则、UID/GID范围、持久绑定与关键异常。重复的接入流程、状态表、存储细节、概率分析及多个例子由[模块设计](../../../design/workspace-identity.md)承载；具体行为契约不变。
- 修正两处容易误解的表述：构造器只保存配置，验证发生在 `resolve`；随机候选函数只抽取一次，查重和32次重试属于准备器。R4/R5添加的实现和关键行内注释全部保留。
- 验证：[r2-comment-simplification.json](evidence/r2-comment-simplification.json)。Windows侧保存修改前快照，WSL Ubuntu-24.04 中用独立 `python:3.12-slim` Docker只读挂载快照与工作区，以Python标准库解析源码。4个业务文件去掉docstring后的AST与源码文本均一致；`main.py`原始内容也一致。18个测试/配置及R3～R5交接文件的SHA256保持不变。
- 本轮未执行应用代码、未新增或修改测试，未重跑pytest；语法解析及 `git diff --check` 通过。既有业务测试结果引用[R5 v1.3交接](handoff-r5.md)，不当作本轮新测试结果。注释变化会改变原始文件摘要，R4/R5历史快照保留原值。
- 所有编辑在Windows侧完成；当前工作区已有实现与验收改动未提交，本轮未提交、未重启或部署服务。下一步由用户/R0按现有流程决定提交或继续维护，无新增业务决定或接口接入工作。

## v1.2 设计交付（历史记录）

| 产物 | 实际路径与内容 |
| --- | --- |
| 模块设计 v1.2 | [docs/design/workspace-identity.md](../../../design/workspace-identity.md)：架构、身份格式与资源路由清单、8位首选名/随机换名及持久绑定、清单格式、并发状态机、异常和兼容边界。 |
| 跨平台身份契约 | [open_terminal/utils/identity.py](../../../../open_terminal/utils/identity.py)：`WorkspaceAccount`、两类异常、`validate_user_id` 骨架及完整注释。 |
| Linux 准备接口 | [open_terminal/utils/user_isolation.py](../../../../open_terminal/utils/user_isolation.py)：首选名、随机候选名、UID/GID 分配及 `WorkspaceProvisioner` 骨架；旧实现保留，未切换运行路径。 |
| HTTP 身份入口 | [open_terminal/dependencies.py](../../../../open_terminal/dependencies.py)：新增 `require_user_identity` 骨架，尚未接入真实路由。 |
| 黑盒测试 | [tests/req001](../../../../tests/req001)：5 个测试文件、290 个展开用例，覆盖身份值/依赖、真实 HTTP/WS、短名/随机重试及实际哈希碰撞、Linux 准备与恢复。 |
| 最小测试配置 | [conftest.py](../../../../tests/req001/conftest.py)、[Dockerfile](../../../../tests/req001/Dockerfile)：固定合成 key、关闭 Notebook、独立测试镜像。 |
| 运行证据 | [baseline-pytest.txt](baseline-pytest.txt)：最终完整 pytest 标准输出，Windows 侧保存，仅清理行尾空白。 |

所有项目文件均在 Windows 侧编辑。保留用户先前在 `AGENTS.md` 填入的 `req_id/req_dir` 修改，未覆盖。未修改 R1 文档、只读 Open WebUI 文件、部署 `.env`、生产容器或持久卷。

## 本轮设计决定

1. **完整 ID 决定归属。** 原样、大小写敏感，支持 1～128 字符 ASCII 合同；哈希生成候选 Linux 名称，完整 ID 持久记录防止清洗/截断碰撞及同名接管。短 ID 和系统名作为上游标识可用，但映射到独立账号。
2. **最小持久清单。** `/home/.open-terminal/registry.json` 加独立锁文件，版本化原子 JSON。无需数据库、JWT 或账号同步服务。清单随 `/home` 卷保留，无记录旧文件不自动接管。
3. **两池独立分配。** UID/GID 分别在 10001～19999 闭区间按 NSS 与持久预留避号；两池预检后才能变更系统资源。私有组与用户名同名，复用现有 UserFS，不要求 UID=GID。
4. **准备串行、运行复用。** 线程锁加进程文件锁覆盖归属检查、预留、创建与 ready；planned/group_ready/ready 可诊断重试。现存可信区间外账号不改号；账号消失时按新区间恢复原 home。新类通过旧 `resolve_user` 元组签名接入。
5. **资源身份与服务认证分开。** 统一校验资源 HTTP/WS 的用户头；列表不会创建账号。`/system` 等元数据保持无用户上下文兼容，不能把必需用户头塞入所有 `verify_api_key`。端口准备失败不再吞成成功空列表。
6. **只交设计，不实现业务。** 新入口保留 `NotImplementedError`；仅数据结构、异常和构造参数保存是可执行声明。既有业务代码未重写、运行路由未接线。

两项交叉审阅问题已同步修复：GECOS 使用 `ot-` 而非非法的冒号前缀；重试故障注入限定为目标 home 的 chmod 2770，不误拦管理目录准备。

2026-10-28 用户修订已采纳：首选名取完整 ID 的 SHA-256 前8位十六进制，先拼接既有 prefix，再仅对数字开头的最终名称补 `u`；字母 `a`～`f` 开头不补，默认总长8/9字符，合法非空prefix下最长15字符。首次未绑定用户遇到其他记录或系统账号/组/home占用时，最多用 `secrets.token_hex(4)` 随机抽取32个短名候选（重复值也计次），逐个在原全局锁内检查，选中空闲名且两池可用后持久写 planned，再创建账号。连续候选都占用才报 `username_allocation_failed`（503），不新增绑定和系统对象。

planned 以后，任何重试、重启和账号重建都按完整 ID 查记录并沿用已选名称；随机名称无需等于 ID 的首选哈希。schema v1 设计新增 prefix 字段用于配置核验，不保存随机种子。完整ID、完整64位GECOS摘要、UID/GID分配及已有绑定异常拒绝仍保留。首次换名不等于遇到绑定错误就重建空间。1000个用户约0.0116%、9999个约1.157%的概率只描述首选哈希发生重名，需要随机换候选，不能再作为最终开通失败率。

接口注释、首选/随机候选示例、清单示例、故障恢复与并发测试及交接均已同步。真实碰撞样例 `req001-collision-49585` / `req001-collision-89826`（共同前8位 `19042bfe`）现在要求两用户获得不同空间并稳定复用；测试不再把首次碰撞拒绝当作正确结果。已确认上一版有效JSON示例是8位主体，长名文字只见于旧方案说明；本版把历史明确标为已替代，正文JSON直接展示 `u19042bfe` 和 `a7c9e210` 两个短名。仍为R2设计，不实现业务或调整生产账号，也没有旧方案运行数据需要迁移。

## v1.2 验证证据（历史记录）

执行位置：Windows PowerShell 发起 WSL `Ubuntu-24.04`，WSL 工作目录 `~/projects/webui/open-terminal`（对应本项目）。只在 WSL 构建、运行和测试；镜像内 COPY 的源码属于构建输入，没有通过 WSL 修改仓库。

独立测试镜像 `webui-lab/open-terminal:req001-r2-tests`，最终 `docker image inspect .Id`：

```text
sha256:212000941192084c034c41575f9603ddc6004dfea2c2595c0a0197042d6c8c3f
```

构建基础为已有 `webui-lab/open-terminal:local` / `sha256:1603db9c88492ac7d1e224799b3dd3e68692db4d6b2e5d3fb124ad842c9a69dd`，复制当前源码并重新打包安装 0.14.5；不替换部署 tag。运行依赖实测：pytest 9.0.2、FastAPI 0.162.0、Starlette 1.10.0、httpx 0.28.1，Python 3.12 基础镜像。包版本未加业务发布号，不能仅以同为 0.14.5 推断测试镜像与部署内容相同。

WSL 中的复现命令：

```bash
cd ~/projects/webui/open-terminal
docker build --network host \
  --build-arg http_proxy --build-arg https_proxy --build-arg no_proxy \
  -f tests/req001/Dockerfile -t webui-lab/open-terminal:req001-r2-tests .
docker run --rm --network none webui-lab/open-terminal:req001-r2-tests \
  tests/req001 -q -p no:cacheprovider --tb=short
```

代理参数使用已有环境，不写入源码或打印凭据；无需代理时可省略。v1.0 首次两次构建因 PyPI SSL/代理失败，属于环境失败，未计入业务测试；使用现有小写代理变量后构建成功。v1.2 使用上述命令重新构建成功。Dockerfile 将依赖层放在测试 COPY 前，后续测试修订可复用依赖层。

运行容器以 root 使用自己的临时文件系统、禁用网络、无主机目录挂载、无生产数据卷、无发布端口，完成后 `--rm`。测试专用 `REQ001_DISPOSABLE_CONTAINER=1` 限制 OS fixture 误跑宿主；对现存账号创建测试只发生在这个临时容器内。最终检查只剩原两服务容器。

v1.2 最终 pytest（2026-10-28）：**290 项收集并执行，54 passed，236 failed，1 warning，3.91 秒，退出码 1**。

| 分类 | 数量 | 解释 |
| --- | ---: | --- |
| 既有行为通过 | 54 | 服务 key 拒绝、服务/公开元数据、合法身份空列表不准备账号、WS 已有跨用户拒绝等。 |
| 新入口业务未实现 | 141 | 身份值 39、身份依赖 17、准备接口 69、恢复 16；触达约定入口后以 NotImplementedError 失败。深层 OS/恢复、随机换名及碰撞后双方成功的断言尚未执行到。 |
| 真实路由尚未达到新契约 | 95 | 旧缺头400为字符串详情、列表仍返回空/404、非法/重复头未统一拒绝、端口吞错、准备异常未转译（含新增换名失败503）、WS资源查找早于认证。这些是已知基线差距，含新增响应契约，不代表本次引入95个回归。 |
| 导入、收集、fixture/测试环境错误 | 0 | 没有跳过或 xfail，没有把未实现当作成功。 |

警告来自既有 Starlette TestClient 对 httpx 的弃用提示，不影响加载或运行；本次未为消除该提示扩展依赖升级。历史 v1.0 为253项、54通过/199失败；v1.1 为279项、54通过/225失败；v1.2 增加随机候选、重试/恢复/并发和错误转译用例，以上述290项结果作为当前基线。`baseline-pytest.txt` 已替换为本次完整输出。

命名测试中的19个固定样例已用 Windows .NET SHA256 独立核算，覆盖十六种首字符；两条碰撞 ID 也由独立计算找到并核实。这只证明测试输入与期望正确，不代表尚未实现的业务入口已通过。碰撞概率由均匀独立假设下的生日问题公式计算，未当作真实用户数量或实际运行数据。

Windows `git diff --check` 通过。只读复核了本地 Open WebUI v0.12.3 的 HTTP/WS/AI 身份来源；这是源码证据，未做真实 A/B 登录联调。

## v1.2 设计阶段的验证边界（历史记录）

- 本轮没有实现新契约，不宣称 REQ-001 或任一完整 AC 已验收。R4 必须让真实 API 接线及准备/恢复测试通过，不能只实现孤立纯函数。
- 真实 WebUI A/B 账号认证、浏览器伪造身份不可覆盖、界面/响应不交付实例 key、正常 WS/PTY 收发，需要 R4 与 REQ-008 联调。实际部署 WebUI 继续固定 v0.12.3。
- 非 root 服务进程附加组刷新、系统工具超时、锁超时、磁盘满/进程突然终止后的刷盘恢复，仍需实现阶段补充针对性验证；故障注入和状态 fixture 不等同所有崩溃场景通过。
- 保留 home 的账号删除/恢复测试是容器内模拟；真实保留 `/home` 卷重建与升级、文件/个人配置持久性、个人包可运行性分别交由 REQ-007/REQ-005，不能由本次测试替代。
- 文件路径与符号链接全面授权仍属于 REQ-002；本设计只拒绝归属根/home 的冲突，未实现整个文件 API 隔离改造。

## v1.2 当时的下一步（历史记录）

无待人工澄清的业务决定。**请在 R3 独立对话中基于本交接单，为 REQ-001 编写 `<req_dir>/plan.md`**：规划接口实现、真实路由接线、Linux 准备/恢复与现有功能回归，保留本文件的红基线及集成验证边界。若 R3 发现设计问题，在该需求的交接记录中给出编号后回交 R2；不直接在本轮推进 R4 实现或部署。

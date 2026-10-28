# Info For Skills

- project_root：D:\Documents\synowave\WebUI\open-terminal
- work_id：opt-user-space
- work_dir：D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space
- req_id：REQ-001
- req_dir：D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\REQ-001
- design_dir：D:\Documents\synowave\WebUI\open-terminal\docs\design
- requirements_path：D:\Documents\synowave\WebUI\open-terminal\docs\dev-workflow\opt-user-space\requirements.md

# Rules

- WSL 的 Ubuntu 系统仅用于构建、运行和测试，禁止在其中修改任何代码或执行 Git 提交；通过 `/mnt/d` 或软链接访问时也适用此限制。如需修改或提交本项目代码，应在 Windows 侧进行。
- Open WebUI 仓库 `D:\Documents\synowave\WebUI\open-webui` 仅作为只读联调依赖，禁止改动其中的文件或提交变更。

# Tests

- 测试环境：WSL Ubuntu 中使用 Docker，Open WebUI 固定为 `v0.12.3`。
- 当前 Open Terminal 源码版本：`v0.14.5`。源码同步后需重新构建镜像，运行容器版本以实际构建结果为准。
- 测试工作根目录：`~/projects/webui`，对应 Windows 的 `D:\Documents\synowave\WebUI`。
- Open Terminal 构建和测试目录：`~/projects/webui/open-terminal`。
- Open WebUI 联调依赖目录（只读）：`~/projects/webui/open-webui`。
- 部署目录：`~/projects/deploy/webui`，包含 `compose.yaml`、本地 `.env` 及构建日志。
    - `.env` 中的 `WEBUI_ADMIN_EMAIL`、`WEBUI_ADMIN_PASSWORD` 为管理员账号密码；按需读取。
    - 修改 `.env` 后使用 `docker compose up -d` 加载新配置，单独执行 `docker compose restart` 不会更新容器环境变量。
    - 持久卷为 `webui-lab-open-webui-data`（容器内 `/app/backend/data`）和 `webui-lab-open-terminal-data`（容器内 `/home`）；保留这两个卷以保留账号及用户工作空间。

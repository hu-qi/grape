# GitLab / Grape 双 ECS 隔离验收环境

本目录提供部署模板及只读预检工具。资源创建须先明确区域、域名、Agent/模型、镜像 digest、规格和费用；模板生成与预检均不创建云资源。新实例登记为 `gitlab@lab`，用于自建 GitLab 的可控端到端验收，不替代原企业实例的网络、证书、版本和权限验收。

## 1. 资源与网络清单

| 资源 | 验收规划 | 用途 |
| --- | --- | --- |
| ECS GitLab | Linux x86_64，8 vCPU / 16 GiB，SSD 100 GiB 起 | 独立 GitLab，内置 PostgreSQL/Redis，配置/日志/仓库持久化 |
| ECS Grape | Linux x86_64，4 vCPU / 8 GiB，SSD 100 GiB 起 | Grape、实际 Agent、独立 MySQL 8.0 |
| VPC / 子网 | 同一区域；使用明确选定的现有网络或新建验收网络 | 两台 ECS 私网互通；不与公司网段冲突 |
| 安全组 | GitLab 与 Grape 分组 | 按下表限制入站，不修改业务安全组 |
| EIP | Grape 需要公网可达入口；GitLab 的 EIP 按管理访问需要决定 | GitLab.com 回调 Grape；GitLab 可以仅通过私网/VPN管理 |
| 域名 / TLS | 两个独立域名和有效证书 | GitLab URL、Grape server_base_url 与证书 SAN 一致 |
| SWR 私有仓库 | 与 ECS 同区域 | 托管 Grape 运行镜像；GitLab/Caddy/MySQL 使用明确 digest |

上述规格是验收起点，未执行负载测试。若升级 EVS、增加 EIP/VPN/RDS/Runner，需要单独纳入资源及费用清单。本轮无需 CCE、ELB、RDS 或 GitLab Runner：验证的是 Grape Agent，不涉及 GitLab CI 作业；后续测 Pipeline 时再增加 Runner。

| 目标 | 入站 | 来源 |
| --- | --- | --- |
| GitLab ECS | TCP 443 | Grape ECS 私网地址，以及明确的管理网段/VPN |
| Grape ECS | TCP 443 | 自建 GitLab 私网地址、管理来源、GitLab.com Webhook 所需公网来源 |
| 两台 ECS | TCP 22 | 明确的运维出口 /32 或堡垒机 |
| Grape/MySQL | 3306 不发布 | 仅 Compose internal 数据库网络 |
| Grape 后端 | 3000 不发布 | 仅同机 ingress 容器 |
| GitLab HTTP | 80 不发布 | 仅同机 ingress 容器 |

所有外部入口使用 HTTPS；不发布 Git SSH 端口，本次 Git clone/push 使用 HTTPS。若 Grape 接收 GitLab.com 回调，应按 GitLab 当前官方出口策略配置；无法稳定限制来源时，仅把 HTTPS 作为公共服务入口，由应用验证认证和独立 Webhook 密钥。绝不把 22/3306/3000 全网开放。

出站需允许 DNS/NTP、镜像仓库、GitLab API/Git HTTPS、Agent 模型/登录/下载端点。使用私网 IP 访问对方时仍用域名解析和可信 TLS。GitLab 项目 Webhook 默认限制内网目标，需在管理员出站策略中允许 Grape 的精确主机/IP，并验证重投。仅配置 VPC 互通不会自动解除该限制。

## 2. 准备镜像与 Agent

所有部署镜像均用 `repository@sha256:...`。选择受维护的 GitLab 明确版本、MySQL 8.0、Caddy 2，并解析其实际 digest；模板不默认拉取 latest。CPU 架构统一使用 linux/amd64。

`grape.Dockerfile` 包含 Python、Node、Git、CA 和 SSH 客户端，以 UID/GID 10001 运行应用。构建时明确选择 Debian bookworm 系列的 Python/Node 镜像及 digest，避免跨发行版复制 Node 二进制。可通过 `AGENT_NPM_PACKAGE` 安装显式版本的 npm Agent；Python/独立二进制 Agent 需使用经审查的派生镜像。引擎和模型必须明确选择，不会自动替换。OpenCode 当前验收版本为 `opencode-ai@1.19.5`；Grape 使用该版本支持的 `--auto` 参数。

另行取得经过审查的独立 Grape CLI wheel，放入 `deploy/gitlab-lab/cli-artifacts/`（该目录的 wheel 已被 Git 忽略），记录其来源提交和 SHA256。构建要求目录中恰好一个 wheel，并通过 `GRAPE_CLI_WHEEL_SHA256` 校验后安装到独立虚拟环境。CLI 与服务端都使用顶层 `src` 包；镜像内的 `grape` 启动器使用 Python 隔离模式，避免工作目录或 `PYTHONPATH` 导致导入错误。不要从公共 PyPI 上按同名包猜测安装，也不要在镜像构建中传入私有仓库凭据。

独立 CLI 0.3.3（源码提交 `7eb77b906cff9e9bcd5ee048b756070609e91b73`）的 `grape git` 仅识别 GitCode。使用该基线时，先在独立源码工作副本用 `git apply --check --unidiff-zero` 检查本目录的 `grape-cli-gitlab.patch`，再用 `git apply --unidiff-zero` 应用、运行其完整测试并构建 wheel。补丁保留 GitLab 子组路径，并按服务端返回的 `clone_url` 限定认证头；包含真实 Git URL 匹配测试，验证其他主机、仓库或 HTTP 请求不会取得认证头。不要直接使用未修补的 0.3.3 验证私有仓库推送。补丁尚需独立 CLI 上游接纳；不要将其 wheel 或私有源码提交到本仓库。

从仓库根目录构建，以下变量都应来自本地配置，不含登录凭据：

```sh
docker build --platform linux/amd64 \
  -f deploy/gitlab-lab/grape.Dockerfile \
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE:?digest required}" \
  --build-arg "NODE_IMAGE=${NODE_IMAGE:?digest required}" \
  --build-arg "GRAPE_CLI_WHEEL_SHA256=${GRAPE_CLI_WHEEL_SHA256:?reviewed wheel digest required}" \
  --build-arg "AGENT_NPM_PACKAGE=${AGENT_NPM_PACKAGE:-}" \
  --build-arg "VCS_REF=$(git rev-parse HEAD)" \
  -t "${GRAPE_TAG:?SWR image tag required}" .
# 按 SWR 官方方式用 password-stdin 登录，禁止把密码放进 argv。
docker push "${GRAPE_TAG:?}"
```

记录本次实际推送的仓库 digest，并用其填写 inventory 的 Grape image。构建前确认工作区干净，`VCS_REF` 必须对应构建内容。构建上下文排除了 `.lab-local/`；真实凭据、证书和验收产物只能放到该目录或仓库外。

## 3. 生成本地部署包

先在本地建立 `.lab-local/`，复制 `inventory.example.json` 为 `.lab-local/inventory.json`，填入真实的区域、两个域名、Agent 引擎/模型、完整提交 SHA 和四个镜像 digest。示例中的 REQUIRED 必须替换。

```sh
python3 deploy/gitlab-lab/labctl.py prepare \
  --inventory .lab-local/inventory.json \
  --directory .lab-local/rendered
```

工具只生成本地文件，独占创建，第二次运行不会覆盖/轮换已有密码。生成目录权限 0700，密钥文件 0600。GitLab root 密码、Grape 管理员初始密码、JWT、凭据加密密钥、MySQL 密码分别生成，不复制原系统凭据。GitLab root 密码仅在新数据库初始化时生效。选择 OpenCode 时生成不含密钥的 opencode.json，主模型与 small_model 均锁定到指定模型；在本机私密的 `grape/engine.env` 中注入 `LAB_MODEL_API_KEY` 和 `LAB_MODEL_BASE_URL`，不要用 Docker build-arg 传入密钥。模型网关优先使用可信 HTTPS 或已验证的私网路径；公网 HTTP 会明文传输认证头。

- `.lab-local/rendered/gitlab`：只分发到 GitLab ECS。
- `.lab-local/rendered/grape`：只分发到 Grape ECS。
- `inventory.json`：本地保存，不提交 PR、不放入镜像。

分别把匹配域名的 `fullchain.pem` / `privkey.pem` 放到各角色的 `tls/`。默认使用预先签发的可信证书；如使用私有 CA，需在 GitLab、Grape 和 Agent Git 传输中配置同一信任链，不关闭校验。Grape 的 `ca_bundle` 需要显式挂载证书路径；探测工具支持 `--ca-bundle`。

如果使用独立数据盘，应在全新的验收主机上先按已记录的 EVS ID 校验并挂载，再把 Docker data-root 指向该盘（例如 /srv/docker）；否则具名卷默认仍落在系统盘。不要格式化任何未核实归属的设备。

远程主机需预装 Docker Engine 与 Compose。用受控 SSH/SCP 分发到 `/srv/grape-gitlab-lab`，两台主机分别只接收各自角色目录；禁止把整个含密钥的包上传到 SWR 或 Git。

Grape 配置 bind mount 必须能由 UID/GID 10001 读取。仅在 Grape 验收主机执行：

```sh
sudo chown -R 10001:10001 /srv/grape-gitlab-lab/configs
cd /srv/grape-gitlab-lab
sudo docker compose config --quiet
sudo docker compose up -d
sudo docker compose ps
```

GitLab 主机也在自己的角色目录执行最后四条 Compose 命令，不执行 Grape 配置 chown。`config --quiet` 仅验证，不打印展开后的密码。严禁使用 `config` 或 `docker inspect` 全量输出收集证据。

数据使用具名卷保存，普通容器重建不删除数据。首次启动不要用 `down -v`；清理必须列出当前 deployment_id 对应的资源和数据卷后单独处理。

## 4. 服务初始化与预检

这是一套隔离的 staging 环境，`security.json` 明确设为 staging；生产门禁、邮件与 OAuth 登录配置需在生产验收中单独完成。稳定保留 security.json 与数据卷，否则旧 PAT 可能无法解密。

1. 验证 GitLab ready，关闭公开注册，创建专用验收用户和测试组。不要导入业务仓库或复用原企业 PAT。
2. 登录 Grape 管理员，确认 `server_base_url` 为 Grape HTTPS 域名；生成器已写入 `configs/server-config.json`。用界面配置实际 Agent 引擎、模型、认证和场景。仅安装 CLI 不等于已配置可用 Agent。
3. 在 Grape 个人设置绑定 lab 用户 PAT（provider `gitlab@lab`）；如验 GitLab.com，使用独立测试项目及对应凭据。
4. 使用新建的验收项目测试托管，确认 Hook URL 和独立 secret，由服务自动管理。

可以先从 Grape ECS 执行只读预检（不得用 curl -k）：

```sh
python3 deploy/gitlab-lab/labctl.py probe \
  --grape-url https://grape-lab.example.com \
  --gitlab-url https://gitlab-lab.example.com \
  --output .lab-local/preflight.json
```

返回值包含健康、Grape 对外基址、Agent online 检查；失败退出码为 1。可通过本地环境 `GITLAB_LAB_TOKEN` / `GRAPE_LAB_API_KEY` 增加认证只读检查，使用终端安全输入，禁止出现在命令行和聊天。API Key 必须是 Grape 用户级 Key，不能用任务 session Token 管理连接。脚本不输出响应正文和异常详情，不跟随重定向。`full_e2e_verified` 始终为 false，明确表示该脚本只做预检。

同一 VPC 不会自动让公网域名走私网。分配 ECS 私网 IP 后，在 GitLab/Grape 的 Compose override 中为调用方容器配置对方域名的 extra_hosts（或使用明确的私有 DNS），保持域名和证书一致；未配置时须按实际公网源 IP 检查安全组。

使用私网的 GitLab ECS 到 Grape 的回调，应再从 GitLab 管理界面发起真实 Hook 测试；从操作员电脑探测成功不能证明这一方向连通。

## 5. 真实 Agent 验收与证据

| 检查 | 操作 / 命令 | 通过标准 | 失败处理 / 证据 |
| --- | --- | --- | --- |
| 部署身份 | 在 Grape 主机运行 `docker compose images`；只读查询容器镜像的 RepoDigests 与 OCI revision label | digest、SHA 与 inventory 一致 | 不一致先停止验收并重新部署；保存仅含 digest/SHA 的证据 |
| 数据库就绪 | `docker compose exec -T grape python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:3000/readyz', timeout=5).status)"` | 返回 200 | 检查 DB/迁移与卷权限；不要以 /api/health 替代 DB readiness |
| API/Git 适配 | 在 Grape 运行环境中，按接入说明设置 `gitlab@lab` token_env，运行 `scripts/verify_gitlab.py --provider gitlab@lab --write --output <本地证据路径>` | 临时项目 Issue/评论、clone/push、MR 创建/合并通过 | 脚本仅删除本次创建项目；保留脱敏 JSON；不代表 Agent 完成 |
| 真实任务 | 在已托管测试项目创建唯一 `grape-lab-<nonce>` Issue，明确要求 Agent 新建 `lab-probe.txt`、独立分支和 MR | GitLab 的真实投递对应 Grape event_seq → task_id → worker → commit SHA → MR，文件内容和目标分支符合要求 | 根据事件/任务状态定位，不以截图或内部 API 200 判成功；保存上述关联 ID |
| 重复投递 | 对已处理投递在 GitLab Recent events 执行 Resend | 同 UUID 无第二个任务/重复评论；收件状态可解释 | 同时核对业务幂等，不只看 HTTP duplicate |
| 待处理重启恢复 | 在隔离环境中暂停 worker 派发窗口或注入受控故障，确认事件已 pending/processing 后重启 Grape | 同一事件恢复；processing 租约到期后可领取，后续形成真实任务/MR | 记录时间与收件状态；单纯 restart 后健康通过不算恢复通过 |
| 派发重试 | 使用受控、可恢复的派发异常；解除后观察恢复 | 退避最多三次，耗尽 failed；手动重发可恢复 | 单独区分派发异常与 Agent 业务失败，后者通过事件管理处理 |
| 持久化 | 正常重建 Grape 容器，再登录并读取原任务、PAT 绑定和工作目录 | 记录/密钥/仓库工作区仍有效 | 检查挂载，禁止生成新 security.json 覆盖旧配置 |

重启/异常注入尚未提供一键工具，因为它必须绑定实际的 worker、任务和事件，避免把空队列重启冒充恢复验收。上述步骤应在专用验收项目执行，不以原企业业务项目作为破坏性测试对象。

## 6. DevKit 云资源落地门槛

先查域名的公网权威 NS；在华为云能查到同名 Zone 不代表该 Zone 已被委派。DNS 在其他提供商时，应向实际权威提供商添加精确的 A/TXT 记录，不修改无效 Zone 或根域委派。中国大陆部署还需先确认域名备案/接入条件；非大陆区域应重新询价，不能沿用大陆报价。

用 DevKit 的脱敏 profile 和只读身份接口确认账号，再在用户指定区域查询网络、镜像、规格、配额和现有同名资源。不要跨区域盲扫。真实云资源 ID、域名和费用结果只保存在 `.lab-local/` 或当前会话状态文件中。

创建前需有可审查的清单：两台 ECS 的规格/AZ/镜像/盘、VPC/子网/安全组来源、EIP 数量、SWR 组织/仓库、域名及证书方案、真实模型费用，并核对现金余额和欠费。用户确认清单后再通过 DevKit plan/approved-command 执行，逐项记录资源 ID。若中途失败，报告已创建项，不自动删除或修改业务资源。

ECS 关机不等于停止全部费用；EVS、EIP、备份与镜像存储需要分别核对。验收结束时以 deployment_id 逐项列出需保留或释放的资源，不能按名称模糊批量删除。

参考：[GitLab Docker](https://docs.gitlab.com/install/docker/installation/)、[GitLab 资源要求](https://docs.gitlab.com/install/requirements/)、[GitLab 出站过滤](https://docs.gitlab.com/security/webhooks/)、[SWR 内网](https://support.huaweicloud.com/bestpractice-swr/swr_bestpractice_0007.html)、[HuaweiCloud DevKit](https://github.com/huaweicloud/huaweicloud-devkit)。

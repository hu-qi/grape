# GitLab.com 与私有化 GitLab 接入

Grape 可通过 Personal Access Token（PAT）接入 GitLab.com 与多个 GitLab Self-Managed 实例。先登录已有 Grape 账号，再在 **个人设置 → GitLab** 绑定令牌。这里绑定的是仓库访问凭据，不会增加一个 GitLab OAuth 登录方式。

## 1. 配置实例

GitLab.com 默认启用，provider 为 `gitlab`。私有实例由服务端环境变量登记；JSON 中只放地址与环境变量名，不放 Token：

```sh
export GRAPE_GITLAB_INSTANCES='{"corp":{"url":"https://gitlab.example.com","token_env":"CORP_GITLAB_TOKEN"}}'
```

私有实例 provider 为 `gitlab@corp`。实例名使用小写字母开头，后续可包含小写字母、数字、`_`、`-`，最长 40 个字符。支持多个实例、非默认 HTTPS 端口和子路径，例如 `https://gitlab.example.com:8443/gitlab`。

企业 CA 可按实例配置：

```sh
export GRAPE_GITLAB_INSTANCES='{"corp":{"url":"https://gitlab.example.com","ca_bundle":"/etc/ssl/certs/company-ca.pem"}}'
```

必须使用 HTTPS。API 和 Git 传输验证证书，不跟随认证请求重定向；不能通过关闭证书校验绕过企业 CA 配置。配置应同步到需要访问仓库的服务器和 worker。GitLab API 需要保留项目路径中的 `%2F`，反向代理不能提前解码该路径。

可选 `token_env` 提供该实例的全局 bot Token；GitLab.com 默认从 `GITLAB_TOKEN` 读取。推荐通过界面绑定用户 PAT，按需配置仓库 bot。生产环境使用项目现有的凭据加密密钥配置，绑定的用户 PAT 和仓库 bot Token 经现有 AES-GCM 凭据模块加密存储。

## 2. 绑定与托管

1. 重启 Grape 服务，打开 **个人设置 → GitLab**（未绑定平台的新手引导和项目页也提供 GitLab 入口）。
2. 选择 GitLab.com 或管理员配置的私有地址，输入 PAT，点击“验证并绑定”。成功后输入框清空；前端不缓存 Token。
3. 点击“加载项目”。成员项目分页查询；项目/组继承权限达到 Maintainer（40）时可托管。
4. 配置 Grape 的 `server_base_url` 为 GitLab 实例能够访问的 HTTPS 服务地址，再点击“托管”。Grape 创建或更新自己的 Webhook，不改动其他 Hook。配置期间仅预留托管权限；远端设置成功后才保存托管记录。配置失败可重试，已有托管关系保留。
5. 进入项目后使用现有场景、任务和 Agent。项目设置中可更新该仓库的 Bot Token，或解除托管。

PAT 需要 `api`；HTTPS Git 操作需要仓库读写权限。令牌 scope 不等同于具体项目权限：创建项目还受实例/命名空间策略限制，保护分支和 MR 审批规则仍由 GitLab 执行。PAT 过期后在同一连接中重新绑定，不会自动续期。

实例和项目标识始终保留：

```text
gitlab:team/project
gitlab@corp:team/subgroup/project
```

不同实例中的同名项目使用独立凭据、任务记录与工作目录。GitLab 项目路径不回退到 GitCode/GitHub；解除托管仅清理该实例的记录和工作目录。

用户托管后可使用个人 PAT 执行任务。配置仓库 Bot Token 后优先使用该 bot；清空 bot 配置后回退到同实例的托管用户凭据。执行 token 标记过期后停止使用，重新绑定恢复。Webhooks 使用独立随机密钥，更新 PAT 不会使 Hook 密钥失效。

## 3. 接口

下列用户操作使用现有 Grape JWT 或用户 API Key，**不使用 GitLab PAT 作为 Grape 登录凭据**。Token 仅在连接/仓库 bot 配置请求的 JSON 字段传入，响应不回显。

| 方法与路径 | 内容 |
|---|---|
| `GET /api/gitlab/instances` | 已配置地址与当前用户的绑定状态 |
| `PUT /api/gitlab/connection` | `{provider, token}`，验证并加密保存 PAT |
| `DELETE /api/gitlab/connection?provider=gitlab@corp` | 解除绑定；仍有该用户托管项目时返回 409 |
| `GET /api/gitlab/projects?provider=gitlab@corp` | 当前用户可见的成员项目及托管权限 |
| `POST /api/gitlab/host` | `{provider, project: "team/subgroup/project"}` |
| `POST /api/gitlab/remove` | 同上，删除 Grape 的 Hook 与本地托管数据，不删除 GitLab 项目 |
| `PUT /api/gitlab/bot-token` | `{provider, project, token}`；空字符串清除仓库 bot |
| `GET /api/repos/member?provider=gitlab@corp` | 统一项目列表，支持实例筛选 |
| `POST /api/repos/sync` | 同步所有已绑定平台，按实例缓存 |
| `GET /api/repos/check-name?provider=gitlab@corp&name=demo` | 检查个人命名空间同名项目 |
| `POST /api/repos/create` | `provider/name/description/visibility/org/auto_init/default_branch/host_after_create`；`org` 为可选 GitLab 组路径 |

Agent 使用已有的仓库会话 Token（`tk_`）访问统一接口，平台与实例由会话的 `repo_key` 决定，调用方不能用 query/header 切换实例。下列接口支持多级 namespace：

```text
/api/git-repo/team/subgroup/project/issues
/api/git-repo/team/subgroup/project/issues/7/comments
/api/git-repo/team/subgroup/project/pulls
/api/git-repo/team/subgroup/project/pulls/9/merge
/api/repos/team/subgroup/project/git-credential
/api/repos/team/subgroup/project/workspace
```

`/api/git-repo` 支持仓库信息、分支、提交、目录树、文件内容、标签、里程碑、Issue 和 MR 的现有白名单操作。GitLab 的 `iid/description/web_url/source_branch/target_branch` 转换为既有契约的 `number/body/html_url/head.ref/base.ref`。MR 使用 `source_branch/target_branch` 创建；更新操作使用 GitLab 的 PUT，评论使用 Notes API。

多级路径也支持显式 `/-/` 分隔，例如 `/api/git-repo/team/subgroup/project/-/issues/7`。后台项目视图的路径可以在 owner 前保留 provider（例如 `gitlab@corp:team/subgroup`），使同名仓库的工作台查询保持隔离。

## 4. Webhook

```text
GitLab.com:  https://grape.example.com/webhook/gitlab/default
私有 corp:  https://grape.example.com/webhook/gitlab/corp
兼容入口:   https://grape.example.com/webhook/gitlab
```

托管操作自动配置 Issue、MR、Note、Push、Tag、Pipeline、Release 事件。接收端核对项目实例地址和独立随机 `X-Gitlab-Token`，用常量时间比较验证密钥，按实例、项目与 `X-Gitlab-Event-UUID` 拦截重复投递。验证后的事件先写入持久化收件表，再返回成功；存储失败返回 503。后台领取带 60 秒租约的事件，每 10 秒续期，进程退出后可回收过期租约。派发异常按退避策略最多尝试三次；耗尽后保留 failed 状态，可在 GitLab 手动重发同一投递重新排队。正常返回的投递 ID 保留 24 小时，业务过滤或失败状态通过 Grape 事件管理处理。投递语义为至少一次：崩溃发生在业务处理成功与收件表确认之间时可能重复执行，业务层仍需保持幂等。收件表不保存认证请求头。内部触发标记不会接受来自外部 payload 的注入。

不能以“API 可以访问”推断 Hook 可回调：GitLab → Grape 和 Grape/worker → GitLab 两个方向都需要实际连通。内网 GitLab 不要求公网暴露，可使用它能访问的内网 HTTPS Grape 地址。

## 5. 可复现验证

无需真实 Token 的测试：

```sh
python3 -m pytest -q tests/test_gitlab_integration.py
python3 -m compileall -q src
node --check src/api/static/js/pages/gitlab.js
```

真实适配器验证脚本只从环境变量读取 Token。用终端安全输入，避免把 Token 写入 shell 历史：

```sh
read -r -s 'GITLAB_TOKEN?GitLab.com Token: '; export GITLAB_TOKEN
read -r -s 'CORP_GITLAB_TOKEN?Private GitLab Token: '; export CORP_GITLAB_TOKEN
python3 scripts/verify_gitlab.py --provider gitlab --provider gitlab@corp
unset GITLAB_TOKEN CORP_GITLAB_TOKEN
```

以上 `read` 示例适用于 macOS zsh。只读模式检查用户、版本、成员项目、仓库元数据、分支、Issue 和 MR 列表。加 `--write` 会新建**临时私有项目**，验证 Issue/评论、Git clone/push、MR 创建/合并及关闭 Issue，最后仅请求删除本次创建的项目。GitLab 若启用延迟删除，`deletion_accepted` 表示删除请求已被接受，不表示已立即物理删除。脚本不修改既有项目，不写入应用数据库，不输出 Token 或 Git 的认证环境。

完整的 GitLab → 已部署 Grape → 真实 Agent → MR 流程需要可达的部署地址、场景和引擎，不能用适配器测试代替。

参考：[GitLab REST API](https://docs.gitlab.com/api/rest/)、[MR API](https://docs.gitlab.com/api/merge_requests/)、[Project Webhooks](https://docs.gitlab.com/api/project_webhooks/)。

专项 CI 为 `.github/workflows/gitlab-integration.yml`，在 PR 上运行隔离的 Python 回归、MySQL 8.0 收件表契约测试、JS 行为测试及语法检查，无需真实 Token。排查投递可检查 `gitlab_webhook_inbox` 的 `status/attempts/updated_at`；`done` 仅表示派发函数正常返回，不代表 Agent 任务成功。失败记录和待处理事件不会按成功投递的 24 小时规则删除。未携带事件 UUID 的投递会生成独立 ID，无法跨请求去重。

独立 GitLab 与 Grape 的 ECS 验收部署模板、网络规则和预检工具见 [双 ECS 验收环境](../../deploy/gitlab-lab/README.md)。

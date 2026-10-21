FROM python:3.12-slim

WORKDIR /app

# 系统依赖——gcc/g++ 编译 C 扩展（bcrypt/cffi 等）+ curl/git（引擎安装与仓库操作）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# 本机直连引擎需要真实 CLI。集群无法稳定访问 GitHub，优先从内网 Gitea 包仓库下载；
# 官方 release 作为外部构建的回退。校验固定版本的摘要，安装失败时让镜像构建失败。
ARG OPENCODE_VERSION=1.19.3
RUN set -eux; \
    case "$(uname -m)" in \
      aarch64|arm64) asset=opencode-linux-arm64.tar.gz; sha=7900818a6a98881edbbadab4b7525940eff3b9750040c200481f9f56cacee405 ;; \
      x86_64|amd64) asset=opencode-linux-x64.tar.gz; sha=13a8fb2978302071b86f9d7e00da9c8895f9cd846978379c3bdc6a5333536c9b ;; \
      *) echo "Unsupported OpenCode architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    mirror="http://172.16.145.202:30344/api/packages/dev/generic/opencode-cli/${OPENCODE_VERSION}/${asset}"; \
    upstream="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${asset}"; \
    curl -fL --retry 2 --connect-timeout 10 --max-time 180 "$mirror" -o /tmp/opencode.tar.gz || \
      curl -fL --retry 2 --connect-timeout 10 --max-time 180 "$upstream" -o /tmp/opencode.tar.gz; \
    echo "$sha  /tmp/opencode.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin opencode; \
    rm /tmp/opencode.tar.gz; \
    opencode --version

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码
COPY . .

# 时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

EXPOSE 3000

# 只启动 3000 主服务——main:app 已包含所有路由（含 webhook + A2A + runtime API）
# 不启动 8080——run_webhook:app 是冗余的，main:app 已注册 webhook_router（main.py 第 119 行）
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers", "--forwarded-allow-ips=*"]

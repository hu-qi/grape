FROM python:3.12-slim

WORKDIR /app

# 系统依赖——gcc/g++ 编译 C 扩展（bcrypt/cffi 等）+ curl/git（引擎安装与仓库操作）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# opencode CLI（本机直连引擎探测需要：find_cli → probe --version）
# 官方安装脚本将二进制放到 /root/.opencode/bin/opencode（os_adapter.find_cli 的 Unix 回退路径覆盖）。
# 容错：内网构建环境若不可达 opencode.ai 则跳过（不阻断流水线），运行期引擎仍可经 PATH/回退路径探测。
RUN (curl -fsSL https://opencode.ai/install | bash) || echo "[docker] opencode install skipped (network unreachable)"
ENV PATH="/root/.opencode/bin:${PATH}"

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

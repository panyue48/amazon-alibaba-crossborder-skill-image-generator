# NanoBanana WebApp 配置与启动（小白版）

本项目提供一个“上传图片 + 中文提示词 → 生成图片”的本地 Web 工具：

- 前端页面：`webapp/static/index.html`（启动后访问 `/`）
- 后端服务：`webapp/app.py`（FastAPI）

本文档只保留**一种**推荐用法：**Conda 创建环境 + `config.local.json` 配置 + 进入 `webapp/` 启动 uvicorn**。

## 1) 一步一步启动（只按这个做）

### 1.1 创建 conda 虚拟环境并安装依赖

在仓库根目录执行：

```powershell
conda create -n nanobanana python=3.10 -y
conda activate nanobanana
pip install -r webapp/requirements.txt
```

> 环境名 `nanobanana` 可自定义，例如你也可以用 `rag_bot`。

### 1.2 创建本地配置文件（以后都改这个）

复制示例到本地配置文件：

```powershell
Copy-Item webapp/config.local.sample.json webapp/config.local.json
notepad .\\webapp\\config.local.json
```

> 注意：`webapp/config.local.json` 已被 `webapp/.gitignore` 忽略，上传 GitHub 时不会提交你的密钥。

至少需要你改两项：

- `grsai_host`：国内直连 `https://grsai.dakka.com.cn` 或海外 `https://grsaiapi.com`
- `default_model`：默认模型（例如 `nano-banana-2`）

API Key 有两种用法（二选一，推荐第二种更贴近产品体验）：

- 写入配置文件：`grsai_api_key`
- 或者启动后在页面里运行时输入 Key（可勾选“记住”）

### 1.3 启动服务

```powershell
cd webapp
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

打开页面：

- `http://127.0.0.1:8000/`

## 2) config.local.json 字段说明

`webapp/config.local.json`（从 `webapp/config.local.sample.json` 复制）：

- `grsai_host`：上游节点（国内/海外）
- `grsai_api_key`：API Key（可选；如果你只用页面运行时输入 Key，可不填或留占位）
- `default_model`：默认模型（页面会自动选中）
- `urls_base64_mode`：`data_url`（默认）或 `plain`
  - 遇到图片参考图导致失败，可尝试改为 `plain`
- `httpx_trust_env`：是否读取系统代理环境变量（默认 `true`）
  - 如果你机器上设置了代理但不可用，报 `ProxyError` 时改成 `false`
- `tls_verify`：是否校验 HTTPS 证书（默认 `true`，不建议关闭）
- `timeout_sec`：上游请求超时秒数（默认 `60`）

## 3) 常见问题（排查顺序）

### 3.1 启动报 `Could not import module "app"`

你多半不在 `webapp/` 目录启动。正确方式：

```powershell
cd webapp
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

### 3.2 提交报 502，detail 里出现 `ProxyError/ConnectError`

优先检查 `webapp/config.local.json`：

```json
{ "httpx_trust_env": false }
```

然后重启 uvicorn 再试。

### 3.3 任务失败：`failure_reason=output_moderatio`

这通常是上游内容合规/审核拦截（可能由提示词或参考图触发），与额度是否充足无直接关系。

建议：

- 换更中性安全的提示词
- 先不上传参考图，仅用提示词生成，确认链路正常后再加图
- 换模型（例如 `nano-banana-fast`）或先用 `1K`

关于“扣费但失败”：

- 平台可能先扣费再异步返还（失败返还/违规返还），建议到 Grsai 控制台日志/消费明细里查看是否有返还记录（可能有延迟）
- 提交阶段就被拦截时，后端可能返回 `HTTP 422`，页面会显示“内容合规拦截”的友好提示

### 3.4 结果 URL 是否会过期？

通常会过期（平台常见策略是临时链接）。建议生成后立刻点“下载”保存到本地或转存到你自己的存储。

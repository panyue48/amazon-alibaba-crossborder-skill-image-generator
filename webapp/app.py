from __future__ import annotations

import asyncio
import base64
import ipaddress
import mimetypes
import os
import socket
import time
import traceback
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from ecommerce_skills import compose_skill_bundle, list_categories, list_creative_modes, list_skillpacks
except ModuleNotFoundError:
    from webapp.ecommerce_skills import compose_skill_bundle, list_categories, list_creative_modes, list_skillpacks

CONFIG_PATHS = [
    os.path.join(os.path.dirname(__file__), "config.local.json"),
]

_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_MTIME: float | None = None
_CONFIG_ERROR: str | None = None


def _read_json_file(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    import json

    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {os.path.basename(path)}: {e.msg} (line {e.lineno}, col {e.colno})") from e


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        v = int(value)
        return v if v > 0 else default
    except Exception:
        return default


def get_runtime_config() -> dict[str, str]:
    """
    运行时配置优先级：
    1) webapp/config.local.json（本地配置文件）
    2) 环境变量（兜底，不在文档中推荐）
    """
    global _CONFIG_CACHE, _CONFIG_MTIME

    chosen_path: str | None = None
    for p in CONFIG_PATHS:
        if os.path.exists(p):
            chosen_path = p
            break

    if chosen_path:
        mtime = os.path.getmtime(chosen_path)
        if _CONFIG_CACHE is None or _CONFIG_MTIME != mtime:
            global _CONFIG_ERROR
            try:
                _CONFIG_CACHE = _read_json_file(chosen_path) or {}
                _CONFIG_ERROR = None
            except Exception as e:
                # 配置文件存在但不可用（例如 JSON 格式错误）时，不应直接把整个服务打挂。
                # 这里回退到 env/default，并把错误暴露给 /api/config 便于 UI 显示。
                _CONFIG_CACHE = {}
                _CONFIG_ERROR = str(e)
            _CONFIG_MTIME = mtime
        raw = _CONFIG_CACHE or {}
    else:
        raw = {}

    grsai_host = (
        str(raw.get("grsai_host") or os.getenv("GRSAI_HOST", "https://grsai.dakka.com.cn"))
        .strip()
        .rstrip("/")
    )
    grsai_api_key = str(raw.get("grsai_api_key") or os.getenv("GRSAI_API_KEY", "")).strip()
    default_model = str(raw.get("default_model") or os.getenv("NANOBANANA_MODEL", "nano-banana-2")).strip()
    urls_base64_mode = str(raw.get("urls_base64_mode") or os.getenv("GRSAI_URLS_BASE64_MODE", "data_url")).strip()
    webhook_url = str(raw.get("webhook_url") or os.getenv("GRSAI_WEBHOOK_URL", "")).strip()
    httpx_trust_env = _as_bool(raw.get("httpx_trust_env"), True)
    tls_verify = _as_bool(raw.get("tls_verify"), True)
    timeout_sec = _as_int(raw.get("timeout_sec"), 60)
    market_insight_ttl_sec = _as_int(raw.get("market_insight_ttl_sec"), 1800)
    market_fetch_timeout_sec = _as_int(raw.get("market_fetch_timeout_sec"), 20)
    market_max_items = _as_int(raw.get("market_max_items"), 6)

    return {
        "grsai_host": grsai_host,
        "grsai_api_key": grsai_api_key,
        "default_model": default_model,
        "urls_base64_mode": urls_base64_mode,
        "webhook_url": webhook_url,
        "httpx_trust_env": str(httpx_trust_env),
        "tls_verify": str(tls_verify),
        "timeout_sec": str(timeout_sec),
        "market_insight_ttl_sec": str(market_insight_ttl_sec),
        "market_fetch_timeout_sec": str(market_fetch_timeout_sec),
        "market_max_items": str(market_max_items),
    }

ALLOWED_MODELS = [
    "nano-banana-2",
    "nano-banana-fast",
    "nano-banana",
    "nano-banana-pro",
    "nano-banana-pro-vt",
    "nano-banana-pro-cl",
    "nano-banana-pro-vip",
    "nano-banana-pro-4k-vip",
]

# 分辨率约束（平台文档描述）
MODEL_IMAGE_SIZES: dict[str, set[str]] = {
    "nano-banana-pro-vip": {"1K", "2K"},
    "nano-banana-pro-4k-vip": {"4K"},
}


app = FastAPI(title="NanoBanana2 Minimal Web", version="0.1.0")
APP_DEBUG = os.getenv("APP_DEBUG", "").strip().lower() in {"1", "true", "yes", "y", "on"}

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception):
    # 避免前端拿到纯文本 "Internal Server Error" 导致 JSON 解析失败。
    # 本项目是本地工具，返回简化错误信息更利于排查；生产环境不建议暴露 traceback。
    payload: dict[str, Any] = {
        "detail": f"Internal Server Error: {type(exc).__name__}: {exc}",
    }
    if APP_DEBUG:
        payload["traceback"] = traceback.format_exc(limit=50)
    return JSONResponse(status_code=500, content=payload)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/config")
def get_config() -> JSONResponse:
    cfg = get_runtime_config()
    default_model = cfg["default_model"] if cfg["default_model"] in ALLOWED_MODELS else "nano-banana-2"
    try:
        source_mtime = os.path.getmtime(__file__)
    except OSError:
        source_mtime = None
    return JSONResponse(
        {
            "grsai_host": cfg["grsai_host"],
            "default_model": default_model,
            "allowed_models": ALLOWED_MODELS,
            "model_image_sizes": {k: sorted(list(v)) for k, v in MODEL_IMAGE_SIZES.items()},
            "allowed_image_sizes": ["1K", "2K", "4K"],
            "config_error": _CONFIG_ERROR,
            "httpx_trust_env": cfg["httpx_trust_env"] == "True",
            "tls_verify": cfg["tls_verify"] == "True",
            "market_insight_ttl_sec": int(cfg["market_insight_ttl_sec"]),
            "market_fetch_timeout_sec": int(cfg["market_fetch_timeout_sec"]),
            "market_max_items": int(cfg["market_max_items"]),
            "server_source_file": __file__,
            "server_source_mtime": source_mtime,
            "allowed_aspect_ratios": [
                "auto",
                "1:1",
                "16:9",
                "9:16",
                "4:3",
                "3:4",
                "3:2",
                "2:3",
                "5:4",
                "4:5",
                "21:9",
            ],
        }
    )


class SkillRefreshRequest(BaseModel):
    skill_id: str
    category_id: str
    creative_mode: str = "hero"
    search_keywords: str = ""
    product_name: str = ""
    selling_points: str = ""
    style_notes: str = ""
    manual_prompt: str = ""
    force_refresh: bool = False


def _compose_skill_bundle_for_request(payload: SkillRefreshRequest) -> dict[str, Any]:
    cfg = get_runtime_config()
    return compose_skill_bundle(
        skill_id=(payload.skill_id or "").strip(),
        category_id=(payload.category_id or "").strip(),
        creative_mode_id=(payload.creative_mode or "").strip() or "hero",
        search_keywords=(payload.search_keywords or "").strip(),
        product_name=(payload.product_name or "").strip(),
        selling_points=(payload.selling_points or "").strip(),
        style_notes=(payload.style_notes or "").strip(),
        manual_prompt=(payload.manual_prompt or "").strip(),
        force_refresh=bool(payload.force_refresh),
        ttl_sec=int(cfg["market_insight_ttl_sec"]),
        timeout_sec=int(cfg["market_fetch_timeout_sec"]),
        max_items=int(cfg["market_max_items"]),
    )


@app.get("/api/skills")
def get_skills() -> JSONResponse:
    return JSONResponse(
        {
            "skills": list_skillpacks(),
            "categories": list_categories(),
            "creative_modes": list_creative_modes(),
        }
    )


@app.post("/api/skills/refresh")
async def refresh_skill(payload: SkillRefreshRequest) -> JSONResponse:
    bundle = await asyncio.to_thread(_compose_skill_bundle_for_request, payload)
    return JSONResponse(bundle)


def _auth_headers() -> dict[str, str]:
    return _auth_headers_with_key(get_runtime_config().get("grsai_api_key", ""))


def _auth_headers_with_key(api_key: str) -> dict[str, str]:
    api_key = (api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Missing API key. Input it in the page, or configure webapp/config.local.json (grsai_api_key).",
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _bytes_to_base64_url(content: bytes, content_type: str | None) -> str:
    b64 = base64.b64encode(content).decode("ascii")
    cfg = get_runtime_config()
    if cfg["urls_base64_mode"] == "plain":
        return b64
    content_type = content_type or "application/octet-stream"
    return f"data:{content_type};base64,{b64}"


def _data_url_to_plain_base64(value: str) -> str:
    s = (value or "").strip()
    if not s.startswith("data:"):
        return s
    parts = s.split(",", 1)
    return parts[1] if len(parts) == 2 else s


def _is_moderation_block(reason: Any, error: Any = None) -> bool:
    text = f"{reason or ''} {error or ''}".strip().lower()
    return any(k in text for k in ["moderatio", "moderation", "violation", "safety", "policy", "blocked"])


def _moderation_user_message(reason: Any, error: Any = None) -> str:
    base = "内容合规拦截：提示词或参考图可能触发上游审核策略。"
    detail = str(reason or error or "").strip()
    if detail:
        return f"{base}（{detail}）\n建议：改成更中性安全的提示词，或更换/裁剪参考图后重试。"
    return f"{base}\n建议：改成更中性安全的提示词，或更换/裁剪参考图后重试。"


async def _read_first_sse_json(resp: httpx.Response, max_seconds: float = 8.0) -> dict[str, Any]:
    """
    Parse `text/event-stream` and return the first JSON payload from `data: ...`.
    """
    import json

    started = time.monotonic()
    async for line in resp.aiter_lines():
        if time.monotonic() - started > max_seconds:
            break
        if not line:
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise HTTPException(status_code=502, detail="Upstream SSE stream did not contain JSON data event in time")


def _normalize_aspect_ratio(value: str) -> str:
    v = (value or "").strip()
    allowed = {
        "auto",
        "1:1",
        "16:9",
        "9:16",
        "4:3",
        "3:4",
        "3:2",
        "2:3",
        "5:4",
        "4:5",
        "21:9",
    }
    return v if v in allowed else "auto"


def _normalize_image_size(value: str) -> str:
    v = (value or "").strip().upper()
    allowed = {"1K", "2K", "4K"}
    return v if v in allowed else "1K"


@app.post("/api/submit")
async def submit_task(
    prompt: str = Form(""),
    aspect_ratio: str = Form("auto"),
    image_size: str = Form("1K"),
    model: str = Form(""),
    skill_id: str = Form(""),
    category_id: str = Form(""),
    creative_mode: str = Form("hero"),
    search_keywords: str = Form(""),
    product_name: str = Form(""),
    selling_points: str = Form(""),
    style_notes: str = Form(""),
    force_refresh: bool = Form(False),
    shut_progress: bool = Form(False),
    files: list[UploadFile] | None = File(None),
    x_grsai_api_key: str | None = Header(default=None, alias="X-Grsai-Api-Key"),
) -> JSONResponse:
    prompt = (prompt or "").strip()
    skill_id = (skill_id or "").strip()
    skill_bundle: dict[str, Any] | None = None

    async def _build_submit_skill_bundle(include_manual_prompt: bool) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                compose_skill_bundle,
                skill_id=skill_id,
                category_id=(category_id or "").strip(),
                creative_mode_id=(creative_mode or "").strip() or "hero",
                search_keywords=(search_keywords or "").strip(),
                product_name=(product_name or "").strip(),
                selling_points=(selling_points or "").strip(),
                style_notes=(style_notes or "").strip(),
                manual_prompt=prompt if include_manual_prompt else "",
                force_refresh=bool(force_refresh) if not include_manual_prompt else False,
                ttl_sec=int(get_runtime_config()["market_insight_ttl_sec"]),
                timeout_sec=int(get_runtime_config()["market_fetch_timeout_sec"]),
                max_items=int(get_runtime_config()["market_max_items"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not prompt and skill_id:
        skill_bundle = await _build_submit_skill_bundle(include_manual_prompt=False)
        prompt = (skill_bundle.get("prompt_preview") or "").strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    urls: list[str] = []
    for f in files or []:
        if not f.filename:
            continue
        try:
            content = await f.read()
            urls.append(_bytes_to_base64_url(content, f.content_type))
        finally:
            try:
                f.file.close()
            except Exception:
                pass

    cfg = get_runtime_config()
    if skill_bundle is None and skill_id:
        skill_bundle = await _build_submit_skill_bundle(include_manual_prompt=True)
    selected_model = (model or cfg["default_model"]).strip() or cfg["default_model"]
    if selected_model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {selected_model}. Allowed: {', '.join(ALLOWED_MODELS)}",
        )

    normalized_image_size = _normalize_image_size(image_size)
    allowed_sizes = MODEL_IMAGE_SIZES.get(selected_model)
    if allowed_sizes is not None and normalized_image_size not in allowed_sizes:
        raise HTTPException(
            status_code=400,
            detail=f"Model {selected_model} only supports imageSize: {', '.join(sorted(allowed_sizes))}",
        )

    payload: dict[str, Any] = {
        "model": selected_model,
        "prompt": prompt,
        "aspectRatio": _normalize_aspect_ratio(aspect_ratio),
        "imageSize": normalized_image_size,
        "urls": urls,
        "shutProgress": bool(shut_progress),
    }
    if cfg["webhook_url"]:
        payload["webHook"] = cfg["webhook_url"]

    upstream_key = (x_grsai_api_key or cfg["grsai_api_key"]).strip()
    url = f"{cfg['grsai_host']}/v1/draw/nano-banana"
    timeout = httpx.Timeout(float(cfg["timeout_sec"]))

    def _submit_response(task_id: str, *, mode: str | None = None, status: Any = None, progress: Any = None) -> JSONResponse:
        body: dict[str, Any] = {
            "task_id": task_id,
            "upstream": {"host": cfg["grsai_host"]},
        }
        if mode:
            body["mode"] = mode
        if status is not None:
            body["status"] = status
        if progress is not None:
            body["progress"] = progress
        if skill_bundle is not None:
            body["skill_bundle"] = skill_bundle
        return JSONResponse(body)

    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=(cfg["httpx_trust_env"] == "True"),
        verify=(cfg["tls_verify"] == "True"),
    ) as client:
        try:
            async with client.stream("POST", url, headers=_auth_headers_with_key(upstream_key), json=payload) as resp:
                content_type = (resp.headers.get("content-type") or "").lower()
                if resp.status_code >= 400:
                    raw_err = (await resp.aread()).decode("utf-8", "ignore")
                    raise HTTPException(status_code=502, detail=f"Upstream HTTP {resp.status_code}: {raw_err[:500]}")

                # SSE 流式进度：data: {...}\n\n
                if "text/event-stream" in content_type:
                    first = await _read_first_sse_json(resp)
                    task_id = first.get("id") or first.get("taskId")
                    if not task_id:
                        raise HTTPException(status_code=502, detail=f"Upstream SSE missing id: {str(first)[:500]}")
                    return _submit_response(
                        task_id,
                        mode="stream",
                        status=first.get("status"),
                        progress=first.get("progress"),
                    )

                raw_text = (await resp.aread()).decode("utf-8", "ignore")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Upstream request error ({type(e).__name__}): {e}") from e

    content_type = content_type if "content_type" in locals() else ""
    try:
        import json

        data = json.loads(raw_text) if raw_text else None
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream returned non-JSON ({content_type or 'unknown content-type'}): {raw_text[:500]}",
        )

    # 有些异常情况下上游会返回 JSON 的 `null`，这里需要做健壮性处理避免 AttributeError。
    if data is None and urls and cfg["urls_base64_mode"] != "plain":
        # 兼容：如果上游不接受 data URL 形式的 base64，这里自动重试一次（改为纯 base64）。
        payload2 = dict(payload)
        payload2["urls"] = [_data_url_to_plain_base64(u) for u in urls]
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=(cfg["httpx_trust_env"] == "True"),
            verify=(cfg["tls_verify"] == "True"),
        ) as client:
            try:
                async with client.stream("POST", url, headers=_auth_headers_with_key(upstream_key), json=payload2) as resp2:
                    ct2 = (resp2.headers.get("content-type") or "").lower()
                    if resp2.status_code >= 400:
                        raw2 = (await resp2.aread()).decode("utf-8", "ignore")
                        raise HTTPException(status_code=502, detail=f"Upstream retry HTTP {resp2.status_code}: {raw2[:500]}")
                    if "text/event-stream" in ct2:
                        first = await _read_first_sse_json(resp2)
                        task_id = first.get("id") or first.get("taskId")
                        if not task_id:
                            raise HTTPException(status_code=502, detail=f"Upstream retry SSE missing id: {str(first)[:500]}")
                        return _submit_response(
                            task_id,
                            mode="stream",
                            status=first.get("status"),
                            progress=first.get("progress"),
                        )
                    data_raw_text = (await resp2.aread()).decode("utf-8", "ignore")
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Upstream retry error ({type(e).__name__}): {e}") from e
        try:
            import json

            data = json.loads(data_raw_text) if data_raw_text else None
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream retry returned non-JSON ({ct2}): {data_raw_text[:500]}",
            )

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"Unexpected upstream JSON: {raw_text[:500]}")
    data_payload = data.get("data")
    if data_payload is None and urls and cfg["urls_base64_mode"] != "plain":
        # 兼容：上游可能不接受 data URL 形式的 base64，尝试改为纯 base64 再提交一次。
        payload2 = dict(payload)
        payload2["urls"] = [_data_url_to_plain_base64(u) for u in urls]
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=(cfg["httpx_trust_env"] == "True"),
            verify=(cfg["tls_verify"] == "True"),
        ) as client:
            try:
                async with client.stream("POST", url, headers=_auth_headers_with_key(upstream_key), json=payload2) as resp2:
                    ct2 = (resp2.headers.get("content-type") or "").lower()
                    if resp2.status_code >= 400:
                        raw2 = (await resp2.aread()).decode("utf-8", "ignore")
                        raise HTTPException(status_code=502, detail=f"Upstream retry HTTP {resp2.status_code}: {raw2[:500]}")

                    if "text/event-stream" in ct2:
                        first = await _read_first_sse_json(resp2)
                        task_id = first.get("id") or first.get("taskId")
                        if not task_id:
                            raise HTTPException(status_code=502, detail=f"Upstream retry SSE missing id: {str(first)[:500]}")
                        return _submit_response(
                            task_id,
                            mode="stream",
                            status=first.get("status"),
                            progress=first.get("progress"),
                        )

                    raw2_text = (await resp2.aread()).decode("utf-8", "ignore")
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Upstream retry error ({type(e).__name__}): {e}") from e
        try:
            import json

            data = json.loads(raw2_text) if raw2_text else None
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream retry returned non-JSON ({ct2}): {raw2_text[:500]}",
            )
        raw_text = raw2_text
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail=f"Unexpected upstream JSON: {raw_text[:500]}")
        data_payload = data.get("data")

    if data_payload is None:
        upstream_reason = data.get("msg") or data.get("message") or data.get("failure_reason") or data.get("failureReason")
        upstream_error = data.get("error")
        if _is_moderation_block(upstream_reason, upstream_error):
            raise HTTPException(status_code=422, detail=_moderation_user_message(upstream_reason, upstream_error))
        raise HTTPException(status_code=502, detail=f"Upstream returned data=null: {raw_text[:500]}")
    if not isinstance(data_payload, dict):
        data_payload = {}

    task_id = data_payload.get("id") or data_payload.get("taskId") or data.get("taskId") or data.get("id")
    if not task_id:
        raise HTTPException(status_code=502, detail=f"Unexpected upstream response: {str(data)[:500]}")

    return _submit_response(task_id)


@app.get("/api/result/{task_id}")
async def get_result(
    task_id: str,
    x_grsai_api_key: str | None = Header(default=None, alias="X-Grsai-Api-Key"),
) -> JSONResponse:
    task_id = (task_id or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    cfg = get_runtime_config()
    url = f"{cfg['grsai_host']}/v1/draw/result"
    upstream_key = (x_grsai_api_key or cfg["grsai_api_key"]).strip()
    timeout = httpx.Timeout(float(cfg["timeout_sec"]))
    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=(cfg["httpx_trust_env"] == "True"),
        verify=(cfg["tls_verify"] == "True"),
    ) as client:
        try:
            resp = await client.post(url, headers=_auth_headers_with_key(upstream_key), json={"id": task_id})
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Upstream request error ({type(e).__name__}): {e}") from e

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream returned non-JSON ({(resp.headers.get('content-type') or '').lower()}): {resp.text[:500]}",
        )
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"Unexpected upstream JSON: {resp.text[:500]}")

    payload = data.get("data")
    if isinstance(payload, dict):
        status = payload.get("status")
        reason = payload.get("failure_reason") or payload.get("failureReason")
        err = payload.get("error")
        if _is_moderation_block(reason, err) or str(status or "").lower() in {"violation", "blocked", "rejected"}:
            payload["blocked_by_moderation"] = True
            payload["hint"] = _moderation_user_message(reason, err)
            data["data"] = payload

    return JSONResponse(data)


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _assert_safe_download_url(raw_url: str) -> str:
    if not raw_url or len(raw_url) > 2000:
        raise HTTPException(status_code=400, detail="Invalid url")
    p = urlparse(raw_url)
    if p.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http/https allowed")
    if not p.hostname:
        raise HTTPException(status_code=400, detail="Missing hostname")
    hostname = p.hostname
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        raise HTTPException(status_code=400, detail="Cannot resolve hostname")
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            raise HTTPException(status_code=400, detail="Blocked hostname (private IP)")
    return raw_url


def _guess_download_filename(source_url: str, content_type: str) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    ext = mimetypes.guess_extension(ct) if ct else None
    if ext == ".jpe":
        ext = ".jpg"
    if not ext:
        # fallback: try infer from URL path
        try:
            path = urlparse(source_url).path
            _, url_ext = os.path.splitext(path)
            if url_ext and len(url_ext) <= 8:
                ext = url_ext
        except Exception:
            ext = None
    if not ext:
        ext = ".png"
    if not ext.startswith("."):
        ext = "." + ext
    return f"nanobanana-result{ext}"


@app.get("/api/download")
async def download(url: str) -> Response:
    safe_url = _assert_safe_download_url(url)
    cfg = get_runtime_config()
    timeout = httpx.Timeout(float(cfg["timeout_sec"]))
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        trust_env=(cfg["httpx_trust_env"] == "True"),
        verify=(cfg["tls_verify"] == "True"),
    ) as client:
        try:
            r = await client.get(safe_url)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Download error: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Download HTTP {r.status_code}")
    content_type = r.headers.get("content-type", "application/octet-stream")
    filename = _guess_download_filename(safe_url, content_type)
    return Response(
        content=r.content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

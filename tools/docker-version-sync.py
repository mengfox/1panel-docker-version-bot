#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1Panel Docker Version Bot v3.3

目标：
- 默认只跟踪最新版本，不回填历史版本；
- 已存在 2.17 时，不再创建 2.16 / 2.15；
- 当版本目录已存在，但 latest 镜像 digest 变化时，可自动更新已有版本目录中的镜像 digest；
- 支持自动清理旧版本，每个应用默认只保留最新 3 个版本，且保留 latest 模板目录；
- 修复 dry-run 下新建版本不会参与清理预览的问题，避免预览与真实执行结果不一致；
- 清理旧版本时默认只匹配版本目录，避免误删 assets、scripts、images 等非版本目录；
- 支持 GitHub Release / GitHub Tag / Docker Tag / latest digest / GitHub Commit；
- 新增直接读取 compose 镜像状态，减少 state 文件不同步造成的误判；
- dry-run 创建版本时会提前验证模板 compose 是否能匹配镜像；
- 增强布尔值、数字配置解析，避免字符串 false 被当成 True；
- Git push 前 rebase 失败会直接中断，避免冲突状态下继续推送；
- v2.1 增加 HTTP 重试、大小写不敏感版本过滤、Release 到 Tag 回退、state 安全兜底；
- v2.2 完善 GitHub Actions 日志提示：分组、摘要、原因、动作结果和 dry-run 标识更清晰；
- v2.3 修复 V 前缀大小写 alias、清理正则大小写、GitHub 分页请求重试和日志/状态细节；
- v2.6 增加“官方镜像版本为准”策略：允许按 Docker Hub 官方标签降级/修正本地错误版本，并清理非官方版本目录；
- v3.2 增加模板目录自动回退：source_version 指定目录不存在时，可自动使用 latest 或已有最高版本作为模板；
- v3.3 增加安全清理保护：新官方版本未成功创建/不存在时，不清理旧的非官方版本，避免应用被清空；
- v3.3 修复 max_new=0 被非回填模式强制改为 1 的问题，支持只检查/只清理场景；
- 无第三方 Python 依赖，适合 GitHub Actions 直接运行。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_CONFIG = "config/docker-version-sync.json"
SUPPORTED_MODES = {"docker_tag", "github_release", "github_tag", "latest_digest", "github_commit"}
DIGEST_POLICIES = {"skip", "update_existing", "create_digest_version"}


@dataclass(frozen=True)
class ImageRef:
    registry: str
    registry_api: str
    repository: str
    tag: str
    compose_base: str


@dataclass(frozen=True)
class VersionCandidate:
    tag: str
    github_tag: str
    version_value: str
    image_tag: str
    date: str
    digest: str = ""
    digest12: str = ""
    commit: str = ""
    commit8: str = ""
    commit_date: str = ""


@dataclass(frozen=True)
class ReplaceResult:
    matched: bool
    changed: bool



LOG_PREFIX = "[1Panel Version Bot]"


def gha_escape(value: str) -> str:
    """Escape GitHub Actions command text."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def is_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true"


def log(message: str, icon: str = "ℹ️") -> None:
    print(f"{LOG_PREFIX} {icon} {message}", flush=True)


def success(message: str) -> None:
    log(message, "✅")


def skip_log(message: str) -> None:
    log(message, "⏭️")


def action_log(message: str, dry_run: bool = False) -> None:
    log(message, "🧪" if dry_run else "🚀")


def warn(message: str) -> None:
    if is_github_actions():
        print(f"::warning title=1Panel Docker Version Bot::{gha_escape(message)}", flush=True)
    print(f"{LOG_PREFIX} ⚠️ {message}", file=sys.stderr, flush=True)


def die(message: str, code: int = 1) -> None:
    if is_github_actions():
        print(f"::error title=1Panel Docker Version Bot::{gha_escape(message)}", flush=True)
    print(f"{LOG_PREFIX} ❌ {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def section(title: str) -> None:
    print(f"\n{LOG_PREFIX} 🔷 {title}\n" + "-" * 72, flush=True)


def group_start(title: str) -> None:
    if is_github_actions():
        print(f"::group::{gha_escape(title)}", flush=True)
    else:
        section(title)


def group_end() -> None:
    if is_github_actions():
        print("::endgroup::", flush=True)


def format_items(items: Iterable[str], limit: int = 10) -> str:
    values = [str(x) for x in items if str(x)]
    if not values:
        return "无"
    if len(values) <= limit:
        return ", ".join(values)
    return ", ".join(values[:limit]) + f" ... 共 {len(values)} 项"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_yyyymmdd() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")


def run(cmd: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("执行命令：" + " ".join(cmd), "🔧")
    return subprocess.run(cmd, cwd=str(cwd), check=check, text=True)


def run_capture(cmd: List[str], cwd: Path) -> str:
    result = subprocess.run(cmd, cwd=str(cwd), check=True, text=True, capture_output=True)
    return result.stdout.strip()


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists():
        if default is not None:
            return default
        die(f"配置文件不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"JSON 解析失败：{path}，错误：{exc}")
    return {}


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off", ""}:
            return False
    return default


def parse_non_negative_int(value: Any, default: int = 0, field_name: str = "value") -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        die(f"配置错误：{field_name} 必须是非负整数，当前值：{value}")
    if number < 0:
        die(f"配置错误：{field_name} 不能小于 0，当前值：{number}")
    return number


def resolve_config_path(appstore_root: Path, config: str) -> Path:
    p = Path(config)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    return (appstore_root / p).resolve()


def request_raw(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Tuple[bytes, Dict[str, str], int]:
    """带轻量重试的 HTTP 请求。

    只对网络抖动、429、5xx 做重试；401/403/404 这类明确失败直接抛出，
    避免错误凭据或错误地址被长时间重试。
    """
    retry_codes = {429, 500, 502, 503, 504}
    last_exc: Optional[BaseException] = None

    for attempt in range(3):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "1panel-docker-version-bot", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read() if method != "HEAD" else b""
                return data, dict(resp.headers), int(resp.status)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in retry_codes and attempt < 2:
                delay = 2 ** attempt
                warn(f"HTTP {exc.code}，{delay}s 后重试：{url}")
                time.sleep(delay)
                continue
            raise
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < 2:
                delay = 2 ** attempt
                warn(f"网络请求失败，{delay}s 后重试：{url}，错误：{exc}")
                time.sleep(delay)
                continue
            raise

    raise last_exc if last_exc else RuntimeError(f"请求失败：{url}")


def http_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Dict[str, Any] | List[Any]:
    raw, resp_headers, status = request_raw(url, method="GET", headers={"Accept": "application/json", **(headers or {})}, timeout=timeout)
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        die(f"接口返回空内容：{url}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:300].replace("\n", " ")
        die(f"接口返回内容不是 JSON：{url}，HTTP={status}，Content-Type={resp_headers.get('Content-Type', '')}，错误={exc}，预览={preview}")
    return {}


def github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "1panel-docker-version-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GH_API_TOKEN") or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_image_ref(image: str, default_tag: str = "latest") -> ImageRef:
    raw = image.strip().strip("'").strip('"')
    if not raw:
        die("镜像名为空")

    if "@" in raw:
        raw = raw.split("@", 1)[0]

    parts = raw.split("/")
    registry = "docker.io"
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts[0]
        repo_part = "/".join(parts[1:])
    else:
        repo_part = raw

    last = repo_part.rsplit("/", 1)[-1]
    if ":" in last:
        repo_no_tag, tag = repo_part.rsplit(":", 1)
    else:
        repo_no_tag, tag = repo_part, default_tag

    compose_base = raw
    if "@" in compose_base:
        compose_base = compose_base.split("@", 1)[0]
    if ":" in compose_base.rsplit("/", 1)[-1]:
        compose_base = compose_base.rsplit(":", 1)[0]

    registry_api = registry
    if registry in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        registry = "docker.io"
        registry_api = "registry-1.docker.io"
        if "/" not in repo_no_tag:
            repo_no_tag = f"library/{repo_no_tag}"

    return ImageRef(registry=registry, registry_api=registry_api, repository=repo_no_tag, tag=tag, compose_base=compose_base)


def parse_www_authenticate(header: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not header:
        return result
    value = header.strip()
    if value.lower().startswith("bearer "):
        value = value[7:]
    for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', value):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        result[key.strip()] = raw.strip().strip('"')
    return result


def registry_token(registry_api: str, repository: str, www_auth: str) -> Optional[str]:
    auth = parse_www_authenticate(www_auth)
    realm = auth.get("realm")
    service = auth.get("service")
    scope = auth.get("scope") or f"repository:{repository}:pull"
    if not realm:
        return None

    params = {}
    if service:
        params["service"] = service
    if scope:
        params["scope"] = scope

    headers: Dict[str, str] = {}
    username = os.getenv("REGISTRY_USERNAME", "")
    password = os.getenv("REGISTRY_PASSWORD", "")
    if username and password:
        basic = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"

    data = http_json(realm + "?" + urllib.parse.urlencode(params), headers=headers)
    if not isinstance(data, dict):
        return None
    token = data.get("token") or data.get("access_token")
    return str(token) if token else None


def registry_request_with_auth(registry_api: str, repository: str, path: str, method: str, accept: str) -> Tuple[bytes, Dict[str, str], int]:
    url = f"https://{registry_api}/v2/{repository}/{path}"
    try:
        return request_raw(url, method=method, headers={"Accept": accept})
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        token = registry_token(registry_api, repository, exc.headers.get("WWW-Authenticate", ""))
        if not token:
            raise
        return request_raw(url, method=method, headers={"Accept": accept, "Authorization": f"Bearer {token}"})


def registry_v2_json(registry_api: str, repository: str, path: str, accept: str = "application/json") -> Tuple[Dict[str, Any], Dict[str, str]]:
    raw, headers, status = registry_request_with_auth(registry_api=registry_api, repository=repository, path=path, method="GET", accept=accept)
    if not raw:
        return {}, headers
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}, headers
    try:
        return json.loads(text), headers
    except json.JSONDecodeError as exc:
        preview = text[:300].replace("\n", " ")
        die(f"Registry 返回内容不是 JSON：https://{registry_api}/v2/{repository}/{path}，HTTP={status}，错误={exc}，预览={preview}")
    return {}, headers


def dockerhub_tags(repository: str, page_size: int, max_pages: int) -> List[str]:
    repo = repository if "/" in repository else f"library/{repository}"
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size={page_size}"
    tags: List[str] = []
    for _ in range(max_pages):
        data = http_json(url)
        if not isinstance(data, dict):
            break
        for item in data.get("results", []):
            name = item.get("name")
            if name:
                tags.append(str(name))
        next_url = data.get("next")
        if not next_url:
            break
        url = str(next_url)
    return sorted(set(tags))


def registry_v2_tags(registry_api: str, repository: str) -> List[str]:
    data, _ = registry_v2_json(registry_api, repository, "tags/list")
    return sorted(set(str(x) for x in data.get("tags", []) if x))


def fetch_image_tags(image: str, page_size: int, max_pages: int) -> List[str]:
    ref = parse_image_ref(image)
    if ref.registry == "docker.io":
        return dockerhub_tags(ref.repository, page_size, max_pages)
    return registry_v2_tags(ref.registry_api, ref.repository)


MANIFEST_ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.v1+json",
])


def fetch_image_digest(image: str, tag: str = "latest") -> str:
    ref = parse_image_ref(image, default_tag=tag)
    path = f"manifests/{tag}"
    try:
        _, headers, _ = registry_request_with_auth(ref.registry_api, ref.repository, path, "HEAD", MANIFEST_ACCEPT)
        digest = headers.get("Docker-Content-Digest") or headers.get("docker-content-digest") or ""
        if digest.startswith("sha256:"):
            return digest
    except Exception as exc:
        warn(f"HEAD 获取 digest 失败，尝试 GET：{image}:{tag}，错误：{exc}")

    raw, headers, _ = registry_request_with_auth(ref.registry_api, ref.repository, path, "GET", MANIFEST_ACCEPT)
    digest = headers.get("Docker-Content-Digest") or headers.get("docker-content-digest") or ""
    if digest.startswith("sha256:"):
        return digest
    if raw:
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"
    die(f"获取镜像 digest 失败：{image}:{tag}")
    return ""


SEMVER_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(.*)$", re.IGNORECASE)
PRERELEASE_WORDS = ["alpha", "beta", "rc", "dev", "nightly", "snapshot", "preview", "canary", "test"]


def version_sort_key(value: str) -> Tuple[int, Tuple[int, ...], str]:
    """
    版本排序键。

    支持 2 段、3 段、4 段版本号，例如 2.17、3.1.1、1.5.0.0。
    旧版只解析到 major/minor/patch，遇到 XArrPay 这类 1.5.0.0 版本时，
    1.5.0.10 和 1.5.0.2 可能无法准确排序。这里解析全部数字段，
    并补齐长度，保证长期自动更新时不会误判新旧版本。
    """
    raw = str(value or "").strip()
    m = SEMVER_RE.match(raw)
    if not m:
        return (0, tuple([0] * 8), raw.lower())

    number_part = m.group(1) or "0"
    suffix = (m.group(2) or "").lower()
    nums = [int(x) for x in number_part.split(".") if x.isdigit()]
    nums = (nums + [0] * 8)[:8]
    stable = 0 if any(x in suffix or x in raw.lower() for x in PRERELEASE_WORDS) else 1
    return (stable, tuple(nums), raw.lower())


def strip_v_prefix(value: str) -> str:
    return value[1:] if value[:1].lower() == "v" and len(value) > 1 and value[1].isdigit() else value


def regex_matches_any(value: str, patterns: Iterable[str]) -> bool:
    """大小写不敏感匹配目录名。

    清理旧版本时经常遇到上游使用 V2.17 / v2.17 / 2.17 混用，
    这里统一忽略大小写，避免 V 前缀目录被漏清理。
    """
    for pattern in patterns:
        if pattern and re.search(pattern, value, flags=re.IGNORECASE):
            return True
    return False


def canonical_version_name(value: str) -> str:
    """用于 v/V 前缀互认和保护目录互认的规范名。"""
    return strip_v_prefix(str(value)).lower()


def filter_values(values: Iterable[str], include_regex: str, exclude_regex: str) -> List[str]:
    inc = re.compile(include_regex, re.IGNORECASE) if include_regex else None
    exc = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None
    result: List[str] = []
    for value in values:
        if inc and not inc.search(value):
            continue
        if exc and exc.search(value):
            continue
        result.append(value)
    return sorted(set(result), key=version_sort_key, reverse=True)


def github_paginated(url: str, max_pages: int = 5) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    current = url
    for _ in range(max_pages):
        raw_bytes, headers, status = request_raw(current, method="GET", headers=github_headers(), timeout=30)
        raw = raw_bytes.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            preview = raw[:300].replace("\n", " ")
            die(f"GitHub API 返回非 JSON：{current}，HTTP={status}，错误={exc}，预览={preview}")
        if isinstance(data, list):
            results.extend(data)
        elif isinstance(data, dict):
            results.append(data)

        next_url = ""
        for part in headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    next_url = m.group(1)
                    break
        if not next_url:
            break
        current = next_url
    return results


def github_releases(repo: str, include_prerelease: bool, max_pages: int) -> List[str]:
    items = github_paginated(f"https://api.github.com/repos/{repo}/releases?per_page=100", max_pages=max_pages)
    tags: List[str] = []
    for item in items:
        if item.get("draft"):
            continue
        if item.get("prerelease") and not include_prerelease:
            continue
        tag = item.get("tag_name")
        if tag:
            tags.append(str(tag))
    return sorted(set(tags), key=version_sort_key, reverse=True)


def github_versions_from_releases_or_tags(repo: str, include_prerelease: bool, max_pages: int, fallback_to_tags: bool) -> List[str]:
    releases = github_releases(repo, include_prerelease=include_prerelease, max_pages=max_pages)
    if releases or not fallback_to_tags:
        return releases
    warn(f"{repo} 没有可用 Release，已自动回退到 Git Tag 作为版本来源")
    return github_tags(repo, max_pages=max_pages)


def github_tags(repo: str, max_pages: int) -> List[str]:
    items = github_paginated(f"https://api.github.com/repos/{repo}/tags?per_page=100", max_pages=max_pages)
    tags = [str(item.get("name")) for item in items if item.get("name")]
    return sorted(set(tags), key=version_sort_key, reverse=True)


def github_commit(repo: str, branch: str) -> Tuple[str, str]:
    data = http_json(f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(branch)}", headers=github_headers())
    if not isinstance(data, dict):
        die(f"GitHub commit API 返回异常：{repo}@{branch}")
    sha = str(data.get("sha", ""))
    if not sha:
        die(f"无法获取 GitHub commit：{repo}@{branch}")
    date = data.get("commit", {}).get("committer", {}).get("date", "")
    return sha, str(date)


def list_all_version_dirs(app_dir: Path) -> List[str]:
    if not app_dir.exists():
        return []
    return sorted([p.name for p in app_dir.iterdir() if p.is_dir() and not p.name.startswith(".")], key=version_sort_key, reverse=True)


def normalize_string_list(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return list(default or [])


def preserve_versions_for_app(app_cfg: Dict[str, Any], config: Dict[str, Any]) -> Set[str]:
    preserve = set(normalize_string_list(config.get("preserve_versions"), ["latest"]))
    preserve.update(normalize_string_list(app_cfg.get("preserve_versions"), []))

    source_version = str(app_cfg.get("source_version", ""))
    if source_version and source_version != "auto":
        preserve.add(source_version)

    return preserve


def cleanup_include_patterns_for_app(app_cfg: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    """
    旧版本清理必须保守：默认只清理版本号目录，避免误删 assets、scripts、images 等非版本目录。
    可以通过 cleanup_include_regex 显式扩大匹配范围。
    """
    configured = app_cfg.get("cleanup_include_regex", config.get("cleanup_include_regex"))
    if configured:
        return normalize_string_list(configured, [])

    app_include = app_cfg.get("include_regex")
    if app_include:
        return [str(app_include)]

    return [r"^v?\d+(?:\.\d+){1,3}$"]


def comparable_version_dirs_for_app(existing: Iterable[str], app_cfg: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    """返回可比较的版本目录，排除 latest、assets 等保护/非版本目录。"""
    preserve = preserve_versions_for_app(app_cfg, config)
    preserve_canon = {canonical_version_name(name) for name in preserve}
    patterns = cleanup_include_patterns_for_app(app_cfg, config)
    result: List[str] = []
    for name in existing:
        name = str(name)
        if not name or name.startswith("."):
            continue
        if name in preserve or canonical_version_name(name) in preserve_canon:
            continue
        if regex_matches_any(name, patterns):
            result.append(name)
    return sorted(set(result), key=version_sort_key, reverse=True)


def newest_existing_version(existing: Iterable[str], app_cfg: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
    versions = comparable_version_dirs_for_app(existing, app_cfg, config)
    return versions[0] if versions else None


def is_not_newer_than_existing(candidate_version: str, existing_version: str) -> bool:
    """candidate 小于或等于当前最高版本时返回 True，用于避免自动降级创建。"""
    return version_sort_key(candidate_version) <= version_sort_key(existing_version)


def cleanup_old_versions(
    appstore_root: Path,
    app_cfg: Dict[str, Any],
    config: Dict[str, Any],
    dry_run: bool,
    extra_versions: Optional[Iterable[str]] = None,
    exclude_versions: Optional[Iterable[str]] = None,
) -> List[str]:
    app_name = app_cfg["app"]
    app_dir = appstore_root / "apps" / app_name
    if not app_dir.is_dir():
        return []

    keep_latest_versions = parse_non_negative_int(app_cfg.get("keep_latest_versions", config.get("keep_latest_versions", 0)), 0, f"{app_name}.keep_latest_versions")
    cleanup_enabled = parse_bool(app_cfg.get("cleanup_old_versions", config.get("cleanup_old_versions", keep_latest_versions > 0)), keep_latest_versions > 0)
    if not cleanup_enabled or keep_latest_versions <= 0:
        return []

    preserve = preserve_versions_for_app(app_cfg, config)
    cleanup_patterns = cleanup_include_patterns_for_app(app_cfg, config)

    names = set(list_all_version_dirs(app_dir))
    if extra_versions:
        names.update(str(v) for v in extra_versions if str(v))
    if exclude_versions:
        exclude_names = set()
        for item in exclude_versions:
            raw = str(item)
            if not raw:
                continue
            prefix = f"{app_name}/"
            exclude_names.add(raw[len(prefix):] if raw.startswith(prefix) else raw)
        if exclude_names:
            names.difference_update(exclude_names)

    preserve_canon = {canonical_version_name(name) for name in preserve}
    protected = {
        name for name in names
        if name in preserve or canonical_version_name(name) in preserve_canon or name.startswith(".")
    }
    cleanup_candidates = [
        name for name in names
        if name not in protected and regex_matches_any(name, cleanup_patterns)
    ]

    ignored = sorted(names - protected - set(cleanup_candidates))
    if ignored:
        skip_log(f"{app_name} 清理保护：已忽略非版本目录：{', '.join(ignored)}")

    versions = sorted(cleanup_candidates, key=version_sort_key, reverse=True)
    expired_versions = versions[keep_latest_versions:]
    retained_versions = versions[:keep_latest_versions]
    log(
        f"清理策略：保留最新 {keep_latest_versions} 个版本；保护目录={format_items(sorted(preserve))}；"
        f"参与清理版本={format_items(versions)}"
    )

    if not expired_versions:
        success(f"{app_name} 版本保留检查通过：无需清理；当前保留={format_items(retained_versions)}")
        return []

    action_log(
        f"{app_name} 将清理旧版本：{format_items(expired_versions)}；保留={format_items(retained_versions)}",
        dry_run,
    )

    deleted: List[str] = []
    for version in expired_versions:
        path = app_dir / version
        action_log(f"{'预览删除' if dry_run else '删除'}旧版本：{app_name}/{version}", dry_run)
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                warn(f"待删除目录不存在，跳过：{app_name}/{version}")
                continue
        deleted.append(f"{app_name}/{version}")

    return deleted


def official_version_names_from_candidates(candidates: Iterable[VersionCandidate], template: str) -> List[str]:
    """把上游官方候选版本转换为本地版本目录名。"""
    names: List[str] = []
    for candidate in candidates:
        names.append(context_format(template, candidate_context(candidate)))
    return sorted(set(names), key=version_sort_key, reverse=True)




def official_target_available(
    appstore_root: Path,
    app_cfg: Dict[str, Any],
    official_versions: Iterable[str],
    extra_versions: Optional[Iterable[str]] = None,
) -> bool:
    """确认本地或本次计划中至少存在一个官方版本目录。

    开启 official_versions_source_of_truth/prune_unofficial_versions 时，如果候选官方版本
    因模板缺失、镜像不匹配等原因没有创建成功，不能继续删除旧版本。否则可能出现：
    本地只有 1.5.0.0 -> 上游最新 1.5.0.1 -> 创建失败 -> 又把 1.5.0.0 当非官方删掉，
    最终应用目录没有任何可用版本。
    """
    app_name = app_cfg["app"]
    app_dir = appstore_root / "apps" / app_name
    official_canon = {canonical_version_name(v) for v in official_versions if str(v)}
    if not official_canon:
        return False

    available = set(list_all_version_dirs(app_dir))
    if extra_versions:
        for item in extra_versions:
            raw = str(item)
            if not raw:
                continue
            prefix = f"{app_name}/"
            available.add(raw[len(prefix):] if raw.startswith(prefix) else raw)

    available_canon = {canonical_version_name(v) for v in available if str(v)}
    return bool(official_canon & available_canon)


def prune_unofficial_versions(
    appstore_root: Path,
    app_cfg: Dict[str, Any],
    config: Dict[str, Any],
    dry_run: bool,
    official_versions: Iterable[str],
    extra_versions: Optional[Iterable[str]] = None,
) -> List[str]:
    """按上游官方镜像标签清理本地不存在于官方列表中的版本目录。

    适合 mTab 这类本地历史版本可能写高、但 Docker Hub 官方标签较低的场景。
    该逻辑只在存在官方候选版本时运行；无候选版本时不执行，避免接口异常导致误删。
    """
    app_name = app_cfg["app"]
    app_dir = appstore_root / "apps" / app_name
    if not app_dir.is_dir():
        return []

    enabled = parse_bool(
        app_cfg.get("official_versions_source_of_truth", app_cfg.get("prune_unofficial_versions", config.get("official_versions_source_of_truth", False))),
        False,
    )
    if not enabled:
        return []

    official_names = {str(v) for v in official_versions if str(v)}
    if extra_versions:
        official_names.update(str(v) for v in extra_versions if str(v))
    if not official_names:
        skip_log(f"{app_name} 官方版本列表为空：不执行非官方版本清理，避免误删")
        return []

    safe_prune = parse_bool(
        app_cfg.get("safe_prune_requires_official_target", config.get("safe_prune_requires_official_target", True)),
        True,
    )
    if safe_prune and not official_target_available(appstore_root, app_cfg, official_names, extra_versions):
        warn(
            f"{app_name} 未检测到任何已存在或本次已计划创建的官方版本目录；"
            "为避免删除最后一个可用版本，已跳过非官方版本清理"
        )
        return []

    preserve = preserve_versions_for_app(app_cfg, config)
    preserve_canon = {canonical_version_name(name) for name in preserve}
    cleanup_patterns = cleanup_include_patterns_for_app(app_cfg, config)
    official_canon = {canonical_version_name(name) for name in official_names}

    names = set(list_all_version_dirs(app_dir))
    cleanup_candidates = [
        name for name in names
        if name not in preserve
        and canonical_version_name(name) not in preserve_canon
        and not name.startswith(".")
        and regex_matches_any(name, cleanup_patterns)
    ]
    unofficial = sorted(
        [name for name in cleanup_candidates if canonical_version_name(name) not in official_canon],
        key=version_sort_key,
        reverse=True,
    )

    log(
        f"官方版本校验：以镜像仓库标签为准；官方版本={format_items(sorted(official_names, key=version_sort_key, reverse=True))}；"
        f"本地待修正={format_items(unofficial)}",
        "🧾",
    )

    if not unofficial:
        success(f"{app_name} 官方版本校验通过：没有发现非官方版本目录")
        return []

    action_log(f"{app_name} 将清理非官方版本目录：{format_items(unofficial)}", dry_run)
    deleted: List[str] = []
    for version in unofficial:
        path = app_dir / version
        action_log(f"{'预览删除' if dry_run else '删除'}非官方版本：{app_name}/{version}", dry_run)
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                warn(f"待删除目录不存在，跳过：{app_name}/{version}")
                continue
        deleted.append(f"{app_name}/{version}")
    return deleted


def list_template_dirs(app_dir: Path) -> List[str]:
    if not app_dir.exists():
        return []
    versions: List[str] = []
    for item in app_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        has_data = (item / "data.yml").exists() or (item / "data.yaml").exists()
        has_compose = (item / "docker-compose.yml").exists() or (item / "docker-compose.yaml").exists()
        if has_data and has_compose:
            versions.append(item.name)
    return sorted(versions, key=version_sort_key, reverse=True)


def version_aliases(value: str) -> Set[str]:
    """生成 v/V 前缀别名，用于日志和兼容旧目录。"""
    raw = str(value)
    base = strip_v_prefix(raw)
    return {raw, base, f"v{base}", f"V{base}"}


def find_existing_version(existing: Set[str], version_dir_name: str, allow_v_prefix_alias: bool) -> Optional[str]:
    if version_dir_name in existing:
        return version_dir_name
    if allow_v_prefix_alias:
        target = canonical_version_name(version_dir_name)
        matches = {name for name in existing if canonical_version_name(name) == target}
        if matches:
            return sorted(matches, key=version_sort_key, reverse=True)[0]
    return None


def find_existing_version_by_digest(appstore_root: Path, app_cfg: Dict[str, Any], config: Dict[str, Any], existing: Set[str], digest: str, image_tag: str) -> Optional[str]:
    """latest 只有 digest 变化时，优先复用已存在的同 digest 版本目录。

    解决 state 文件丢失后，已存在 20260427-abcdef123456 目录，
    第二天又因同一 digest 生成 20260428-abcdef123456 重复目录的问题。
    """
    if not digest:
        return None
    digest12 = digest.replace("sha256:", "")[:12]
    candidates = comparable_version_dirs_for_app(existing, app_cfg, config)

    # 优先按目录名中的 digest12 匹配，例如 20260427-abcdef123456。
    for name in candidates:
        if digest12 and digest12.lower() in name.lower():
            return name

    # 其次读取 compose，确认是否已经固定到同一个 digest。
    for name in candidates:
        matched, needs_update = existing_version_digest_status(appstore_root, app_cfg, name, image_tag, digest)
        if matched and not needs_update:
            return name

    return None


def source_version_candidates_for_app(app_cfg: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    """
    生成模板目录选择顺序。

    约定：
    - 具体目录名：优先使用该目录，例如 latest、2.9.5、1.5.0.0；
    - auto：从所有可用模板目录中选择语义化版本最高的目录。

    默认顺序是 latest -> auto。这样有 latest 模板时优先使用模板，
    没有 latest 时自动使用现有最高版本目录作为模板。
    """
    raw = app_cfg.get("source_version_candidates", config.get("source_version_candidates", ["latest", "auto"]))
    candidates = normalize_string_list(raw, ["latest", "auto"])
    if not candidates:
        candidates = ["latest", "auto"]
    result: List[str] = []
    for item in candidates:
        item = str(item).strip()
        if item and item not in result:
            result.append(item)
    if "auto" not in result:
        result.append("auto")
    return result


def choose_source_version(app_dir: Path, configured: str, app_cfg: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
    """选择可复制的模板目录。

    旧逻辑：source_version 指向 latest 时，如果 latest 不存在就直接失败。
    新逻辑：允许自动回退到其他可用版本目录，适合没有 latest 模板、
    只有 1.5.0.0 / 2.9.5 这类版本目录的应用。
    """
    configured = str(configured or "auto").strip()
    allow_fallback = parse_bool(app_cfg.get("allow_source_version_fallback", config.get("allow_source_version_fallback", True)), True)

    def has_template(name: str) -> bool:
        d = app_dir / name
        return d.is_dir() and bool(compose_files(d))

    if configured and configured != "auto":
        if has_template(configured):
            return configured
        if not allow_fallback:
            return None
        warn(f"配置的源版本目录不存在或缺少 docker-compose：{app_dir.name}/{configured}；将自动尝试其他模板目录")
        candidates = [x for x in source_version_candidates_for_app(app_cfg, config) if x != configured]
    else:
        candidates = source_version_candidates_for_app(app_cfg, config)

    for item in candidates:
        if item == "auto":
            versions = list_template_dirs(app_dir)
            if versions:
                selected = versions[0]
                success(f"已自动选择模板目录：{app_dir.name}/{selected}")
                return selected
            continue
        if has_template(item):
            success(f"已选择模板目录：{app_dir.name}/{item}")
            return item

    return None


def image_candidates(image: str) -> List[str]:
    ref = parse_image_ref(image)
    repo = ref.repository
    repo_no_library = repo.replace("library/", "", 1)
    candidates: List[str] = []
    if ref.registry == "docker.io":
        candidates.extend([repo, repo_no_library, f"docker.io/{repo}", f"registry-1.docker.io/{repo}"])
    else:
        candidates.append(f"{ref.registry}/{repo}")

    raw = image.strip().strip("'").strip('"')
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if ":" in raw.rsplit("/", 1)[-1]:
        raw = raw.rsplit(":", 1)[0]
    candidates.append(raw)
    return sorted(set(candidates), key=len, reverse=True)


def target_image_ref(image: str, new_tag: str, digest: Optional[str], pin_digest: bool) -> str:
    ref = parse_image_ref(image)
    if pin_digest and digest:
        return f"{ref.compose_base}@{digest}"
    return f"{ref.compose_base}:{new_tag}"


def render_image_replacement(text: str, image: str, replacement: str) -> Tuple[str, ReplaceResult]:
    new_text = text
    matched = False
    changed = False

    for candidate in image_candidates(image):
        pattern = re.compile(
            rf'(^\s*image:\s*["\']?)({re.escape(candidate)})(?:(?::[^"\'\s#]+)|(?:@sha256:[a-fA-F0-9]{{64}}))?(["\']?\s*(?:#.*)?$)',
            flags=re.MULTILINE,
        )

        def repl(m: re.Match[str]) -> str:
            nonlocal matched, changed
            matched = True
            line = f"{m.group(1)}{replacement}{m.group(3)}"
            if line != m.group(0):
                changed = True
            return line

        new_text = pattern.sub(repl, new_text)

    return new_text, ReplaceResult(matched=matched, changed=changed and new_text != text)


def replace_image_in_compose(path: Path, image: str, replacement: str, dry_run: bool = False) -> ReplaceResult:
    old_text = path.read_text(encoding="utf-8", errors="ignore")
    new_text, result = render_image_replacement(old_text, image, replacement)
    if result.changed and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return result


def compose_files(version_dir: Path) -> List[Path]:
    files = list(version_dir.glob("docker-compose.yml")) + list(version_dir.glob("docker-compose.yaml"))
    return [p for p in files if p.is_file()]


def load_state(appstore_root: Path, state_file: str) -> Dict[str, Any]:
    return read_json(appstore_root / state_file, default={})


def save_state(appstore_root: Path, state_file: str, state: Dict[str, Any]) -> None:
    write_json(appstore_root / state_file, state)


def context_format(template: str, ctx: Dict[str, str]) -> str:
    try:
        return template.format(**ctx)
    except KeyError as exc:
        die(f"version_dir_template 缺少变量：{exc}，模板：{template}")
    return template


def validate_config(config: Dict[str, Any]) -> None:
    apps = config.get("apps", [])
    if not isinstance(apps, list):
        die("配置错误：apps 必须是数组")
    policy = str(config.get("on_existing_digest_change", "skip"))
    if policy not in DIGEST_POLICIES:
        die(f"配置错误：on_existing_digest_change 不支持：{policy}，可选：{', '.join(sorted(DIGEST_POLICIES))}")

    parse_non_negative_int(config.get("keep_latest_versions", 0), 0, "keep_latest_versions")
    parse_non_negative_int(config.get("max_new_versions_per_app", 1), 1, "max_new_versions_per_app")
    parse_non_negative_int(config.get("dockerhub_page_size", 100), 100, "dockerhub_page_size")
    parse_non_negative_int(config.get("dockerhub_max_pages", 10), 10, "dockerhub_max_pages")
    parse_non_negative_int(config.get("github_max_pages", 5), 5, "github_max_pages")

    for pattern in normalize_string_list(config.get("cleanup_include_regex"), []):
        try:
            re.compile(pattern)
        except re.error as exc:
            die(f"配置错误：cleanup_include_regex 正则无效：{pattern}，错误：{exc}")

    for i, app in enumerate(apps):
        if not isinstance(app, dict):
            die(f"配置错误：apps[{i}] 必须是对象")
        if not parse_bool(app.get("enabled", True), True):
            continue
        name = app.get("app")
        image = app.get("image")
        mode = app.get("mode", "docker_tag")
        if not name:
            die(f"配置错误：apps[{i}] 缺少 app")
        if not image:
            die(f"配置错误：{name} 缺少 image")
        if mode not in SUPPORTED_MODES:
            die(f"配置错误：{name} mode 不支持：{mode}")
        if mode in {"github_release", "github_tag", "github_commit"} and not app.get("github_repo"):
            die(f"配置错误：{name} 使用 {mode} 必须配置 github_repo")
        app_policy = str(app.get("on_existing_digest_change", policy))
        if app_policy not in DIGEST_POLICIES:
            die(f"配置错误：{name} on_existing_digest_change 不支持：{app_policy}")
        parse_non_negative_int(app.get("keep_latest_versions", config.get("keep_latest_versions", 0)), 0, f"{name}.keep_latest_versions")
        parse_non_negative_int(app.get("max_new_versions", config.get("max_new_versions_per_app", 1)), 1, f"{name}.max_new_versions")
        for pattern in normalize_string_list(app.get("cleanup_include_regex"), []):
            try:
                re.compile(pattern)
            except re.error as exc:
                die(f"配置错误：{name} cleanup_include_regex 正则无效：{pattern}，错误：{exc}")


def candidate_context(candidate: VersionCandidate) -> Dict[str, str]:
    clean_tag = strip_v_prefix(candidate.tag)
    clean_github_tag = strip_v_prefix(candidate.github_tag)
    clean_version = strip_v_prefix(candidate.version_value)
    return {
        "tag": candidate.tag,
        "github_tag": candidate.github_tag,
        "date": candidate.date,
        "digest": candidate.digest,
        "digest12": candidate.digest12,
        "commit": candidate.commit,
        "commit8": candidate.commit8,
        "commit_date": candidate.commit_date,
        "version": candidate.version_value,
        "clean_tag": clean_tag,
        "clean_github_tag": clean_github_tag,
        "clean_version": clean_version,
    }


def create_version(appstore_root: Path, app_cfg: Dict[str, Any], config: Dict[str, Any], new_version_name: str, image_version_for_compose: str, digest: Optional[str], dry_run: bool) -> bool:
    app_name = app_cfg["app"]
    image = app_cfg["image"]
    app_dir = appstore_root / "apps" / app_name
    if not app_dir.exists():
        warn(f"应用目录不存在：apps/{app_name}；已跳过该应用")
        return False

    source_version = choose_source_version(app_dir, app_cfg.get("source_version", "auto"), app_cfg, config)
    if not source_version:
        warn(f"找不到可复制的源版本目录：{app_name}；请检查 source_version、source_version_candidates 或现有版本目录是否包含 docker-compose.yml")
        return False

    src_dir = app_dir / source_version
    dst_dir = app_dir / new_version_name
    if dst_dir.exists():
        skip_log(f"版本目录已存在：{app_name}/{new_version_name}；跳过创建")
        return False

    replacement = target_image_ref(image=image, new_tag=image_version_for_compose, digest=digest, pin_digest=parse_bool(app_cfg.get("pin_digest", False)))
    source_compose_files = compose_files(src_dir)
    require_image_match = parse_bool(app_cfg.get("require_image_match", True), True)
    if not source_compose_files:
        msg = f"源版本目录没有 docker-compose 文件：{app_name}/{source_version}"
        if require_image_match:
            warn(msg + "；已跳过创建")
            return False
        warn(msg)
    else:
        matched_any = False
        changed_any = False
        for compose in source_compose_files:
            result = replace_image_in_compose(compose, image, replacement, dry_run=True)
            matched_any = matched_any or result.matched
            changed_any = changed_any or result.changed
        if not matched_any:
            msg = f"源版本模板未匹配到镜像：{app_name}/{source_version}，image={image}"
            if require_image_match:
                warn(msg + "；已跳过创建，避免生成错误版本目录")
                return False
            warn(msg + "；仍继续创建，但创建后可能保留旧镜像")
        elif not changed_any:
            success(f"模板检查通过：源版本镜像已经是目标值：{replacement}")

    action_log(f"{'预览创建' if dry_run else '创建'}版本：{app_name}/{new_version_name}；模板={source_version}；镜像={replacement}", dry_run)
    if dry_run:
        return True

    shutil.copytree(src_dir, dst_dir)
    changed_compose = False
    matched_compose = False
    for compose in compose_files(dst_dir):
        result = replace_image_in_compose(compose, image, replacement, dry_run=False)
        matched_compose = matched_compose or result.matched
        changed_compose = changed_compose or result.changed
        if result.changed:
            success(f"镜像已更新：{compose} -> {replacement}")
    if not matched_compose:
        warn(f"创建完成但未匹配到镜像：目录={dst_dir}，配置镜像={image}；请检查 docker-compose.yml")
    elif not changed_compose:
        success(f"镜像无需修改：{dst_dir} 已是目标值 {replacement}")
    return True


def existing_version_digest_status(appstore_root: Path, app_cfg: Dict[str, Any], existing_version: str, image_tag: str, digest: str) -> Tuple[bool, bool]:
    """返回 (matched, needs_update)。直接读取 compose，避免只依赖 state 文件。"""
    app_name = app_cfg["app"]
    image = app_cfg["image"]
    version_dir = appstore_root / "apps" / app_name / existing_version
    if not version_dir.is_dir():
        return False, False
    replacement = target_image_ref(image=image, new_tag=image_tag, digest=digest, pin_digest=True)
    matched = False
    needs_update = False
    for compose in compose_files(version_dir):
        result = replace_image_in_compose(compose, image, replacement, dry_run=True)
        matched = matched or result.matched
        needs_update = needs_update or result.changed
    return matched, needs_update


def update_existing_version_digest(appstore_root: Path, app_cfg: Dict[str, Any], existing_version: str, image_tag: str, digest: str, dry_run: bool) -> str:
    app_name = app_cfg["app"]
    image = app_cfg["image"]
    version_dir = appstore_root / "apps" / app_name / existing_version
    if not version_dir.is_dir():
        warn(f"无法更新 digest：版本目录不存在 {app_name}/{existing_version}")
        return "missing"

    replacement = target_image_ref(image=image, new_tag=image_tag, digest=digest, pin_digest=True)
    matched = False
    changed = False
    for compose in compose_files(version_dir):
        result = replace_image_in_compose(compose, image, replacement, dry_run=dry_run)
        matched = matched or result.matched
        changed = changed or result.changed
        if result.changed:
            action_log(f"{'预览更新' if dry_run else '更新'}已有版本镜像：{compose} -> {replacement}", dry_run)
    if changed:
        return "changed"
    if matched:
        success(f"{app_name}/{existing_version} 镜像 digest 已是最新：{digest[:19]}...")
        return "same"
    warn(f"未匹配到可更新的镜像：{app_name}/{existing_version}，配置镜像={image}；请检查 compose 中 image 字段")
    return "no_match"


def candidates_for_app(app_cfg: Dict[str, Any], config: Dict[str, Any], state: Dict[str, Any]) -> List[VersionCandidate]:
    mode = app_cfg.get("mode", "docker_tag")
    image = app_cfg["image"]
    app_name = app_cfg["app"]
    include_regex = app_cfg.get("include_regex", r"^v?\d+(\.\d+){1,3}$")
    exclude_regex = app_cfg.get("exclude_regex", r"(alpha|beta|rc|dev|nightly|snapshot)")
    max_pages = parse_non_negative_int(config.get("github_max_pages", 5), 5, "github_max_pages")
    page_size = parse_non_negative_int(config.get("dockerhub_page_size", 100), 100, "dockerhub_page_size")
    dockerhub_max_pages = parse_non_negative_int(config.get("dockerhub_max_pages", 10), 10, "dockerhub_max_pages")
    date = today_yyyymmdd()

    if mode == "docker_tag":
        tags = fetch_image_tags(image, page_size=page_size, max_pages=dockerhub_max_pages)
        filtered = filter_values(tags, include_regex, exclude_regex)
        log(f"候选来源：{app_name} Docker Tag；获取={len(tags)}，过滤后={len(filtered)}，最新={filtered[0] if filtered else '无'}", "🏷️")
        return [VersionCandidate(tag=t, github_tag=t, version_value=t, image_tag=t, date=date) for t in filtered]

    if mode in {"github_release", "github_tag"}:
        github_repo = app_cfg["github_repo"]
        if mode == "github_release":
            versions = github_versions_from_releases_or_tags(
                github_repo,
                include_prerelease=parse_bool(app_cfg.get("include_prerelease", False), False),
                max_pages=max_pages,
                fallback_to_tags=parse_bool(app_cfg.get("fallback_to_github_tags", config.get("fallback_to_github_tags", True)), True),
            )
        else:
            versions = github_tags(github_repo, max_pages=max_pages)
        filtered = filter_values(versions, include_regex, exclude_regex)
        log(f"候选来源：{app_name} {'GitHub Release' if mode == 'github_release' else 'GitHub Tag'}；获取={len(versions)}，过滤后={len(filtered)}，最新={filtered[0] if filtered else '无'}", "🏷️")
        track_tag = app_cfg.get("track_tag", "latest")
        digest = ""
        if parse_bool(app_cfg.get("pin_digest", False), False) and filtered:
            digest = fetch_image_digest(image, tag=track_tag)
            log(f"镜像摘要：{app_name} {image}:{track_tag} -> {digest}", "🐳")
        return [VersionCandidate(tag=v, github_tag=v, version_value=v, image_tag=track_tag, date=date, digest=digest, digest12=digest.replace("sha256:", "")[:12] if digest else "") for v in filtered]

    if mode == "latest_digest":
        track_tag = app_cfg.get("track_tag", "latest")
        digest = fetch_image_digest(image, tag=track_tag)
        digest12 = digest.replace("sha256:", "")[:12]
        app_state = state.get(app_name, {})
        if app_state.get("digest") == digest and app_state.get("version"):
            # 不直接 return []，而是返回 state 中记录的版本名，让 process_app 再确认目录是否真的存在。
            version_value = str(app_state.get("version"))
            success(f"{app_name} latest digest 未变化：{digest12}；继续复核版本目录 {version_value}")
        else:
            version_value = app_cfg.get("version_value_template", "{date}-{digest12}").format(date=date, digest12=digest12, digest=digest, tag=track_tag)
        return [VersionCandidate(tag=track_tag, github_tag=track_tag, version_value=version_value, image_tag=track_tag, date=date, digest=digest, digest12=digest12)]

    if mode == "github_commit":
        github_repo = app_cfg["github_repo"]
        branch = app_cfg.get("github_branch", "main")
        commit, commit_date = github_commit(github_repo, branch)
        commit8 = commit[:8]
        app_state = state.get(app_name, {})
        if app_state.get("commit") == commit and app_state.get("version"):
            # state 只用于复用版本名，不再作为唯一跳过依据。
            version_value = str(app_state.get("version"))
            success(f"{app_name} commit 未变化：{commit8}；继续复核版本目录 {version_value}")
        else:
            version_value = commit8
        track_tag = app_cfg.get("track_tag", "latest")
        digest = fetch_image_digest(image, tag=track_tag) if parse_bool(app_cfg.get("pin_digest", False), False) else ""
        return [VersionCandidate(tag=track_tag, github_tag=track_tag, version_value=version_value, image_tag=track_tag, date=date, commit=commit, commit8=commit8, commit_date=commit_date, digest=digest, digest12=digest.replace("sha256:", "")[:12] if digest else "")]

    die(f"未知 mode：{mode}，应用：{app_name}")
    return []


def git_has_changes(repo_root: Path) -> bool:
    return bool(run_capture(["git", "status", "--porcelain"], cwd=repo_root))



def split_app_version(item: str) -> Tuple[str, str]:
    """从 app/version 或 app/version extra 中提取应用名和版本号。"""
    raw = str(item).strip()
    if not raw or "/" not in raw:
        return raw, ""
    app, rest = raw.split("/", 1)
    version = rest.split()[0].strip()
    return app, version


def build_commit_subject(created: List[str], updated: List[str], deleted: List[str], max_items: int = 3) -> str:
    """生成更清晰的 Git commit 标题，GitHub 文件列表会直接显示这个标题。"""
    actions: List[str] = []

    for item in created:
        app, version = split_app_version(item)
        if app and version:
            actions.append(f"{app} to {version}")
        elif item:
            actions.append(str(item))

    if not actions:
        for item in updated:
            app, version = split_app_version(item)
            if app and version:
                actions.append(f"{app} {version} digest")
            elif item:
                actions.append(str(item))

    if actions:
        shown = actions[:max_items]
        suffix = f" and {len(actions) - max_items} more" if len(actions) > max_items else ""
        return "chore(appstore): update " + ", ".join(shown) + suffix

    if deleted:
        apps: List[str] = []
        seen: Set[str] = set()
        for item in deleted:
            app, _ = split_app_version(item)
            if app and app not in seen:
                apps.append(app)
                seen.add(app)
        if apps:
            shown = apps[:max_items]
            suffix = f" and {len(apps) - max_items} more" if len(apps) > max_items else ""
            return "chore(appstore): cleanup old versions for " + ", ".join(shown) + suffix
        return "chore(appstore): cleanup old app versions"

    return "chore(appstore): sync docker image versions"


def build_commit_body(created: List[str], updated: List[str], deleted: List[str], skipped: List[str]) -> str:
    lines = [
        "Docker Version Bot 自动同步结果",
        "",
        f"- 新建版本：{len(created)}",
        f"- 更新已有版本：{len(updated)}",
        f"- 清理旧版本：{len(deleted)}",
        f"- 跳过/提示：{len(skipped)}",
        "",
    ]
    sections = [
        ("新建版本", created),
        ("更新已有版本", updated),
        ("清理旧版本", deleted),
    ]
    for title, values in sections:
        if values:
            lines.append(f"## {title}")
            lines.extend([f"- {item}" for item in values[:30]])
            if len(values) > 30:
                lines.append(f"- ... 共 {len(values)} 项")
            lines.append("")
    return "\n".join(lines).strip()

def write_summary(created: List[str], updated: List[str], deleted: List[str], skipped: List[str], dry_run: bool) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    mode_text = "dry-run 预览，不会写入" if dry_run else "write 真实执行"
    lines = [
        "# 1Panel Docker Version Bot 执行摘要",
        "",
        f"> 模式：**{mode_text}**",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
        f"| 新建版本 | {len(created)} |",
        f"| 更新已有版本 | {len(updated)} |",
        f"| 清理旧版本 | {len(deleted)} |",
        f"| 跳过/提示 | {len(skipped)} |",
        "",
    ]
    if dry_run:
        lines.extend([
            "> 本次是 dry-run，仅展示计划动作。确认无误后再使用 `dry_run=false` 执行。",
            "",
        ])
    sections = [
        ("新建版本", created),
        ("更新已有版本", updated),
        ("清理旧版本", deleted),
        ("跳过/提示", skipped),
    ]
    for title, values in sections:
        if values:
            lines.append(f"## {title}")
            lines.extend([f"- {item}" for item in values])
            lines.append("")
    Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_app(appstore_root: Path, app_cfg: Dict[str, Any], config: Dict[str, Any], state: Dict[str, Any], args: argparse.Namespace, dry_run: bool) -> Tuple[List[str], List[str], List[str], List[str]]:
    app_name = app_cfg["app"]
    app_dir = appstore_root / "apps" / app_name
    mode = app_cfg.get("mode", "docker_tag")
    image = app_cfg.get("image", "")
    track_tag = app_cfg.get("track_tag", "latest")
    source_version = app_cfg.get("source_version", "auto")
    template = app_cfg.get("version_dir_template", "{tag}")
    backfill_missing_versions = parse_bool(app_cfg.get("backfill_missing_versions", config.get("backfill_missing_versions", False)), False)
    allow_v_prefix_alias = parse_bool(app_cfg.get("allow_v_prefix_alias", config.get("allow_v_prefix_alias", True)), True)
    policy = str(app_cfg.get("on_existing_digest_change", config.get("on_existing_digest_change", "skip")))
    keep_latest_versions = parse_non_negative_int(app_cfg.get("keep_latest_versions", config.get("keep_latest_versions", 0)), 0, f"{app_name}.keep_latest_versions")
    cleanup_enabled = parse_bool(app_cfg.get("cleanup_old_versions", config.get("cleanup_old_versions", keep_latest_versions > 0)), keep_latest_versions > 0)
    official_versions_source_of_truth = parse_bool(
        app_cfg.get("official_versions_source_of_truth", app_cfg.get("prune_unofficial_versions", config.get("official_versions_source_of_truth", False))),
        False,
    )
    skip_older_than_existing = parse_bool(
        app_cfg.get("skip_older_than_existing", config.get("skip_older_than_existing", True)),
        True,
    )
    if official_versions_source_of_truth and "skip_older_than_existing" not in app_cfg:
        # 既然以上游官方镜像标签为准，就允许把本地错误写高的版本修正为官方版本。
        skip_older_than_existing = False
    max_new_default = parse_non_negative_int(config.get("max_new_versions_per_app", 1), 1, "max_new_versions_per_app")
    max_new = parse_non_negative_int(args.max_new if args.max_new is not None else app_cfg.get("max_new_versions", max_new_default), max_new_default, f"{app_name}.max_new_versions")

    log(
        f"应用配置：mode={mode}；image={image}；track_tag={track_tag}；source_version={source_version}；"
        f"目录模板={template}；digest策略={policy}；回填历史={backfill_missing_versions}；"
        f"max_new={max_new}；清理旧版={cleanup_enabled}；保留最新={keep_latest_versions}；"
        f"禁止降级创建={skip_older_than_existing}；官方版本为准={official_versions_source_of_truth}",
        "⚙️",
    )

    candidates = candidates_for_app(app_cfg, config, state)
    all_candidates = list(candidates)
    if not candidates:
        if parse_bool(app_cfg.get("cleanup_when_no_candidates", config.get("cleanup_when_no_candidates", False)), False):
            warn(f"{app_name} 无候选版本，但 cleanup_when_no_candidates=true，将继续执行旧版本清理")
            deleted = cleanup_old_versions(appstore_root, app_cfg, config, dry_run)
        else:
            deleted = []
            skip_log(f"{app_name} 无候选版本：默认不清理旧版本，避免接口异常或过滤规则错误造成误删")
        return [], [], deleted, [f"{app_name}: 无候选版本"]

    existing = set(list_all_version_dirs(app_dir))
    log(f"现有目录：{format_items(sorted(existing, key=version_sort_key))}", "📁")

    official_version_names = official_version_names_from_candidates(all_candidates, template)

    if not backfill_missing_versions:
        candidates = candidates[:1]
        log(f"版本策略：只处理最新候选版本，不回填历史版本；max_new={max_new}", "🧭")
    else:
        log(f"版本策略：允许回填缺失版本，最多创建 {max_new} 个", "🧭")

    created: List[str] = []
    updated: List[str] = []
    skipped: List[str] = []
    planned_version_dirs: List[str] = []
    app_state = state.setdefault(app_name, {})
    new_count = 0

    for candidate in candidates:
        ctx = candidate_context(candidate)
        version_dir_name = context_format(template, ctx)
        existing_version = find_existing_version(existing, version_dir_name, allow_v_prefix_alias)
        if (
            not existing_version
            and mode == "latest_digest"
            and candidate.digest
            and parse_bool(app_cfg.get("reuse_existing_digest_version", True), True)
        ):
            reused = find_existing_version_by_digest(
                appstore_root,
                app_cfg,
                config,
                existing,
                candidate.digest,
                candidate.image_tag or candidate.tag or "latest",
            )
            if reused:
                skip_log(
                    f"检测到相同 digest 已存在版本目录：{app_name}/{reused}；"
                    f"复用该目录，避免重复创建 {version_dir_name}"
                )
                existing_version = reused
                version_dir_name = reused
        candidate_label = candidate.github_tag or candidate.tag or candidate.version_value
        log(
            f"候选版本：上游={candidate_label} -> 目录={version_dir_name}；"
            f"镜像标签={candidate.image_tag or candidate.tag or 'latest'}；digest={candidate.digest12 or '无'}",
            "🔍",
        )

        if existing_version:
            skip_log(f"版本目录已存在：{app_name}/{existing_version}；alias匹配={'开启' if allow_v_prefix_alias else '关闭'}")
            should_update_state = True
            if candidate.digest:
                image_tag = candidate.image_tag or candidate.tag or "latest"
                matched, needs_update = existing_version_digest_status(appstore_root, app_cfg, existing_version, image_tag, candidate.digest)
                state_digest_changed = app_state.get("digest") != candidate.digest or app_state.get("version") != existing_version
                log(
                    f"digest 检查：compose匹配={matched}；需要更新={needs_update}；state变化={state_digest_changed}；策略={policy}",
                    "🐳",
                )

                if policy == "update_existing":
                    status = update_existing_version_digest(appstore_root, app_cfg, existing_version, image_tag, candidate.digest, dry_run)
                    if status == "changed":
                        updated.append(f"{app_name}/{existing_version} digest {candidate.digest12}")
                    elif status == "same":
                        skipped.append(f"{app_name}: 最新版本已存在 {existing_version}，digest 已是最新")
                    else:
                        should_update_state = False
                        skipped.append(f"{app_name}: 最新版本已存在 {existing_version}，但未匹配到可更新镜像")
                elif policy == "create_digest_version" and (needs_update or state_digest_changed):
                    digest_template = app_cfg.get("digest_version_dir_template", "{version}-{digest12}")
                    digest_version_name = context_format(digest_template, ctx)
                    if find_existing_version(existing, digest_version_name, allow_v_prefix_alias):
                        skip_log(f"digest 版本已存在：{app_name}/{digest_version_name}")
                        skipped.append(f"{app_name}: digest 版本已存在 {digest_version_name}")
                    elif new_count < max_new:
                        action_log(f"digest 已变化，将创建 digest 版本目录：{app_name}/{digest_version_name}", dry_run)
                        ok = create_version(appstore_root, app_cfg, config, digest_version_name, image_tag, candidate.digest or None, dry_run)
                        if ok:
                            created.append(f"{app_name}/{digest_version_name}")
                            planned_version_dirs.append(digest_version_name)
                            existing.add(digest_version_name)
                            new_count += 1
                    else:
                        should_update_state = False
                        skipped.append(f"{app_name}: digest 已变化但达到 max_new_versions={max_new}，停止创建")
                else:
                    if matched and not needs_update:
                        skipped.append(f"{app_name}: 最新版本已存在 {existing_version}，digest 已是最新")
                    else:
                        skipped.append(f"{app_name}: 最新版本已存在 {existing_version}，不回填历史版本")
            else:
                skipped.append(f"{app_name}: 最新版本已存在 {existing_version}，不回填历史版本")

            if should_update_state:
                app_state.update({
                    "checked_at": utc_now(),
                    "version": existing_version,
                    "tag": candidate.tag,
                    "github_tag": candidate.github_tag,
                    "digest": candidate.digest,
                    "digest12": candidate.digest12,
                    "commit": candidate.commit,
                    "commit8": candidate.commit8,
                })
                success(f"状态记录已更新：{app_name} -> {existing_version}")
            if not backfill_missing_versions:
                break
            continue

        if skip_older_than_existing:
            newest_existing = newest_existing_version(existing, app_cfg, config)
            if newest_existing and is_not_newer_than_existing(version_dir_name, newest_existing):
                skip_log(
                    f"候选版本不是更新版本，已跳过：{app_name}/{version_dir_name}；"
                    f"当前最高版本={newest_existing}；避免 Docker Hub 标签回退或本地版本更高时自动降级"
                )
                skipped.append(f"{app_name}: 候选版本 {version_dir_name} 不高于当前最高版本 {newest_existing}，跳过")
                if not backfill_missing_versions:
                    break
                continue

        if new_count >= max_new:
            skipped.append(f"{app_name}: 达到 max_new_versions={max_new}，停止创建")
            break

        action_log(f"准备创建新版本目录：{app_name}/{version_dir_name}", dry_run)
        ok = create_version(appstore_root, app_cfg, config, version_dir_name, candidate.image_tag or candidate.tag or "latest", candidate.digest or None, dry_run)
        if ok:
            created.append(f"{app_name}/{version_dir_name}")
            planned_version_dirs.append(version_dir_name)
            existing.add(version_dir_name)
            new_count += 1
            app_state.update({
                "checked_at": utc_now(),
                "version": version_dir_name,
                "tag": candidate.tag,
                "github_tag": candidate.github_tag,
                "digest": candidate.digest,
                "digest12": candidate.digest12,
                "commit": candidate.commit,
                "commit8": candidate.commit8,
            })
            success(f"版本处理完成：{app_name}/{version_dir_name}")
        else:
            skipped.append(f"{app_name}: 创建版本 {version_dir_name} 失败或被策略跳过")

        if not backfill_missing_versions:
            break

    pruned = prune_unofficial_versions(
        appstore_root,
        app_cfg,
        config,
        dry_run,
        official_versions=official_version_names,
        extra_versions=planned_version_dirs,
    )
    deleted = cleanup_old_versions(
        appstore_root,
        app_cfg,
        config,
        dry_run,
        extra_versions=planned_version_dirs,
        exclude_versions=pruned,
    )
    all_deleted = pruned + deleted
    success(f"应用处理完成：新建={len(created)}，更新={len(updated)}，清理={len(all_deleted)}，跳过={len(skipped)}")
    return created, updated, all_deleted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="1Panel Docker Version Bot")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"配置文件，默认 {DEFAULT_CONFIG}")
    parser.add_argument("--repo-root", default=".", help="目标 1Panel AppStore 仓库根目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    parser.add_argument("--write", action="store_true", help="写入版本目录或更新已有版本")
    parser.add_argument("--commit", action="store_true", help="自动 git commit")
    parser.add_argument("--push", action="store_true", help="自动 git push")
    parser.add_argument("--push-branch", default=os.getenv("APPSTORE_BRANCH", "main"), help="推送目标分支")
    parser.add_argument("--max-new", type=int, default=None, help="每个应用最多创建几个版本")
    args = parser.parse_args()

    appstore_root = Path(args.repo_root).resolve()
    config_path = resolve_config_path(appstore_root, args.config)
    config = read_json(config_path)
    validate_config(config)

    if not (appstore_root / "apps").exists():
        die(f"目标仓库 apps 目录不存在：{appstore_root / 'apps'}")

    dry_run = args.dry_run or not args.write
    state_file = config.get("state_file", ".state/docker-version-bot.json")
    enabled_apps = [a for a in config.get("apps", []) if isinstance(a, dict) and parse_bool(a.get("enabled", True), True)]

    section("运行环境")
    log(f"运行模式：{'dry-run 预览模式，不会写入、删除、提交或推送文件' if dry_run else 'write 真实执行模式'}", "🧪" if dry_run else "🚀")
    log(f"目标仓库：{appstore_root}", "📦")
    log(f"配置文件：{config_path}", "🧾")
    log(f"状态文件：{appstore_root / state_file}", "🗂️")
    log(f"推送分支：{args.push_branch}；启用应用数：{len(enabled_apps)}", "🌿")

    state = load_state(appstore_root, state_file)
    created_all: List[str] = []
    updated_all: List[str] = []
    deleted_all: List[str] = []
    skipped_all: List[str] = []

    for app_cfg in config.get("apps", []):
        if not parse_bool(app_cfg.get("enabled", True), True):
            skip_log(f"应用已禁用，跳过：{app_cfg.get('app', 'unknown')}")
            continue
        app_name = app_cfg.get("app", "")
        group_start(f"应用检查：{app_name}")
        log(f"开始检查应用：{app_name}", "🔎")
        try:
            created, updated, deleted, skipped = process_app(appstore_root, app_cfg, config, state, args, dry_run)
            created_all.extend(created)
            updated_all.extend(updated)
            deleted_all.extend(deleted)
            skipped_all.extend(skipped)
        except urllib.error.HTTPError as exc:
            warn(f"{app_name} HTTP 请求失败：HTTP {exc.code} {exc.reason}；已跳过该应用")
            skipped_all.append(f"{app_name}: HTTP {exc.code}")
        except Exception as exc:
            warn(f"{app_name} 检查失败：{exc}；已跳过该应用")
            skipped_all.append(f"{app_name}: {exc}")
        finally:
            group_end()

    if not dry_run:
        save_state(appstore_root, state_file, state)
        success(f"状态文件已保存：{appstore_root / state_file}")
    else:
        skip_log("dry-run 模式：状态文件不会写入")

    write_summary(created_all, updated_all, deleted_all, skipped_all, dry_run)

    section("执行结果汇总")
    success(f"新建版本：{len(created_all)} 个；{format_items(created_all)}")
    success(f"更新已有版本：{len(updated_all)} 个；{format_items(updated_all)}")
    success(f"清理旧版本：{len(deleted_all)} 个；{format_items(deleted_all)}")
    skip_log(f"跳过/提示：{len(skipped_all)} 条；{format_items(skipped_all)}")

    if not dry_run and args.commit:
        run(["git", "config", "user.name", config.get("git_user_name", "github-actions[bot]")], cwd=appstore_root)
        run(["git", "config", "user.email", config.get("git_user_email", "github-actions[bot]@users.noreply.github.com")], cwd=appstore_root)
        if git_has_changes(appstore_root):
            action_log("检测到文件变更，准备提交", False)
            run(["git", "add", "apps", state_file], cwd=appstore_root)
            commit_subject = build_commit_subject(created_all, updated_all, deleted_all)
            commit_body = build_commit_body(created_all, updated_all, deleted_all, skipped_all)
            run(["git", "commit", "-m", commit_subject, "-m", commit_body], cwd=appstore_root)
            success(f"Git commit 完成：{commit_subject}")
        else:
            success("没有文件变更，无需 commit")

    if not dry_run and args.push:
        action_log(f"准备推送到远程分支：{args.push_branch}", False)
        run(["git", "pull", "--rebase", "--autostash", "origin", args.push_branch], cwd=appstore_root, check=True)
        run(["git", "push", "origin", f"HEAD:{args.push_branch}"], cwd=appstore_root)
        success("Git push 完成")


if __name__ == "__main__":
    main()

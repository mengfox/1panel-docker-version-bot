#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1Panel Docker Version Bot

用于独立 Public 仓库，定时检查 Docker / GitHub 版本，并把新版本同步到目标 1Panel AppStore 仓库。

支持模式：
- docker_tag
- github_release
- github_tag
- latest_digest
- github_commit

特点：
- 无第三方 Python 依赖
- 默认只跟踪最新版本，不回填历史版本
- 支持 Docker Hub / OCI Registry V2
- 支持 GitHub Release / Tag / Commit
- 支持 latest digest 固定到 @sha256
- 支持 dry-run、自动 commit、自动 push
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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_CONFIG = "config/docker-version-sync.json"
SUPPORTED_MODES = {"docker_tag", "github_release", "github_tag", "latest_digest", "github_commit"}


@dataclass
class ImageRef:
    registry: str
    registry_api: str
    repository: str
    tag: str
    compose_base: str


@dataclass
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


def log(message: str) -> None:
    print(f"[docker-version-bot] {message}")


def warn(message: str) -> None:
    print(f"[docker-version-bot][WARN] {message}", file=sys.stderr)


def die(message: str, code: int = 1) -> None:
    print(f"[docker-version-bot][ERROR] {message}", file=sys.stderr)
    raise SystemExit(code)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_yyyymmdd() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")


def run(cmd: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd), check=check, text=True)


def run_capture(cmd: List[str], cwd: Path) -> str:
    result = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=True)
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


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_config_path(appstore_root: Path, config: str) -> Path:
    p = Path(config)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    return (appstore_root / p).resolve()


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


def request_raw(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Tuple[bytes, Dict[str, str], int]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": "1panel-docker-version-bot",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read() if method != "HEAD" else b""
        return data, dict(resp.headers), int(resp.status)


def http_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Dict[str, Any] | List[Any]:
    raw, resp_headers, status = request_raw(
        url,
        method="GET",
        headers={"Accept": "application/json", **(headers or {})},
        timeout=timeout,
    )
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        die(f"接口返回空内容：{url}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:300].replace("\n", " ")
        die(f"接口返回内容不是 JSON：{url}，HTTP={status}，Content-Type={resp_headers.get('Content-Type', '')}，错误={exc}，预览={preview}")


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

    return ImageRef(
        registry=registry,
        registry_api=registry_api,
        repository=repo_no_tag,
        tag=tag,
        compose_base=compose_base,
    )


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
        url = next_url

    return sorted(set(tags))


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

    headers = {}
    username = os.getenv("REGISTRY_USERNAME", "")
    password = os.getenv("REGISTRY_PASSWORD", "")
    if username and password:
        basic = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"

    data = http_json(realm + "?" + urllib.parse.urlencode(params), headers=headers)
    if not isinstance(data, dict):
        return None
    return data.get("token") or data.get("access_token")


def registry_request_with_auth(
    registry_api: str,
    repository: str,
    path: str,
    method: str,
    accept: str,
) -> Tuple[bytes, Dict[str, str], int]:
    url = f"https://{registry_api}/v2/{repository}/{path}"
    try:
        return request_raw(url, method=method, headers={"Accept": accept})
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise

        token = registry_token(registry_api, repository, exc.headers.get("WWW-Authenticate", ""))
        if not token:
            raise

        return request_raw(
            url,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
            },
        )


def registry_v2_json(registry_api: str, repository: str, path: str, accept: str = "application/json") -> Tuple[Dict[str, Any], Dict[str, str]]:
    raw, headers, status = registry_request_with_auth(
        registry_api=registry_api,
        repository=repository,
        path=path,
        method="GET",
        accept=accept,
    )

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


SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-._+].*)?$")


def version_sort_key(value: str) -> Tuple[int, int, int, int, str]:
    m = SEMVER_RE.match(value)
    if not m:
        return (0, 0, 0, 0, value)

    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    stable = 0 if any(x in value.lower() for x in ["alpha", "beta", "rc", "dev", "nightly", "snapshot"]) else 1
    return (stable, major, minor, patch, value)


def filter_values(values: Iterable[str], include_regex: str, exclude_regex: str) -> List[str]:
    inc = re.compile(include_regex) if include_regex else None
    exc = re.compile(exclude_regex) if exclude_regex else None

    result = []
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
        req = urllib.request.Request(current, headers=github_headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                preview = raw[:300].replace("\n", " ")
                die(f"GitHub API 返回非 JSON：{current}，错误={exc}，预览={preview}")

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)

            next_url = ""
            for part in resp.headers.get("Link", "").split(","):
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
    tags = []
    for item in items:
        if item.get("draft"):
            continue
        if item.get("prerelease") and not include_prerelease:
            continue
        tag = item.get("tag_name")
        if tag:
            tags.append(str(tag))
    return sorted(set(tags), key=version_sort_key, reverse=True)


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
    return sha, date


def list_version_dirs(app_dir: Path) -> List[str]:
    if not app_dir.exists():
        return []

    versions = []
    for item in app_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue

        has_data = (item / "data.yml").exists() or (item / "data.yaml").exists()
        has_compose = (item / "docker-compose.yml").exists() or (item / "docker-compose.yaml").exists()
        if has_data and has_compose:
            versions.append(item.name)

    return sorted(versions, key=version_sort_key, reverse=True)


def version_aliases(value: str) -> set[str]:
    aliases = {value}
    if value.startswith("v"):
        aliases.add(value[1:])
    else:
        aliases.add(f"v{value}")
    return aliases


def existing_matches(existing: set[str], version_dir_name: str, allow_v_prefix_alias: bool) -> bool:
    if version_dir_name in existing:
        return True
    if allow_v_prefix_alias and (existing & version_aliases(version_dir_name)):
        return True
    return False


def choose_source_version(app_dir: Path, configured: str) -> Optional[str]:
    versions = list_version_dirs(app_dir)
    if not versions:
        return None
    if configured and configured != "auto":
        return configured if configured in versions else None
    return versions[0]


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


def replace_image_in_compose(path: Path, image: str, replacement: str) -> bool:
    old_text = path.read_text(encoding="utf-8", errors="ignore")
    new_text = old_text
    changed = False

    for candidate in image_candidates(image):
        pattern = re.compile(
            rf'(^\s*image:\s*["\']?)({re.escape(candidate)})(?:(?::[^"\'\s#]+)|(?:@sha256:[a-fA-F0-9]{{64}}))?(["\']?\s*(?:#.*)?$)',
            flags=re.MULTILINE,
        )

        def repl(m: re.Match) -> str:
            nonlocal changed
            changed = True
            return f"{m.group(1)}{replacement}{m.group(3)}"

        new_text = pattern.sub(repl, new_text)

    if changed and new_text != old_text:
        path.write_text(new_text, encoding="utf-8")
        return True

    return False


def replace_texts_in_tree(version_dir: Path, replacements: Dict[str, str], enabled: bool) -> None:
    if not enabled:
        return

    suffixes = {".yml", ".yaml", ".env", ".sh", ".md", ".txt", ".json"}
    for path in version_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        new_text = text
        for old, new in replacements.items():
            if old and new:
                new_text = new_text.replace(old, new)

        if new_text != text:
            path.write_text(new_text, encoding="utf-8")


def load_state(appstore_root: Path, state_file: str) -> Dict[str, Any]:
    return read_json(appstore_root / state_file, default={})


def save_state(appstore_root: Path, state_file: str, state: Dict[str, Any]) -> None:
    write_json(appstore_root / state_file, state)


def context_format(template: str, ctx: Dict[str, str]) -> str:
    try:
        return template.format(**ctx)
    except KeyError as exc:
        die(f"version_dir_template 缺少变量：{exc}，模板：{template}")


def validate_config(config: Dict[str, Any]) -> None:
    apps = config.get("apps", [])
    if not isinstance(apps, list):
        die("配置错误：apps 必须是数组")

    for i, app in enumerate(apps):
        if not isinstance(app, dict):
            die(f"配置错误：apps[{i}] 必须是对象")

        if not app.get("enabled", True):
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


def candidate_context(candidate: VersionCandidate) -> Dict[str, str]:
    return {
        "tag": candidate.tag,
        "github_tag": candidate.github_tag,
        "date": candidate.date,
        "digest": candidate.digest,
        "digest12": candidate.digest12,
        "commit": candidate.commit,
        "commit8": candidate.commit8,
        "version": candidate.version_value,
    }


def create_version(
    appstore_root: Path,
    app_cfg: Dict[str, Any],
    new_version_name: str,
    image_version_for_compose: str,
    digest: Optional[str],
    dry_run: bool,
    ctx: Dict[str, str],
) -> bool:
    app_name = app_cfg["app"]
    image = app_cfg["image"]
    app_dir = appstore_root / "apps" / app_name

    if not app_dir.exists():
        warn(f"应用目录不存在，跳过：apps/{app_name}")
        return False

    source_version = choose_source_version(app_dir, app_cfg.get("source_version", "auto"))
    if not source_version:
        warn(f"找不到可复制的源版本目录，跳过：{app_name}")
        return False

    src_dir = app_dir / source_version
    dst_dir = app_dir / new_version_name

    if dst_dir.exists():
        log(f"{app_name} 已存在版本目录：{new_version_name}，跳过")
        return False

    log(f"{'预览创建' if dry_run else '准备创建'}版本：{app_name}/{new_version_name}，复制自：{source_version}")

    if dry_run:
        return True

    shutil.copytree(src_dir, dst_dir)

    replacement = target_image_ref(
        image=image,
        new_tag=image_version_for_compose,
        digest=digest,
        pin_digest=bool(app_cfg.get("pin_digest", False)),
    )

    compose_files = list(dst_dir.glob("docker-compose.yml")) + list(dst_dir.glob("docker-compose.yaml"))
    changed_compose = False

    for compose in compose_files:
        if replace_image_in_compose(compose, image, replacement):
            changed_compose = True
            log(f"已更新镜像：{compose} -> {replacement}")

    if not changed_compose:
        warn(f"未在 {dst_dir} 的 docker-compose 中匹配到镜像：{image}")

    replacements = {
        source_version: new_version_name,
        "{github_tag}": ctx.get("github_tag", ""),
        "{tag}": ctx.get("tag", ""),
        "{digest}": ctx.get("digest", ""),
        "{digest12}": ctx.get("digest12", ""),
        "{commit}": ctx.get("commit", ""),
        "{commit8}": ctx.get("commit8", ""),
    }

    replace_texts_in_tree(
        version_dir=dst_dir,
        replacements=replacements,
        enabled=bool(app_cfg.get("replace_source_version_text", False)),
    )

    return True


def candidates_for_app(app_cfg: Dict[str, Any], config: Dict[str, Any], state: Dict[str, Any]) -> List[VersionCandidate]:
    mode = app_cfg.get("mode", "docker_tag")
    image = app_cfg["image"]
    app_name = app_cfg["app"]

    include_regex = app_cfg.get("include_regex", r"^v?\d+(\.\d+){1,3}$")
    exclude_regex = app_cfg.get("exclude_regex", r"(alpha|beta|rc|dev|nightly|snapshot)")
    max_pages = int(config.get("github_max_pages", 5))
    page_size = int(config.get("dockerhub_page_size", 100))
    dockerhub_max_pages = int(config.get("dockerhub_max_pages", 10))
    date = today_yyyymmdd()

    if mode == "docker_tag":
        tags = fetch_image_tags(image, page_size=page_size, max_pages=dockerhub_max_pages)
        filtered = filter_values(tags, include_regex, exclude_regex)
        return [VersionCandidate(tag=t, github_tag=t, version_value=t, image_tag=t, date=date) for t in filtered]

    if mode in {"github_release", "github_tag"}:
        github_repo = app_cfg["github_repo"]
        if mode == "github_release":
            versions = github_releases(
                github_repo,
                include_prerelease=bool(app_cfg.get("include_prerelease", False)),
                max_pages=max_pages,
            )
        else:
            versions = github_tags(github_repo, max_pages=max_pages)

        filtered = filter_values(versions, include_regex, exclude_regex)
        track_tag = app_cfg.get("track_tag", "latest")
        digest = ""
        if app_cfg.get("pin_digest", False) and filtered:
            digest = fetch_image_digest(image, tag=track_tag)
            log(f"{app_name} 当前 {image}:{track_tag} digest={digest}")

        return [
            VersionCandidate(
                tag=v,
                github_tag=v,
                version_value=v,
                image_tag=track_tag,
                date=date,
                digest=digest,
                digest12=digest.replace("sha256:", "")[:12] if digest else "",
            )
            for v in filtered
        ]

    if mode == "latest_digest":
        track_tag = app_cfg.get("track_tag", "latest")
        digest = fetch_image_digest(image, tag=track_tag)
        digest12 = digest.replace("sha256:", "")[:12]
        app_state = state.get(app_name, {})
        if app_state.get("digest") == digest:
            log(f"{app_name} latest digest 未变化：{digest12}")
            return []
        return [VersionCandidate(tag=track_tag, github_tag=track_tag, version_value=track_tag, image_tag=track_tag, date=date, digest=digest, digest12=digest12)]

    if mode == "github_commit":
        github_repo = app_cfg["github_repo"]
        branch = app_cfg.get("github_branch", "main")
        commit, commit_date = github_commit(github_repo, branch)
        commit8 = commit[:8]
        app_state = state.get(app_name, {})
        if app_state.get("commit") == commit:
            log(f"{app_name} commit 未变化：{commit8}")
            return []

        track_tag = app_cfg.get("track_tag", "latest")
        digest = ""
        if app_cfg.get("pin_digest", False):
            digest = fetch_image_digest(image, tag=track_tag)

        return [
            VersionCandidate(
                tag=track_tag,
                github_tag=track_tag,
                version_value=commit8,
                image_tag=track_tag,
                date=date,
                commit=commit,
                commit8=commit8,
                commit_date=commit_date,
                digest=digest,
                digest12=digest.replace("sha256:", "")[:12] if digest else "",
            )
        ]

    die(f"未知 mode：{mode}，应用：{app_name}")
    return []


def git_has_changes(repo_root: Path) -> bool:
    return bool(run_capture(["git", "status", "--porcelain"], cwd=repo_root))


def write_summary(created: List[str], skipped: List[str], dry_run: bool) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [
        "# 1Panel Docker Version Bot",
        "",
        f"- Mode: {'dry-run' if dry_run else 'write'}",
        f"- Created: {len(created)}",
        f"- Skipped: {len(skipped)}",
        "",
    ]

    if created:
        lines.append("## New versions")
        for item in created:
            lines.append(f"- {item}")
        lines.append("")

    if skipped:
        lines.append("## Skipped")
        for item in skipped:
            lines.append(f"- {item}")
        lines.append("")

    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="1Panel Docker Version Bot")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"配置文件，默认 {DEFAULT_CONFIG}")
    parser.add_argument("--repo-root", default=".", help="目标 1Panel AppStore 仓库根目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    parser.add_argument("--write", action="store_true", help="写入版本目录")
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
    if dry_run:
        log("当前为 dry-run 模式，不会写入文件。")

    state_file = config.get("state_file", ".state/docker-version-bot.json")
    state = load_state(appstore_root, state_file)

    default_max_new = int(config.get("max_new_versions_per_app", 1))
    max_new_global = args.max_new if args.max_new is not None else default_max_new
    allow_v_prefix_alias = bool(config.get("allow_v_prefix_alias", True))

    created: List[str] = []
    skipped: List[str] = []

    for app_cfg in config.get("apps", []):
        if not app_cfg.get("enabled", True):
            continue

        app_name = app_cfg["app"]
        image = app_cfg["image"]
        mode = app_cfg.get("mode", "docker_tag")
        log(f"处理应用：{app_name}，mode={mode}，image={image}")

        try:
            candidates = candidates_for_app(app_cfg, config, state)
        except Exception as exc:
            warn(f"处理应用失败：{app_name}，错误：{exc}")
            skipped.append(f"{app_name}: error")
            continue

        if not candidates:
            skipped.append(f"{app_name}: no candidate")
            continue

        app_dir = appstore_root / "apps" / app_name
        existing = set(list_version_dirs(app_dir))
        template = app_cfg.get("version_dir_template", "{tag}")
        backfill_missing_versions = bool(app_cfg.get("backfill_missing_versions", config.get("backfill_missing_versions", False)))

        if not backfill_missing_versions:
            latest = candidates[0]
            latest_ctx = candidate_context(latest)
            latest_dir = context_format(template, latest_ctx)

            if existing_matches(existing, latest_dir, allow_v_prefix_alias=allow_v_prefix_alias):
                log(f"{app_name} 最新版本已存在：{latest_dir}，不回填历史版本")
                skipped.append(f"{app_name}: latest exists {latest_dir}")
                continue

            candidates = [latest]

        max_new = int(app_cfg.get("max_new_versions", max_new_global))
        app_created = 0

        for candidate in candidates:
            ctx = candidate_context(candidate)
            version_dir_name = context_format(template, ctx)

            if existing_matches(existing, version_dir_name, allow_v_prefix_alias=allow_v_prefix_alias):
                continue

            ok = create_version(
                appstore_root=appstore_root,
                app_cfg=app_cfg,
                new_version_name=version_dir_name,
                image_version_for_compose=candidate.image_tag or candidate.tag or "latest",
                digest=candidate.digest or None,
                dry_run=dry_run,
                ctx=ctx,
            )

            if ok:
                created.append(f"{app_name}:{version_dir_name}")
                app_created += 1

                if not dry_run:
                    state[app_name] = {
                        "mode": mode,
                        "image": image,
                        "version_dir": version_dir_name,
                        "tag": candidate.tag,
                        "github_tag": candidate.github_tag,
                        "digest": candidate.digest,
                        "commit": candidate.commit,
                        "updated_at": utc_now(),
                    }

            if app_created >= max_new:
                break

        if app_created == 0:
            log(f"{app_name} 没有需要创建的新版本")
            skipped.append(f"{app_name}: no new version")

    if created:
        log("本次创建新版本：")
        for item in created:
            log(f"  - {item}")
    else:
        log("本次没有创建新版本")

    write_summary(created, skipped, dry_run)

    if dry_run:
        return

    save_state(appstore_root, state_file, state)

    if args.commit or args.push:
        if git_has_changes(appstore_root):
            run(["git", "config", "user.name", config.get("git_user_name", "github-actions[bot]")], cwd=appstore_root)
            run(["git", "config", "user.email", config.get("git_user_email", "github-actions[bot]@users.noreply.github.com")], cwd=appstore_root)
            run(["git", "add", "apps", state_file], cwd=appstore_root)
            run(["git", "commit", "-m", config.get("commit_message", "chore: sync docker image versions")], cwd=appstore_root)
        else:
            log("没有文件变更，无需 commit")

    if args.push:
        # 尽量减少定时任务与人工提交冲突。
        run(["git", "pull", "--rebase", "origin", args.push_branch], cwd=appstore_root, check=False)
        run(["git", "push", "origin", f"HEAD:{args.push_branch}"], cwd=appstore_root)


if __name__ == "__main__":
    main()

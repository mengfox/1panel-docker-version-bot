# 配置说明

配置文件：

```text
config/docker-version-sync.json
```

## 全局配置

```json
{
  "state_file": ".state/docker-version-bot.json",
  "dockerhub_page_size": 100,
  "dockerhub_max_pages": 10,
  "github_max_pages": 5,
  "max_new_versions_per_app": 1,
  "commit_message": "chore: sync docker image versions",
  "apps": []
}
```

## 1. Docker 有版本 tag：docker_tag

适合：

```text
nginx:1.27.4
redis:7.2.5
```

配置：

```json
{
  "app": "nginx",
  "enabled": true,
  "mode": "docker_tag",
  "image": "nginx",
  "source_version": "auto",
  "include_regex": "^\\d+\\.\\d+(\\.\\d+)?(-alpine)?$",
  "exclude_regex": "(mainline|perl|otel|bookworm|bullseye|rc|beta|alpha)",
  "version_dir_template": "{tag}",
  "max_new_versions": 1
}
```

生成：

```text
apps/nginx/1.27.4/
```

compose：

```yaml
image: nginx:1.27.4
```

## 2. Docker 只有 latest，GitHub 有 Release：github_release

配置：

```json
{
  "app": "some-app",
  "enabled": true,
  "mode": "github_release",
  "github_repo": "owner/some-app",
  "image": "owner/some-app",
  "track_tag": "latest",
  "pin_digest": true,
  "source_version": "auto",
  "include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot)",
  "version_dir_template": "{github_tag}",
  "max_new_versions": 1
}
```

生成：

```text
apps/some-app/v1.2.3/
```

compose 会固定 digest：

```yaml
image: owner/some-app@sha256:xxxx
```

## 3. Docker 只有 latest，GitHub 有 Tag：github_tag

```json
{
  "app": "another-app",
  "enabled": true,
  "mode": "github_tag",
  "github_repo": "owner/another-app",
  "image": "owner/another-app",
  "track_tag": "latest",
  "pin_digest": true,
  "source_version": "auto",
  "include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot)",
  "version_dir_template": "{github_tag}",
  "max_new_versions": 1
}
```

## 4. 只有 latest：latest_digest

```json
{
  "app": "latest-only-app",
  "enabled": true,
  "mode": "latest_digest",
  "image": "owner/latest-only-app",
  "track_tag": "latest",
  "pin_digest": true,
  "source_version": "auto",
  "version_dir_template": "latest-{date}-{digest12}",
  "max_new_versions": 1
}
```

生成：

```text
apps/latest-only-app/latest-20260427-a1b2c3d4e5f6/
```

## 5. 追踪 GitHub Commit：github_commit

```json
{
  "app": "commit-app",
  "enabled": true,
  "mode": "github_commit",
  "github_repo": "owner/commit-app",
  "github_branch": "main",
  "image": "owner/commit-app",
  "track_tag": "latest",
  "pin_digest": true,
  "source_version": "auto",
  "version_dir_template": "git-{date}-{commit8}",
  "max_new_versions": 1
}
```

## 变量说明

`version_dir_template` 支持：

```text
{tag}
{github_tag}
{date}
{digest}
{digest12}
{commit}
{commit8}
{version}
```

## 状态文件

Bot 会在 AppStore 仓库写入：

```text
.state/docker-version-bot.json
```

用于记录 latest digest / commit 的变化，避免重复生成版本。

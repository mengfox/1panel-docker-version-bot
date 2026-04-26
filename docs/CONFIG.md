# 配置说明

配置文件：

```text
config/docker-version-sync.json
```

## 核心字段

```json
{
  "backfill_missing_versions": false,
  "allow_v_prefix_alias": true,
  "max_new_versions_per_app": 1
}
```

说明：

```text
backfill_missing_versions=false
表示只跟踪最新版本。如果最新版本已经存在，不会继续创建旧版本。

allow_v_prefix_alias=true
表示 2.17 和 v2.17 视为同一个版本，避免重复创建。

max_new_versions_per_app=1
每个应用每次最多创建一个新版本。
```

## rainbow-dnsmgr 推荐配置

```json
{
  "app": "rainbow-dnsmgr",
  "enabled": true,
  "mode": "github_release",
  "github_repo": "netcccyun/dnsmgr",
  "image": "netcccyun/dnsmgr",
  "track_tag": "latest",
  "pin_digest": true,
  "source_version": "latest",
  "include_regex": "^v?\\d+\\.\\d+(\\.\\d+)?$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot)",
  "version_dir_template": "{github_tag}",
  "max_new_versions": 1,
  "backfill_missing_versions": false
}
```

## 版本来源模式

```text
docker_tag       Docker 镜像有版本 tag
github_release   GitHub Release 做版本号
github_tag       GitHub Tag 做版本号
latest_digest    只有 latest，用 digest 判断变化
github_commit    用 GitHub commit 判断变化
```

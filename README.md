# 1Panel Docker Version Bot v2.6

这是用于维护 `mengfox/1panel-appstore` 的自动版本同步工具，当前默认监控：

- `rainbow-dnsmgr`：版本来源为 GitHub Release，镜像使用 `netcccyun/dnsmgr:latest`，并固定 `@sha256:digest`；
- `next-terminal`：版本来源为 Docker Hub 标签，镜像使用 `dushixiang/next-terminal:<version>`；
- `mtab`：版本来源为 Docker Hub 标签，镜像使用 `itushan/mtab:<version>`，并以 Docker Hub 官方镜像标签为准；
- 所有应用默认只保留最新 3 个版本，并保留 `latest` 模板目录。

## v2.6 本次重点

### mtab 以官方镜像标签为准

`mtab` 当前使用 Docker Hub 官方标签作为唯一版本来源。如果本地目录存在高于官方标签的历史版本，例如本地有 `2.9.5`，但 Docker Hub 官方稳定标签是 `2.9.3`，脚本会：

1. 从现有可用模板目录复制生成 `2.9.3`；
2. 将镜像改为 `itushan/mtab:2.9.3`；
3. 删除本地非官方版本目录 `2.9.5`；
4. 继续只保留最新 3 个官方版本；
5. `dry_run=true` 时只预览，不会真正创建或删除。

对应配置：

```json
{
  "app": "mtab",
  "enabled": true,
  "mode": "docker_tag",
  "image": "itushan/mtab",
  "source_version": "auto",
  "include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot|preview|canary|test|b\\d+)",
  "version_dir_template": "{clean_tag}",
  "max_new_versions": 1,
  "backfill_missing_versions": false,
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "cleanup_include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "require_image_match": true,
  "cleanup_when_no_candidates": false,
  "skip_older_than_existing": false,
  "official_versions_source_of_truth": true,
  "prune_unofficial_versions": true
}
```

## 当前推荐策略

```json
{
  "backfill_missing_versions": false,
  "allow_v_prefix_alias": true,
  "on_existing_digest_change": "update_existing",
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "fallback_to_github_tags": true,
  "require_image_match": true,
  "cleanup_when_no_candidates": false,
  "skip_older_than_existing": true,
  "official_versions_source_of_truth": false
}
```

说明：

1. 默认只处理最新候选版本，不回填历史版本；
2. `2.17`、`v2.17`、`V2.17` 视为同一个版本；
3. 如果版本目录已存在，但 Docker `latest` digest 变化，会直接更新已有目录；
4. 每个应用只保留最新 3 个版本；
5. `latest` 模板目录永远保留；
6. 清理旧版本时只处理版本号目录，避免误删 `assets`、`scripts`、`images` 等目录；
7. 上游无候选版本时不清理旧版本；
8. 模板镜像不匹配时不创建新版本；
9. 对 `mtab` 可启用 `official_versions_source_of_truth=true`，按 Docker Hub 官方标签修正本地错误版本。

## mtab 官方版本修正示例

假设本地目录：

```text
apps/mtab/2.9.5
apps/mtab/assets
```

Docker Hub 官方候选：

```text
2.9.3
```

`dry_run=true` 日志会显示：

```text
候选版本：上游=2.9.3 -> 目录=2.9.3
预览创建版本：mtab/2.9.3；模板=2.9.5；镜像=itushan/mtab:2.9.3
官方版本校验：以镜像仓库标签为准；官方版本=2.9.3；本地待修正=2.9.5
预览删除非官方版本：mtab/2.9.5
```

真实执行后：

```text
apps/mtab/2.9.3
apps/mtab/assets
```

## 使用方式

先 dry-run：

```text
Actions -> Docker Version Bot -> Run workflow -> dry_run=true
```

重点看日志：

```text
运行模式
应用配置
候选来源
候选版本
digest 检查
官方版本校验
清理策略
执行结果汇总
```

确认待创建、待更新、待删除目录都正确后，再执行：

```text
Actions -> Docker Version Bot -> Run workflow -> dry_run=false
```

## GitHub Secrets

必须配置：

```text
APPSTORE_PUSH_TOKEN
```

可选配置：

```text
REGISTRY_USERNAME
REGISTRY_PASSWORD
```

私有镜像或 Docker Hub 限流时再配置。

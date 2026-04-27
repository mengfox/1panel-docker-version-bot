# 1Panel Docker Version Bot - v3.0

用于独立 Public 仓库，自动检查 Docker Hub / GitHub Release / GitHub Tag / latest digest，并把新版本同步到目标 `1Panel AppStore` 仓库。

## 当前内置应用

- `rainbow-dnsmgr`：使用 GitHub Release 作为版本来源，镜像固定 `latest` digest；
- `next-terminal`：使用 Docker Hub 版本标签作为版本来源；
- `zdir`：使用 Docker Hub 版本标签作为版本来源，不再使用 `latest` digest 生成日期目录。

所有应用默认只保留最新 3 个版本，并保留 `latest` 模板目录。

## v3.0 本次修正

你要求 `zdir` 不要再按 `latest` 更新，本版已改为按 Docker Hub 镜像版本标签监控：

```text
helloz/zdir:<version>
```

也就是：

1. 读取 Docker Hub `helloz/zdir` 的 Tag 列表；
2. 只匹配稳定版本号，例如 `0.3.6`；
3. 不使用 `latest` 作为版本目录；
4. 从 `apps/zdir/latest` 模板复制新目录；
5. 自动把镜像改为 `helloz/zdir:<version>`；
6. 如果之前生成过 `20260427-digest12` 这类目录，会作为旧策略目录清理；
7. 只保留最新 3 个版本目录，`latest` 永远保留。

## zdir 配置

```json
{
  "app": "zdir",
  "enabled": true,
  "mode": "docker_tag",
  "image": "helloz/zdir",
  "source_version": "latest",
  "include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot|preview|canary|test|b\\d+)",
  "version_dir_template": "{clean_tag}",
  "max_new_versions": 1,
  "backfill_missing_versions": false,
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "cleanup_include_regex": [
    "^v?\\d+\\.\\d+\\.\\d+$",
    "^\\d{8}-[a-fA-F0-9]{12}$"
  ],
  "require_image_match": true,
  "cleanup_when_no_candidates": false,
  "skip_older_than_existing": false,
  "official_versions_source_of_truth": true,
  "prune_unofficial_versions": true,
  "pin_digest": false
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
3. 如果版本目录已存在，但 Docker `latest` digest 变化，可按应用策略更新已有目录；
4. 每个应用只保留最新 3 个版本；
5. `latest` 模板目录永远保留；
6. 清理旧版本时只处理版本目录，避免误删 `assets`、`scripts`、`images` 等目录；
7. 上游无候选版本时不清理旧版本；
8. 模板镜像不匹配时不创建新版本。

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

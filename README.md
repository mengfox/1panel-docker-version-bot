# 1Panel Docker Version Bot - v3.3

用于独立 Public 仓库，自动检查 Docker Hub / GitHub Release / GitHub Tag / latest digest，并把新版本同步到目标 `1Panel AppStore` 仓库。

## 当前内置应用

- `rainbow-dnsmgr`：使用 GitHub Release 作为版本来源，镜像固定 `latest` digest；
- `next-terminal`：使用 Docker Hub 版本标签作为版本来源；
- `zdir`：使用 Docker Hub 版本标签作为版本来源，不再使用 `latest` digest 生成日期目录；
- `xarrpay-merchant`：使用 Docker Hub 版本标签作为版本来源，支持 `1.5.0.0` 这类四段版本号。

所有应用默认只保留最新 3 个版本，并保留 `latest` 模板目录。




## v3.3 模板目录回退修复

修复你遇到的提示：

```text
Warning: 找不到可复制的源版本目录：xarrpay-merchant；请检查 source_version 或 latest 模板目录
```

新版不再强制只能使用 `latest` 做模板。模板选择顺序：

```text
1. source_version_candidates 中配置的目录，例如 latest
2. auto：自动扫描已有版本目录，选择语义化版本最高且包含 docker-compose.yml 的目录
```

例如 `apps/xarrpay-merchant/latest` 不存在，但存在：

```text
apps/xarrpay-merchant/1.5.0.0/docker-compose.yml
```

创建 `1.5.0.1` 时会自动使用 `1.5.0.0` 作为模板。

## v3.3 本次新增

新增 `xarrpay-merchant` 应用监控：

```text
xarrpay/xarrpay-merchant:<version>
```

策略：

1. 读取 Docker Hub `xarrpay/xarrpay-merchant` 的 Tag 列表；
2. 只匹配稳定版本号，例如 `1.5.0.0`、`1.5.0.1`；
3. 支持三段和四段版本号，避免 `1.5.0.10` 与 `1.5.0.2` 排序错误；
4. 优先从 `apps/xarrpay-merchant/latest` 模板复制；如果没有 `latest`，自动使用已有最高版本目录作为模板；
5. 自动把镜像改为 `xarrpay/xarrpay-merchant:<version>`；
6. 以 Docker Hub 标签为准，可清理非官方版本目录；
7. 只保留最新 3 个版本目录，`latest` 永远保留。

## xarrpay-merchant 配置

```json
{
  "app": "xarrpay-merchant",
  "enabled": true,
  "mode": "docker_tag",
  "image": "xarrpay/xarrpay-merchant",
  "source_version": "auto",
  "source_version_candidates": ["latest", "auto"],
  "allow_source_version_fallback": true,
  "include_regex": "^v?\\d+(\\.\\d+){2,3}$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot|preview|canary|test|b\\d+)",
  "version_dir_template": "{clean_tag}",
  "max_new_versions": 1,
  "backfill_missing_versions": false,
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "cleanup_include_regex": [
    "^v?\\d+(\\.\\d+){2,3}$",
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

## v3.3 本次修正

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


## v3.3 安全修复

- 新增 `safe_prune_requires_official_target=true`：开启“官方版本为准”时，只有确认本地已存在或本次已成功计划创建官方版本目录后，才会清理非官方旧版本，避免新版本创建失败时误删最后一个可用版本。
- 修复非回填模式下 `--max-new 0` 被强制改为 1 的问题，现在可以用于只检查、不创建新版本的场景。
- 打包时移除 `__pycache__`，避免提交无意义的 Python 缓存文件。

## v3.4：工作流定时与日志清理

本版新增 GitHub Actions 清理能力：

- 定时任务从每 6 小时改为每 2 小时执行一次：`0 */2 * * *`；
- 同步任务执行后自动清理 `docker-version-bot.yml` 的历史运行记录；
- 成功的 workflow run 只保留最新 3 条；
- 失败、取消、超时等非成功记录保留 3 天；
- 永远排除当前运行中的 run，避免删除正在执行的工作流；
- 新增 `Workflow Cleanup` 手动工作流，支持一键清理记录或日志。

### 手动清理模式

进入：

```text
Actions -> Workflow Cleanup -> Run workflow
```

可选模式：

```text
policy_records          按策略清理运行记录：成功保留最新3条，失败保留3天
policy_logs_only        按策略只清理日志，不删除运行记录
all_finished_logs_only  一键清理所有已完成运行的日志，不删除运行记录
```

默认 `dry_run=true`，建议先预览确认，再改为 `false` 真正清理。

### 权限要求

清理 workflow run 或日志需要 GitHub Actions 写权限，workflow 已配置：

```yaml
permissions:
  contents: read
  actions: write
```

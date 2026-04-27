# 1Panel Docker Version Bot v2.4

这是用于维护 `mengfox/1panel-appstore` 的自动版本同步工具，当前默认配置针对多个 1Panel 应用：

- `rainbow-dnsmgr`：版本来源为 GitHub Release，镜像使用 `netcccyun/dnsmgr:latest` 并固定 `@sha256:digest`；
- `next-terminal`：版本来源为 Docker Hub 标签，镜像使用 `dushixiang/next-terminal:<version>`；
- 所有应用默认只保留最新 3 个版本，并保留 `latest` 模板目录。

## v2.4 本次新增

### 新增 Next Terminal 监控更新

新增 `next-terminal` 应用监控配置：

```json
{
  "app": "next-terminal",
  "enabled": true,
  "mode": "docker_tag",
  "image": "dushixiang/next-terminal",
  "source_version": "latest",
  "include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "exclude_regex": "(alpha|beta|rc|dev|nightly|snapshot|preview|canary|test|b\\d+)",
  "version_dir_template": "{clean_tag}",
  "max_new_versions": 1,
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "cleanup_include_regex": "^v?\\d+\\.\\d+\\.\\d+$",
  "require_image_match": true,
  "cleanup_when_no_candidates": false
}
```

策略说明：

1. 只跟踪 Docker Hub 上的稳定版本标签，例如 `v3.1.0`、`3.1.0`；
2. 默认排除 `alpha`、`beta`、`rc`、`dev`、`nightly`、`snapshot`、`preview`、`canary`、`b4` 等预览/测试版本；
3. 如果上游标签是 `v3.1.0`，生成目录为 `3.1.0`；
4. 从 `apps/next-terminal/latest` 复制模板；
5. 自动替换模板中的 `dushixiang/next-terminal:<旧版本>`；
6. 只保留最新 3 个版本目录，永远保留 `latest` 模板目录。

## v2.3 本次优化

### 1. 兼容性和稳定性修复

v2.3 在 v2.2 日志优化基础上继续修复长期运行细节：

- `2.17` / `v2.17` / `V2.17` 统一视为同一个版本；
- 旧版本清理的 `cleanup_include_regex` 改为大小写不敏感，避免 `V2.17` 漏清理；
- `preserve_versions` 保护目录大小写不敏感，例如 `Latest` 也不会误删；
- GitHub Release / Tag 分页请求统一走重试逻辑，减少 429 / 5xx / 网络抖动失败；
- 清理旧版本函数去掉重复返回，代码更干净。

### 2. 日志提示继续保留

v2.2 起已优化 GitHub Actions 日志可读性：

- 增加“运行环境”“应用检查”“执行结果汇总”分段；
- GitHub Actions 中每个应用会自动折叠成一个 group；
- dry-run 统一用 `🧪` 标识，真实执行统一用 `🚀` 标识；
- 创建、更新、清理、跳过、警告都有明确图标；
- 每个候选版本会输出“上游版本 -> 目标目录 -> 镜像标签 -> digest”；
- 清理旧版本会明确显示“参与清理版本、保留版本、删除版本、保护目录”；
- Warning / Error 会使用 GitHub Actions annotation，方便在 Actions 页面定位；
- `GITHUB_STEP_SUMMARY` 会生成中文表格摘要，方便看整体结果。

示例日志：

```text
[1Panel Version Bot] 🔷 运行环境
[1Panel Version Bot] 🧪 运行模式：dry-run 预览模式，不会写入、删除、提交或推送文件
[1Panel Version Bot] ⚙️ 应用配置：mode=github_release；image=netcccyun/dnsmgr；...
[1Panel Version Bot] 🔍 候选版本：上游=v2.18 -> 目录=2.18；镜像标签=latest；digest=cccccccccccc
[1Panel Version Bot] 🧪 预览创建版本：rainbow-dnsmgr/2.18；模板=latest；镜像=...
[1Panel Version Bot] 🧪 预览删除旧版本：rainbow-dnsmgr/2.15
[1Panel Version Bot] 🔷 执行结果汇总
```

### 3. 保留 v2.1 的安全策略

- state 不再作为唯一跳过依据；
- `include_regex` / `exclude_regex` 大小写不敏感；
- `mode=github_release` 且 Release 为空时自动回退 Git Tag；
- `require_image_match=true`，模板镜像不匹配时跳过创建；
- `cleanup_when_no_candidates=false`，无候选版本时默认不清理旧版本；
- HTTP 请求对 `429 / 5xx / 网络抖动` 增加轻量重试；
- 每个应用只保留最新 3 个版本，`latest` 模板目录永远保留。

## 当前推荐策略

```json
{
  "backfill_missing_versions": false,
  "allow_v_prefix_alias": true,
  "on_existing_digest_change": "update_existing",
  "cleanup_old_versions": true,
  "keep_latest_versions": 3,
  "preserve_versions": ["latest"],
  "cleanup_include_regex": "^v?\\d+\\.\\d+(\\.\\d+)?$",
  "fallback_to_github_tags": true,
  "require_image_match": true,
  "cleanup_when_no_candidates": false
}
```

含义：

1. 只跟踪上游最新 Release；
2. 不回填历史版本；
3. `2.17`、`v2.17`、`V2.17` 视为同一个版本；
4. 如果版本目录已存在，但 Docker `latest` digest 变化，直接更新已有目录；
5. 每个应用只保留最新 3 个版本；
6. `latest` 模板目录永远保留；
7. 清理旧版本时只处理版本号目录，避免误删 `assets`、`scripts`、`images` 等目录；
8. 上游无候选版本时不清理旧版本；
9. 模板镜像不匹配时不创建新版本。

## 使用方式

先 dry-run：

```text
Actions -> Docker Version Bot -> Run workflow -> dry_run=true
```

重点看日志里的这些内容，尤其是 `rainbow-dnsmgr` 和 `next-terminal` 两个应用是否都进入检查：

```text
运行模式
应用配置
候选来源
候选版本
digest 检查
清理策略
执行结果汇总
```

确认日志没问题后执行：

```text
Actions -> Docker Version Bot -> Run workflow -> dry_run=false
```

## 只保留最新 3 个版本

示例目录：

```text
latest
2.15
2.16
2.17
2.18
assets
```

执行后保留：

```text
latest
2.16
2.17
2.18
assets
```

删除：

```text
2.15
```

`dry_run=true` 时只预览，不会真正删除。

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

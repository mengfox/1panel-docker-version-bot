# 1Panel Docker Version Bot - rainbow-dnsmgr 定时监控版

这是 `rainbow-dnsmgr` 专用的 Docker / GitHub Release 自动版本同步 Bot。

## 当前策略

```text
版本来源：GitHub Release
镜像来源：Docker latest
镜像固定：pin_digest=true，生成版本时固定当前 latest digest
模板目录：apps/rainbow-dnsmgr/latest
生成目录：apps/rainbow-dnsmgr/<GitHub Release>
自动检查：每 6 小时运行一次
```

## 文件结构

```text
.github/workflows/docker-version-bot.yml
config/docker-version-sync.json
tools/docker-version-sync.py
docs/RAINBOW_DNSMGR.md
README.md
```

## 需要配置的 Secret

在 Bot 仓库添加：

```text
APPSTORE_PUSH_TOKEN
```

用途：允许 Bot 推送到 `mengfox/1panel-appstore`。

推荐权限：

```text
Public 仓库：public_repo
Private 仓库：repo
Fine-grained token：Contents Read and write
```

可选：

```text
REGISTRY_USERNAME
REGISTRY_PASSWORD
```

用于私有 Docker Registry。

## 目标仓库

workflow 默认：

```yaml
APPSTORE_REPO: mengfox/1panel-appstore
APPSTORE_BRANCH: main
```

## rainbow-dnsmgr 配置

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
  "replace_source_version_text": false
}
```

## 运行逻辑

```text
每 6 小时自动运行
        ↓
检测 netcccyun/dnsmgr GitHub Release
        ↓
如果发现新 Release，复制 apps/rainbow-dnsmgr/latest/
        ↓
生成 apps/rainbow-dnsmgr/<release>/
        ↓
把 image: netcccyun/dnsmgr:latest 固定成 image: netcccyun/dnsmgr@sha256:...
        ↓
提交并推送到 1panel-appstore
```

## 手动运行

```text
Actions
→ Docker Version Bot
→ Run workflow
```

参数说明：

```text
dry_run=true   只预览，不推送
dry_run=false  真实生成并推送
```

## 本地测试

```bash
git clone https://github.com/mengfox/1panel-appstore.git appstore

python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --dry-run
```

实际写入：

```bash
python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --write
```

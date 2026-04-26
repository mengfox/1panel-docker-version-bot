# 1Panel Docker Version Bot - v1.5 优化版

这是 `rainbow-dnsmgr` 专用的 Docker / GitHub Release 自动版本同步 Bot。

## 当前策略

```text
版本来源：GitHub Release
镜像来源：Docker latest
镜像固定：pin_digest=true，生成版本时固定当前 latest digest
模板目录：apps/rainbow-dnsmgr/latest
生成目录：apps/rainbow-dnsmgr/<GitHub Release>
自动检查：每 6 小时运行一次
历史回填：默认关闭
```

## 本版优化

```text
1. 修复已有 2.17 时继续创建 2.16 的历史回填问题
2. 默认 backfill_missing_versions=false，只跟踪最新版本
3. 增加 allow_v_prefix_alias，避免 2.17 / v2.17 重复创建
4. 增加配置校验
5. push 前自动 pull --rebase，减少冲突
6. 打包排除 __pycache__
7. workflow 增加 Python 语法检查
```

## 需要的 Secret

在 Bot 仓库添加：

```text
APPSTORE_PUSH_TOKEN
```

推荐权限：

```text
Public 目标仓库：public_repo
Private 目标仓库：repo
Fine-grained token：Contents Read and write
```

可选：

```text
REGISTRY_USERNAME
REGISTRY_PASSWORD
```

## 目标仓库

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
  "version_dir_template": "{github_tag}",
  "max_new_versions": 1,
  "backfill_missing_versions": false
}
```

## 手动运行

```text
Actions
→ Docker Version Bot
→ Run workflow
```

参数：

```text
dry_run=true   只预览
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

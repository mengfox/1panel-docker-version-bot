# 1Panel Docker Version Bot - rainbow-dnsmgr 完整版

这是用于 `rainbow-dnsmgr` 的完整 Bot 配置包。

目标：

```text
GitHub Release 作为版本来源
Docker latest 作为镜像来源
Docker digest 固定镜像版本
复制 apps/rainbow-dnsmgr/latest 生成 apps/rainbow-dnsmgr/vX.X
```

## 文件结构

```text
.github/workflows/docker-version-bot.yml
config/docker-version-sync.json
tools/docker-version-sync.py
docs/RAINBOW_DNSMGR.md
```

## 需要的 Secret

在 Bot 仓库添加：

```text
APPSTORE_PUSH_TOKEN
```

权限：

```text
如果 1panel-appstore 是公开仓库：public_repo
如果是私有仓库：repo
```

Fine-grained token：

```text
Repository access: mengfox/1panel-appstore
Contents: Read and write
Metadata: Read-only
```

## 目标仓库

workflow 默认目标：

```yaml
APPSTORE_REPO: mengfox/1panel-appstore
APPSTORE_BRANCH: main
```

## 使用前检查

确保 `1panel-appstore` 已存在：

```text
apps/rainbow-dnsmgr/latest/data.yml
apps/rainbow-dnsmgr/latest/docker-compose.yml
```

并且 compose 镜像写法为：

```yaml
image: netcccyun/dnsmgr:latest
```

## 手动运行

```text
Actions
→ Docker Version Bot
→ Run workflow
→ dry_run=false
```

## 本地测试

```bash
git clone https://github.com/mengfox/1panel-appstore.git appstore

python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --dry-run
```

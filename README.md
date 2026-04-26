# 1Panel Docker Version Bot

公开透明维护的 Docker / GitHub 版本同步机器人，用于自动更新 `1panel-appstore` 仓库中的应用版本目录。

## 支持的版本来源

| mode | 适合场景 | 版本目录示例 |
|---|---|---|
| `docker_tag` | Docker 镜像有明确版本 tag | `1.27.4` |
| `github_release` | Docker 只推 `latest`，GitHub 有 Release | `v1.2.3` |
| `github_tag` | Docker 只推 `latest`，GitHub 有 Tag | `v1.2.3` |
| `latest_digest` | Docker 只有 `latest`，没有明确版本 | `latest-20260427-a1b2c3d4e5f6` |
| `github_commit` | 没有版本号，需要追踪分支提交 | `git-20260427-a1b2c3d4` |

## 工作流程

```text
Docker/GitHub 出现新版本
        ↓
Bot 仓库 GitHub Actions 定时检查
        ↓
克隆 1panel-appstore
        ↓
复制已有版本目录生成新版本
        ↓
替换 docker-compose.yml 里的 image
        ↓
提交并推送到 1panel-appstore
        ↓
1panel-appstore 自己同步到 CNB
```

## 仓库结构

```text
1panel-docker-version-bot/
├── .github/workflows/docker-version-bot.yml
├── config/docker-version-sync.json
├── tools/docker-version-sync.py
├── docs/CONFIG.md
├── docs/USAGE.md
└── README.md
```

## 快速开始

### 1. 创建 Public 仓库

推荐仓库名：

```text
1panel-docker-version-bot
```

简介：

```text
Public Docker and GitHub version sync bot for 1Panel AppStore.
```

### 2. 配置 Secret

在 Bot 仓库添加：

```text
APPSTORE_PUSH_TOKEN
```

用途：允许 Bot 推送到 `mengfox/1panel-appstore`。

如果 `1panel-appstore` 是 Public 仓库，Classic PAT 可以用：

```text
public_repo
```

如果是 Private 仓库，Classic PAT 用：

```text
repo
```

Fine-grained token 推荐：

```text
Repository access:
Only selected repositories
选择 mengfox/1panel-appstore

Repository permissions:
Contents: Read and write
Metadata: Read-only
```

可选 Secret：

```text
REGISTRY_USERNAME
REGISTRY_PASSWORD
```

用于私有 Docker Registry。

### 3. 修改目标仓库

编辑：

```text
.github/workflows/docker-version-bot.yml
```

默认：

```yaml
env:
  APPSTORE_REPO: mengfox/1panel-appstore
  APPSTORE_BRANCH: main
```

### 4. 配置应用

编辑：

```text
config/docker-version-sync.json
```

启用需要自动维护的应用，把 `enabled` 改成 `true`。

## 手动运行

```text
Actions
→ Docker Version Bot
→ Run workflow
```

`dry_run=true` 只预览，不会推送。

## 本地测试

```bash
git clone https://github.com/mengfox/1panel-appstore.git appstore

python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --dry-run
```

写入版本目录：

```bash
python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --write
```

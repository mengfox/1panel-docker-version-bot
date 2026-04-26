# 使用教程

## 推送 Bot 仓库

```bash
git init
git add .
git commit -m "init 1panel docker version bot"
git branch -M main
git remote add origin https://github.com/mengfox/1panel-docker-version-bot.git
git push -u origin main
```

## 本地预览

```bash
git clone https://github.com/mengfox/1panel-appstore.git appstore

python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --dry-run
```

## 本地写入

```bash
python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --write
```

## 本地提交推送

```bash
python3 tools/docker-version-sync.py \
  --repo-root appstore \
  --config config/docker-version-sync.json \
  --write \
  --commit \
  --push \
  --push-branch main
```

## GitHub Actions 手动运行

```text
Actions
→ Docker Version Bot
→ Run workflow
```

`dry_run=true` 表示只预览。

## 推荐维护方法

每新增一个应用，只需要在：

```text
config/docker-version-sync.json
```

里面加一段配置。

推荐优先级：

```text
1. Docker 有版本 tag -> docker_tag
2. Docker 只有 latest，但 GitHub 有 Release -> github_release
3. Docker 只有 latest，但 GitHub 有 Tag -> github_tag
4. 只有 latest -> latest_digest
5. 需要追踪源码 -> github_commit
```

# 1Panel Docker Version Bot - v1.6 强制只跟踪最新版本

本版重点修复：已有 `2.17` 时，不再继续创建 `2.16`、`2.15` 等旧版本。

## 修复点

```text
1. 已存在版本判断改为读取 app 目录下所有子目录
2. 不再要求目录里必须有 data.yml/docker-compose.yml 才算存在
3. 默认 backfill_missing_versions=false，只处理最新候选版本
4. 2.17 / v2.17 默认视为同一版本，避免重复创建
5. dry-run 日志会输出已存在目录，方便排查
```

## 当前策略

```text
rainbow-dnsmgr 使用 GitHub Release 作为版本来源
Docker latest 作为镜像来源
pin_digest=true 固定当前 latest digest
source_version=latest 从 latest 模板复制
每 6 小时自动检查一次
```

## 正确效果

如果当前已有：

```text
apps/rainbow-dnsmgr/2.17
```

上游最新还是：

```text
2.17
```

则输出：

```text
rainbow-dnsmgr 最新版本已存在：2.17，不回填历史版本
```

不会再创建：

```text
2.16
2.15
```

## 清理误创建的历史版本

如果已经误创建了：

```text
apps/rainbow-dnsmgr/2.15
apps/rainbow-dnsmgr/2.16
```

在 `1panel-appstore` 仓库执行：

```bash
rm -rf apps/rainbow-dnsmgr/2.15 apps/rainbow-dnsmgr/2.16

git add apps/rainbow-dnsmgr
git commit -m "remove old rainbow-dnsmgr backfill versions"
git push origin main
```

## 手动测试

```text
Actions
→ Docker Version Bot
→ Run workflow
→ dry_run=true
```

# OpenFish GitHub 开源发布清单

## 1. 发布前检查

1. 轮换 Telegram Bot Token（若曾出现在日志/聊天/截图中）。
2. 确认不会提交以下文件：
- `.env`
- `mvp_scaffold/data/`
- `mvp_scaffold/projects.yaml`
- `.venv/`
3. 运行本地检查：

```bash
cd mvp_scaffold
bash scripts/ci_local.sh
```

## 2. 初始化 GitHub 仓库

1. 新建仓库（建议名称：`openfish` 或 `openfish-assistant`）。
2. 设置默认分支 `main`。
3. 推送代码。

## 3. 仓库设置建议

1. 开启 Security alerts（Dependabot alerts）。
2. 配置分支保护（`main`）：
- Require pull request
- Require status checks to pass（CI）
3. 开启 Discussions（可选）。

## 4. 首个版本发布

1. 确认 `CHANGELOG.md` 已包含当前版本说明。
2. 打 tag（示例）：

```bash
git tag -a v0.1.0 -m "OpenFish v0.1.0"
git push origin v0.1.0
```

3. 在 GitHub Release 页面发布 `v0.1.0`。

## 5. 发布后维护节奏

1. 每次合并前跑 CI。
2. 每个版本更新 `CHANGELOG.md`。
3. 处理安全问题时优先走私密披露（见 `SECURITY.md`）。

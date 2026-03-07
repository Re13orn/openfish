# OpenFish GitHub 发布清单

这个文档面向当前仓库的持续发布，而不是“首个版本上线”。

## 1. 发布前基础检查

1. 确认工作区干净：

```bash
git status --short --branch
```

2. 确认不会提交以下本地文件：
- `.env`
- `mvp_scaffold/data/`
- `mvp_scaffold/projects.yaml`
- `.venv/`
- `~/.codex/config.toml`

3. 若 bot token、截图、日志曾暴露敏感信息，先轮换对应凭据。

4. 运行最小质量检查：

```bash
PYTHONPATH=./mvp_scaffold ./mvp_scaffold/.venv/bin/python -m pytest -q
bash mvp_scaffold/scripts/install_start.sh check
```

## 2. 仓库设置建议

1. 默认分支保持为 `main`。
2. 开启 Security alerts / Dependabot alerts。
3. 为 `main` 配置分支保护：
- Require pull request
- Require status checks to pass
4. GitHub Release 使用语义化版本号：
- `v0.9.1`
- `v1.0.0-rc1`
- `v1.0.0`

## 3. 常规版本发布流程

1. 更新 `CHANGELOG.md`。
2. 准备 release notes 文件，建议放在：
- `docs/releases/<version>.md`
3. 推送 `main`：

```bash
git push origin main
```

4. 创建 tag：

```bash
git tag -a vX.Y.Z -m "OpenFish vX.Y.Z"
git push origin vX.Y.Z
```

5. 创建 GitHub Release：

```bash
gh release create vX.Y.Z \
  --repo Re13orn/openfish \
  --title "OpenFish vX.Y.Z" \
  --notes-file docs/releases/vX.Y.Z.md
```

## 4. `v1.0` 发布额外要求

`v1.0` 不只是“再发一个 tag”，还代表稳定承诺。发布前至少满足：

1. 产品边界写清楚：
- 单用户
- Telegram 驱动
- 本地优先
- 不是多用户平台

2. 核心命令面稳定：
- `/projects`
- `/use`
- `/ask`
- `/do`
- `/status`
- `/resume`
- `/retry`
- `/approve`
- `/reject`
- `/cancel`
- `/memory`
- `/note`
- `/schedule-*`
- `/mcp-*`
- `/model`

3. 人工 smoke test 完整通过。
4. 安装文档、README、Release Notes、CHANGELOG 术语一致。
5. 版本发布说明中写明：
- 支持范围
- 已知限制
- 升级建议

## 5. 发布后维护

1. patch 版本只修 bug，例如 `v1.0.1`。
2. minor 版本增加兼容能力，例如 `v1.1.0`。
3. 破坏性变更只放到 major 版本，例如 `v2.0.0`。
4. 安全问题优先走私密披露流程（见 `SECURITY.md`）。

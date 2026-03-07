# OpenFish 人工 Smoke Test 清单

这个清单用于版本发布前的人工验收，尤其适用于 `v1.0.0-rc1` 和 `v1.0.0`。

## 1. 启动前

1. 确认环境检查通过：

```bash
bash mvp_scaffold/scripts/install_start.sh check
```

2. 启动服务：

```bash
bash mvp_scaffold/scripts/install_start.sh restart
```

3. 确认状态正常：

```bash
bash mvp_scaffold/scripts/install_start.sh status
```

## 2. Telegram 基础流程

在 Telegram 私聊 bot，依次验证：

1. `/start`
- 能正常返回欢迎信息
- 主菜单可见

2. 项目面板
- 点“项目”
- 能看到当前项目、最近项目、更多操作

3. `/status`
- 能正常展示状态卡片
- 再次点击时优先更新已有卡片，而不是反复刷新新消息

## 3. 核心任务链路

1. `/ask`
- 直接发普通文本
- 确认按 `/ask` 处理
- typing 正常
- stream 模式下过程流正常

2. `/do`
- 触发一条短任务
- 最终结果正常返回
- 长结果能分段发送

3. `/resume`
- 当存在最近任务时可继续
- 不存在时有合理提示

4. `/retry`
- 最近任务可重试
- 无可重试任务时提示合理

## 4. 审批链路

准备一条会进入审批的任务，然后验证：

1. 批准按钮可点击
2. 拒绝按钮可点击
3. “批准+备注”进入两步流
4. “拒绝+原因”进入两步流
5. 旧审批卡片按钮不会误处理当前审批

## 5. 记忆与状态

1. `/note <text>`
- 笔记能保存

2. `/memory`
- 能看到摘要、笔记、任务
- 超过一页时可翻页
- `上一页 / 下一页` 按钮可用

3. `/diff`
- 能返回当前变更摘要

## 6. 定时任务

1. 新建定时任务
- 按钮向导正常

2. `/schedule-list`
- 列表正常

3. `/schedule-run <id>`
- 可以立即执行

4. `/schedule-pause` / `/schedule-enable`
- 能切换启停状态

5. `/schedule-del`
- 能删除

## 7. MCP 与模型

1. `/mcp`
- 列表正常返回

2. `/mcp <name>`
- 详情正常返回

3. `/mcp-disable <name>`
- 能停用

4. `/mcp-enable <name>`
- 能启用

5. `/model`
- 显示当前模型

6. `/model set <name>`
- 设置成功

7. `/model reset`
- 恢复默认成功

## 8. 上传与异常场景

1. 上传允许类型文件
- 能接收
- 能返回分析结果

2. 上传不允许类型文件
- 返回友好拒绝信息

3. 上传超大文件
- 返回友好错误，不出现未处理异常

## 9. 重启恢复

1. 服务运行中执行：

```bash
bash mvp_scaffold/scripts/install_start.sh restart
```

2. 重启后再发 `/status`
- 服务恢复正常
- 没有明显的历史卡死任务污染状态

## 10. 发布前最终确认

1. 测试通过
2. smoke test 通过
3. `git status` 干净
4. `CHANGELOG.md` 已更新
5. release notes 已准备

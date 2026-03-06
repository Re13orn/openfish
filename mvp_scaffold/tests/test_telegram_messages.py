from pathlib import Path

from src import telegram_messages


def test_prompt_mode_hint_appends_help() -> None:
    assert telegram_messages.prompt_mode_hint("请输入参数。") == "请输入参数。\n可发送 /help 查看命令。"


def test_project_add_prompt_uses_default_root_when_present() -> None:
    text = telegram_messages.project_add_prompt("key", {}, Path("/tmp/projects"))
    assert "项目新增向导 1/4" in text
    assert "默认根目录: /tmp/projects" in text


def test_run_template_confirm_prompt_includes_extra_text() -> None:
    text = telegram_messages.run_template_prompt(
        "confirm",
        {"template": "bugfix", "extra": "先跑测试"},
        "bugfix, audit",
    )
    assert "模板执行向导 3/3" in text
    assert "模板: bugfix" in text
    assert "附加说明: 先跑测试" in text

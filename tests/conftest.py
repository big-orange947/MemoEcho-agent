# -*- coding: utf-8 -*-
"""pytest 公共夹具。

设计原则: 测试**不依赖**真实 LLM、网络、NapCat。
- LLM 用 FakeListChatModel / 自定义 fake 替换;
- 数据库用临时目录(每个测试独立),不污染开发数据;
- 需要图执行时,注入 fake llm_factory。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试能 import app.*(项目根目录加入 sys.path)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =============================================================================
# 测试隔离: 禁止一切真实 QQ 发送
# =============================================================================
# 起因(2026-09-27 事故): 测试里用的是**假模型**, 但发送器是真实的 ——
# NapCat 一上线, 用例里的 send_qq_message 就真的把消息发给了真人(6 条, 两次
# 跑测试各 3 条)。生产实例那边一条记录都没有(测试用的是临时库), 号主只觉得
# "莫名其妙又发消息了"。
#
# 做法: 把 NapcatBridge.call 换成记录器 —— 所有发送路径(工具/outbox/告警/回复)
# 最终都要走它, 一处堵住就够。需要"发送成功"语义的用例照旧拿到 ok;
# 想断言"到底发了什么"的用例, 把 qq_sends 当参数取用即可。
# =============================================================================
@pytest.fixture(autouse=True)
def qq_sends(monkeypatch):
    """把 QQ 桥的动作调用换成记录器, 返回记录列表(测试可断言)。"""
    from app.bridge import napcat as napcat_module

    sent: list[dict] = []

    async def fake_call(self, action: str, params=None):  # noqa: ANN001
        if action in ("send_private_msg", "send_group_msg"):
            sent.append({"action": action, "params": params or {}})
            return {"ok": True, "data": {"message_id": len(sent)}}
        # 读类动作(登录信息/好友列表)在测试里一律视为"没连上":
        # 需要通讯录的用例自己装假桥(见 tests/test_ui_api.py 的 bridge 夹具)。
        return {"ok": False, "error": f"test-mode: action {action} not available"}

    monkeypatch.setattr(napcat_module.NapcatBridge, "call", fake_call)
    return sent


@pytest.fixture()
def temp_data_dir(tmp_path, monkeypatch):
    """把应用数据目录指向临时目录,保证测试之间互不干扰。

    做法: 设置环境变量 MEMO_ECHO_DATA_DIR(pydantic-settings 会自动读取),
    然后清掉配置单例缓存,让下次 get_settings() 重新构建。
    """
    monkeypatch.setenv("MEMO_ECHO_DATA_DIR", str(tmp_path))

    # 清掉配置单例,强制重新读取环境变量
    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    # 清掉数据库连接(线程本地 + 全局注册表),避免指向旧路径
    from app import db as db_module

    db_module.close_connections()
    monkeypatch.setattr(db_module._local, "connection", None, raising=False)
    monkeypatch.setattr(db_module, "_all_connections", {}, raising=False)

    yield tmp_path

    db_module.close_connections()

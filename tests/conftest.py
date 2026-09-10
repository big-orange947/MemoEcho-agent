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

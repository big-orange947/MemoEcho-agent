import asyncio

from app.main import RUNTIME_REVISION, RUNTIME_SOURCE_PATH, health


def test_health_exposes_runtime_identity() -> None:
    """健康检查必须暴露运行版本和源码位置，避免联调时误连残留的旧进程。"""
    result = asyncio.run(health())

    assert result["status"] == "ok"
    assert result["runtimeRevision"] == RUNTIME_REVISION
    assert result["sourcePath"] == RUNTIME_SOURCE_PATH
    assert result["sourcePath"].endswith("app\\main.py")

"""P-A 最小验证：Graphiti + Neo4j + 本地 embedding + DeepSeek 提取。

运行前提：
1. Neo4j 已启动:  docker compose -f ../scripts/docker-compose.neo4j.yml up -d
2. 本地 env 已加载:  . .\\scripts\\local-env.ps1  （或在环境里设置 NEO4J_PASSWORD）
3. LLM 可用：走 Event Center 的模型配置（runtime 环境变量 MEMO_ECHO_RUNTIME_USER_ID 指向有模型配置的用户）

运行：
    cd agent-runtime-python
    uv run python scripts/graphiti_demo.py

验证目标：
- Neo4j 连接与索引初始化
- 本地 bge-small-zh embedding 与 Graphiti 兼容
- DeepSeek（OpenAI 兼容）能完成 Episode → 实体/关系提取
- 检索能命中提取出的关系
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许从仓库根直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.embedding_service import EmbeddingServiceClient
from app.clients.event_center_service import EventCenterServiceClient
from app.memory.graph_service import MemoryGraphService

DEMO_GROUP_ID = "demo:test:qq:private:10001"
DEMO_USER_ID = os.getenv("MEMO_ECHO_RUNTIME_USER_ID", "ff78ddae-979e-4714-b202-93d5f9916c69")


async def main() -> int:
    event_center = EventCenterServiceClient(
        base_url=os.getenv("EVENT_CENTER_SERVICE_BASE_URL", "http://127.0.0.1:8093"),
    )
    embedding = EmbeddingServiceClient()
    service = MemoryGraphService(
        event_center_client=event_center,
        embedding_service=embedding,
        enabled=True,
        user_id=DEMO_USER_ID,
    )

    print(f"== enabled={service.is_enabled} uri={service._neo4j_uri} user={service._neo4j_user} ==")

    # 1) 写入一条模拟 QQ 消息（带 actorType 前缀，与 P-B 事件接入约定一致）
    episode_body = (
        "[actorType=OWNER] 我今晚九点在老地方上课，跟李老师约的，地点是城南路那家咖啡店。"
    )
    result = await service.write_episode(
        name="QQ私聊:demo-10001",
        episode_body=episode_body,
        source_description="QQ 私聊消息（demo），eventId=evt-demo-0001",
        reference_time=datetime.now(timezone.utc),
        group_id=DEMO_GROUP_ID,
        # 注意：首次创建 episode 不能传 uuid（graphiti 会按已存在节点查询并抛 not found）。
        # eventId 溯源映射放入 source_description/content，P-B 事件接入时再建显式映射。
    )
    if result is None:
        print("!! write_episode 降级返回 None（写入失败，见上方日志）")
        await service.close()
        return 2

    print(f"== 写入成功 episode={result.episode.uuid} ==")
    print(f"   实体 {len(result.nodes)} 个: " + ", ".join(n.name for n in result.nodes[:10]))
    print(f"   关系边 {len(result.edges)} 条: " + ", ".join(e.fact[:40] for e in result.edges[:10]))

    # 2) 检索断言
    for query, expect in [
        ("今晚几点上课", "九点"),
        ("李老师", "李老师"),
        ("上课地点", "城南路"),
    ]:
        hits = await service.search(query, group_ids=[DEMO_GROUP_ID], num_results=5)
        if not hits:
            print(f"  [x] 检索无结果: {query}")
            continue
        facts = [e.fact for e in hits[:5]]
        matched = any(expect in (f or "") for f in facts)
        print(f"  [{'ok' if matched else '?'}] query={query!r} -> {facts[:3]}")
        if not matched:
            print("      ↑ 未直接命中期望关键词，检查提取质量")

    await service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

# =============================================================================
# api/reports.py - 上报队列接口(给上游 agent 消费)
# -----------------------------------------------------------------------------
# 定位: 这里是"上报消息的出口"。本服务只负责发现与排队,**不作对外通知**;
#       上游(主 agent)自己来取,取走后自行决定要不要报给用户、怎么报。
#
# 接口:
#   GET  /api/reports                   查队列(pending/所有状态,调试与前端展示)
#   GET  /api/reports/stats             各状态计数(看有没有积压/死信)
#   POST /api/reports/claim             批量认领(带租约;上游消费主入口)
#   POST /api/reports/{id}/ack          确认处理完成
#   POST /api/reports/{id}/drop         放弃(决定不报;也用于丢弃草稿)
#   POST /api/reports/{id}/send         把待确认草稿**真正发出去**(号主确认入口)
#   GET  /api/reports/subscribe         长轮询: 挂起等新消息(上游"看到就处理")
#
# 为什么是"认领"而不是"推送":
#   推送要求上游必须一直在线且地址稳定;认领则允许上游按自己的节奏来,
#   崩了也不会丢消息(租约到期自动重投),这也正是消息中间件的标准语义。
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import outbox
from .. import reports as reports_service
from ..config import get_settings
from .routes import _check_token

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", dependencies=[Depends(_check_token)])
def list_reports(
    status: str = "",
    lane: str = "",
    conversation_id: str = "",
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """列出上报记录(时间倒序)。

    参数:
      status: candidate / pending / claimed / acked / dropped / dead
      lane:   urgent / normal / question / digest
    """
    return reports_service.list_reports(
        status=status, lane=lane, conversation_id=conversation_id, limit=limit
    )


@router.get("/stats", dependencies=[Depends(_check_token)])
def report_stats() -> dict[str, int]:
    """各状态计数 —— 一眼看出有没有积压(pending)或坏消息(dead)。"""
    return reports_service.stats()


@router.post("/claim", dependencies=[Depends(_check_token)])
def claim_reports(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """批量认领待上报消息(上游消费主入口)。

    请求体(全可选):
    ```json
    {
      "limit": 10,                 // 一次最多认领几条
      "lane": "urgent",            // 只看某个通道(空=全部)
      "lease_seconds": 120,        // 租约时长;到期未 ack 会被重新投递
      "claimed_by": "main-agent"   // 认领者标识(审计用)
    }
    ```

    返回: {"items": [...], "count": n}
    **注意**: 认领后必须对每条调用 /ack 或 /drop,否则租约到期后会被别人再次取走
    (至少一次投递,消费方要做幂等)。
    """
    body = body or {}
    items = reports_service.claim(
        limit=int(body.get("limit") or 10),
        lane=str(body.get("lane") or ""),
        lease_seconds=int(body.get("lease_seconds") or reports_service.DEFAULT_LEASE_SECONDS),
        claimed_by=str(body.get("claimed_by") or "upstream"),
    )
    return {"items": items, "count": len(items)}


@router.post("/{record_id}/ack", dependencies=[Depends(_check_token)])
def ack_report(record_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """确认处理完成(上游已经决定并完成了对外通知)。"""
    body = body or {}
    if not reports_service.ack(record_id, claimed_by=str(body.get("claimed_by") or "")):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True, "id": record_id, "status": reports_service.STATUS_ACKED}


@router.post("/{record_id}/send", dependencies=[Depends(_check_token)])
async def send_draft(record_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一条**待确认草稿**真正发给对方(号主确认后调用)。

    与 /ack 的区别: ack 只是"我知道这条了";send 会**真的以号主身份发出消息**。
    它是全系统唯一由人触发的外发入口,所以只对 lane=draft 开放 ——
    上报/请示类记录想发什么由各自的消费方决定,不该借用这个口子。

    请求体(可选):
    ```json
    {"text": "改好的话术"}   // 不传则用草稿原文
    ```

    发送走 outbox(先落库再发送),与 agent 回复是同一条路径。
    **发送失败时记录不会被标记完成** —— 保持待处理,可以重试。
    """
    body = body or {}
    record = reports_service.get_report(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if str(record.get("lane") or "") != reports_service.LANE_DRAFT:
        raise HTTPException(status_code=400, detail="只有待确认草稿(draft)可以用本接口发送")
    if str(record.get("status") or "") in (
        reports_service.STATUS_ACKED,
        reports_service.STATUS_DROPPED,
        reports_service.STATUS_DEAD,
    ):
        raise HTTPException(status_code=409, detail=f"这条草稿已处理过: {record.get('status')}")

    payload = dict(record.get("payload") or {})
    text = str(body.get("text") or payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="草稿内容为空")
    conversation_id = str(record.get("conversation_id") or "")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="草稿缺少会话")

    # 发成功才算数: 这条由**人**点出来的发送,失败时不能在历史里留下
    # "我说过"的记录 —— 否则下一轮模型会以为话已经带到了(见 outbox.deliver)。
    result = await outbox.deliver(conversation_id, text, source="manual", record_first=False)
    if not result.get("ok"):
        # 保持待处理状态: 平台故障/网络抖动不该让一条已经拟好的回复消失
        raise HTTPException(status_code=502, detail=f"发送失败: {result.get('reason') or '未知原因'}")

    reports_service.ack(record_id, claimed_by="manual")
    return {
        "ok": True,
        "id": record_id,
        "status": reports_service.STATUS_ACKED,
        "conversation_id": conversation_id,
        "message_id": str(result.get("message_id") or ""),
    }


@router.post("/{record_id}/drop", dependencies=[Depends(_check_token)])
def drop_report(record_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """放弃这条上报(上游判断不值得打扰人)。"""
    body = body or {}
    if not reports_service.drop(
        record_id, claimed_by=str(body.get("claimed_by") or ""), reason=str(body.get("reason") or "")
    ):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True, "id": record_id, "status": reports_service.STATUS_DROPPED}


@router.get("/subscribe", dependencies=[Depends(_check_token)])
async def subscribe(
    lane: str = "",
    limit: int = Query(default=10, ge=1, le=100),
    timeout: int = Query(default=0, ge=0, le=120),
    claimed_by: str = "upstream",
) -> dict[str, Any]:
    """长轮询: 没有新消息时挂起等待,有消息立刻返回。

    解决的问题: 上游若用轮询,要么太频繁(空转),要么太稀疏(消息压在队列里)。
    长轮询让"看到就处理"这件事既实时又省请求。

    参数:
      timeout: 最长等待秒数(0 = 用配置的默认值);到点仍无消息则返回空列表,
               上游重新发起即可。
    """
    wait_seconds = timeout or max(1, int(get_settings().notify_poll_seconds))
    interval = 0.5
    waited = 0.0
    while True:
        items = reports_service.claim(
            limit=limit, lane=lane, claimed_by=claimed_by
        )
        if items:
            return {"items": items, "count": len(items)}
        if waited >= wait_seconds:
            return {"items": [], "count": 0}
        await asyncio.sleep(interval)
        waited += interval

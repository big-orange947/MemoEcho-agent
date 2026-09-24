# =============================================================================
# api/ui.py - 前端(值守控制台)专用接口
# -----------------------------------------------------------------------------
# 定位: routes.py 是"会话/消息/命令"接口,已经很长了;本文件收拢**只有前端
#       UI 才需要**的几类读取/配置接口,避免把 routes.py 撑成什么都往里塞的大文件。
#
# 接口:
#   GET  /api/tools           可用工具清单(含高危标记 + 私聊/群聊默认授权)
#   GET  /api/configs         全局配置(设置页)
#   PUT  /api/configs/{key}   写单个配置(**白名单**,见下)
#   GET  /api/goals           跨会话目标列表("任务进度"面板)
#
# 三条刻意的设计(都不是风格问题):
#
#   1. 工具清单**从图实例取**,不另写一份工具表。
#      另写一份的失效方式是静默的:新工具加了、清单没同步,前端就少显示一个,
#      没人会发现。同理 default_for 用 policy.resolve_allowed_tools 探测,
#      而不是在这里复述"群聊不给 send_qq_message"的规则 —— 规则只有一处。
#
#   2. 配置**只允许白名单键**。
#      configs 表里混着两类东西:用户设置(联系人别名/上报白名单)与
#      服务自己维护的内部状态(如日报用量计数器 alert_llm_usage:*)。
#      对前端开一个"任意键写入"的口子,等于让一次误操作就能把配额计数清零
#      (结果是当天预算失控)或改坏服务状态,而且这类故障排查起来毫无线索。
#      所以这里显式列白名单,其余一律 400 并说明原因。
#
#   3. 目标列表是**跨会话**的。
#      会话详情页用 /api/conversations/{id}/goals;而"我托 agent 办的事都到哪一步了"
#      是全局视角,前端若自己拼就得先拉全部会话再逐个查目标(N+1 次请求)。
#
# 鉴权: 与 routes.py 一致(api_token 为空则放行)。
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..agent.runtime import get_graph
from ..services import configs as configs_service
from ..services import goals as goals_service
from ..services import policy as policy_service
from .routes import _check_token

router = APIRouter(prefix="/api", tags=["ui"])

# 允许前端写入的配置键(**白名单**,只增不减要慎重)。
#   alert_contacts   上报白名单(这些人说的话天然重要,见 reports._in_watchlist)
#   contact_aliases  联系人别名表("小号" → qq 号,见 tools/contacts.resolve_contact)
# 这两个都是"人配的业务数据",改错了只是行为不合预期,不会破坏服务自身状态。
UI_WRITABLE_CONFIG_KEYS = ("alert_contacts", "contact_aliases")

# 目标状态取值(与 db.py 的 goals.status 注释一致)
GOAL_STATUSES = ("active", "done", "abandoned")

# 目标对外字段(显式列出,不直接把数据库行丢给前端 ——
# 行里还有 completed_at 等内部列,返回面越小越不容易在改库时意外泄漏)
GOAL_VIEW_FIELDS = (
    "id",
    "conversation_id",
    "objective",
    "status",
    "progress",
    "created_at",
    "updated_at",
)


# ---------------------------------------------------------------------------
# 工具清单
# ---------------------------------------------------------------------------
@router.get("/tools", dependencies=[Depends(_check_token)])
def list_tools() -> list[dict[str, Any]]:
    """返回当前可用的工具清单,供前端"工具授权"面板渲染。

    返回:
    ```json
    [{"name": "send_qq_message", "description": "给指定的人发消息",
      "high_risk": true, "tags": ["high_risk"],
      "default_for": {"private": true, "group": false}}]
    ```

    `default_for` = 该工具在私聊/群聊下的**默认**授权(会话没显式配 allowed_tools 时)。
    判定用 policy_service.resolve_allowed_tools 同一套规则(构造一个假的会话 dict 探测),
    这样"前端显示默认不给"与"图实际不给"永远是同一个结论。

    图未初始化(如仅导入 API 做工具扫描)时返回空数组 —— 一个只读接口不该因为
    "服务还没组装完"就抛 500。
    """
    graph = get_graph()
    if graph is None:
        return []

    tools = list(getattr(graph, "tools", None) or [])
    # registry: {工具名: 标签列表} —— 与 graph/_tools_for、policy 的约定一致
    registry = {tool.name: list(getattr(tool, "tags", None) or []) for tool in tools}

    # 只探测"会话类型"这一个变量: allowed_tools 留空 = 走默认集,
    # 正是前端要展示的那个默认值。
    default_private = policy_service.resolve_allowed_tools({"chat_type": "private"}, registry)
    default_group = policy_service.resolve_allowed_tools({"chat_type": "group"}, registry)

    return [
        {
            "name": tool.name,
            "description": (getattr(tool, "description", "") or "").strip(),
            "high_risk": policy_service.is_high_risk(tool.name, registry[tool.name]),
            "tags": registry[tool.name],
            "default_for": {
                "private": tool.name in default_private,
                "group": tool.name in default_group,
            },
        }
        for tool in tools
    ]


# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------
@router.get("/configs", dependencies=[Depends(_check_token)])
def get_configs() -> dict[str, dict[str, str]]:
    """读取全部全局配置(设置页初始化用)。

    原样返回 configs 表的全部键值:读是只读的,前端要不要展示某个键由它自己决定
    (内部键如 alert_llm_usage:* 也在里面,但**写**被下面的白名单挡住)。
    """
    return {"configs": configs_service.get_all_configs()}


@router.put("/configs/{key}", dependencies=[Depends(_check_token)])
def put_config(key: str, body: dict[str, Any]) -> dict[str, Any]:
    """写入单个配置键(仅白名单键)。

    请求体: {"value": "..."}
    返回:   {"key": "...", "value": "..."}

    为什么只放白名单: 见文件头第 2 条 —— 这个口子一旦通用化,
    前端一次误操作就能改掉服务自己维护的内部状态(如日报模型用量计数)。
    拒绝时明确说明原因与允许的键,便于前端把错误直接显示给人看。

    value 允许传字符串或结构化对象: configs 表存的就是字符串,
    而联系人别名本身是 JSON 对象,前端直接传 dict 时这里统一序列化,
    免得每个调用方各写一遍 json.dumps(读侧 reports/contacts 也按 JSON 解析)。
    """
    if key not in UI_WRITABLE_CONFIG_KEYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"配置键 {key} 不允许通过接口写入;"
                f"仅允许: {list(UI_WRITABLE_CONFIG_KEYS)}。"
                "其余键(如内部用量计数 alert_llm_usage:*)由服务自行维护,"
                "外部改写会破坏业务状态。"
            ),
        )

    if "value" not in body or body["value"] is None:
        raise HTTPException(status_code=400, detail="value 必填")

    value = body["value"]
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)

    configs_service.set_config(key, value)
    return {"key": key, "value": value}


# ---------------------------------------------------------------------------
# 跨会话目标(任务进度面板)
# ---------------------------------------------------------------------------
@router.get("/goals", dependencies=[Depends(_check_token)])
def list_goals(
    status: str = "",
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """返回最近的**跨会话**目标列表(按 updated_at 倒序)。

    参数:
      status: active / done / abandoned;空 = 全部
      limit:  返回条数(默认 50,上限 200)

    为什么要跨会话: 前端"任务进度"面板回答的是"我托 agent 办的事都怎么样了",
    这是全局视角 —— 逐会话查会把一次渲染变成 N+1 次请求。
    非法 status 直接 400(而不是静默返回全部): 拼错的过滤器若被忽略,
    前端会拿一堆"已完成"的目标去渲染进行中列表,而看不出哪里错了。
    """
    wanted = str(status or "").strip()
    if wanted and wanted not in GOAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status 必须是 {list(GOAL_STATUSES)} 之一(空 = 全部)",
        )

    rows = goals_service.list_recent_goals(limit=limit, status=wanted)
    return [
        {field: (row.get(field) if row.get(field) is not None else "") for field in GOAL_VIEW_FIELDS}
        for row in rows
    ]

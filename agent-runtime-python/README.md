# Agent Runtime

这个运行时主要负责：

- route 分类
- 执行计划生成
- 领域 Agent 调度
- Tool 调用中介
- 结果聚合

本地启动：

```bash
uvicorn app.main:app --reload
```

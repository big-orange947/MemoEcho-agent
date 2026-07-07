# task-service

`task-service` stores structured work tasks extracted by the Python runtime.

Current responsibilities:

1. create task items from normalized agent output
2. keep idempotency by `sourceEventId`
3. list tasks for later query and UI integration

## Endpoints

- `POST /internal/tasks`
- `GET /internal/tasks`
- `GET /actuator-like/health`

from fastapi import FastAPI

from app.orchestrator.service import OrchestratorService
from app.schemas.events import UnifiedEvent
from app.schemas.results import OrchestratorResult


app = FastAPI(title="Memo Echo Agent Runtime", version="0.1.0")
orchestrator = OrchestratorService.build_default()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/events/handle", response_model=OrchestratorResult)
async def handle_event(event: UnifiedEvent) -> OrchestratorResult:
    return await orchestrator.handle_event(event)


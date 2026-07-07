from app.schemas.events import UnifiedEvent


class MemoryManager:
    def build_history_context(self, event: UnifiedEvent) -> list[dict]:
        return []

    def build_retrieved_knowledge(self, event: UnifiedEvent) -> list[dict]:
        return []


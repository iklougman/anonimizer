import decimal
import uuid

from app.db.repositories.base import BaseRepository
from app.models import LLMRequest


class LLMRequestRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        provider: str,
        model: str,
        sanitized_prompt: str,
        sanitized_response: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: decimal.Decimal,
        latency_ms: int,
    ) -> LLMRequest:
        request = LLMRequest(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider=provider,
            model=model,
            sanitized_prompt=sanitized_prompt,
            sanitized_response=sanitized_response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self.session.add(request)
        self.session.flush()
        return request

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)


async def _database_unavailable(_: Request, error: Exception) -> JSONResponse:
    # OperationalError cobre conexão recusada, queda de conexão e statement_timeout estourado.
    logger.error("banco indisponível", exc_info=error)
    body = ErrorResponse(detail="serviço temporariamente indisponível")
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(OperationalError, _database_unavailable)

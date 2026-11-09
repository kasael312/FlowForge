"""FastAPI application entry point for FlowForge."""

from fastapi import FastAPI

app = FastAPI(title="FlowForge", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by docker-compose and the frontend."""
    return {"status": "ok"}

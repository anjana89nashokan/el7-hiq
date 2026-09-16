import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List

router = APIRouter()

class LogMessage(BaseModel):
    message: str
    level: str  # e.g., "INFO", "ERROR"

# Store active connections
active_connections: List[WebSocket] = []

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    print(f"New WebSocket connected. Active connections: {len(active_connections)}")

    try:
        while True:
            try:
                # Wait for messages or keep alive
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # You can log incoming messages if needed
                print(f"Received from client: {data}")
            except asyncio.TimeoutError:
                # Send ping every 30s to keep the connection alive
                try:
                    await websocket.send_text("[ping]")
                except Exception:
                    break
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"WebSocket error: {e}")
                break
    finally:
        # Clean up
        if websocket in active_connections:
            active_connections.remove(websocket)
        print(f"WebSocket disconnected. Active connections: {len(active_connections)}")


class WebSocketLogHandler(logging.Handler):
    def emit(self, record):
        if not active_connections:
            return

        log_entry = self.format(record)

        for ws in active_connections.copy():
            try:
                if ws.client_state.name != "DISCONNECTED":
                    asyncio.create_task(self._safe_send(ws, log_entry))
                else:
                    if ws in active_connections:
                        active_connections.remove(ws)
            except Exception as e:
                print(f"Error checking WebSocket state: {e}")
                if ws in active_connections:
                    active_connections.remove(ws)

    async def _safe_send(self, websocket: WebSocket, message: str):
        try:
            if websocket.client_state.name != "DISCONNECTED" and "vertexai.agent_engines" not in message:
                await websocket.send_text(message)
        except Exception as e:
            print(f"Failed to send log: {e}")
            if websocket in active_connections:
                active_connections.remove(websocket)


class InfoWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in (logging.INFO, logging.WARNING)


# Setup logging
root_logger = logging.getLogger()
handler = WebSocketLogHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
handler.addFilter(InfoWarningFilter())
root_logger.addHandler(handler)


# Broadcast utility
async def broadcast_log_message(message: str, level: str = "INFO"):
    if not active_connections:
        return

    log_entry = f"{level}: {message}"
    for ws in active_connections.copy():
        try:
            if ws.client_state.name != "DISCONNECTED":
                await ws.send_text(log_entry)
            else:
                if ws in active_connections:
                    active_connections.remove(ws)
        except Exception as e:
            print(f"Failed to broadcast: {e}")
            if ws in active_connections:
                active_connections.remove(ws)


# Cleanup dead sockets
async def cleanup_dead_connections():
    while True:
        for ws in active_connections.copy():
            if ws.client_state.name == "DISCONNECTED":
                if ws in active_connections:
                    active_connections.remove(ws)
        await asyncio.sleep(30)


# Startup hook to run cleanup. Schedule lazily so importing this module does not
# require a running event loop (uvicorn imports the app before the loop starts).
try:
    asyncio.get_running_loop().create_task(cleanup_dead_connections())
except RuntimeError:
    # No loop yet at import time; start the cleanup task on first websocket connect.
    pass

import os
import sqlite3
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import customers, notifications, orders, products
from app.database import Database


def create_app(database_path=None, start_worker=None):
    database = Database(database_path if database_path is not None else os.getenv("LAB_DB_PATH", "data/store.sqlite3"))

    @asynccontextmanager
    async def lifespan(application):
        database.initialize()
        worker = None
        if start_worker if start_worker is not None else os.getenv("LAB_WORKER", "1") == "1":
            worker = notifications.WorkerThread(notifications.NotificationWorker(database))
            worker.start()
        yield
        if worker is not None:
            worker.stop()

    application = FastAPI(title="Tienda Lab", version="1.0.0", lifespan=lifespan)
    application.state.database = database
    application.include_router(products.router)
    application.include_router(customers.router)
    application.include_router(orders.router)
    application.include_router(notifications.router)

    @application.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, error: sqlite3.OperationalError):
        return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible; reintente más tarde"})

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

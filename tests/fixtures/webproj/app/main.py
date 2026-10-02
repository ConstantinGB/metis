"""HTTP API for customers.

Serves customer records over HTTP and reads its database location from the environment.
"""
import os

from fastapi import FastAPI

from app.db import fetch_customer
from app.services import Other, Service

app = FastAPI()
DATABASE_URL = os.environ["DATABASE_URL"]
DEBUG = os.getenv("DEBUG")


@app.get("/customers/{customer_id}")
def get_customer(customer_id: int):
    """Return one customer by id."""
    svc = Service()
    svc.run()
    return fetch_customer(customer_id)


@app.post("/customers")
def create_customer(payload: dict):
    other = Other()
    other.run()
    return payload


def process(fetch_customer):
    # the parameter shadows the imported function: no edge expected
    return fetch_customer()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app)

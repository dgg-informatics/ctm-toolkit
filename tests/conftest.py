import os

# Use a dedicated test database so tests never touch ctm production data.
os.environ["CTM_MONGO_DB"] = "ctm_test"

import pytest
from pymongo import MongoClient


MONGO_URI = os.getenv("CTM_MONGO_URI", "mongodb://localhost:27017")


@pytest.fixture(scope="session")
def mongo_client():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    client.admin.command("ping")
    yield client
    client.close()


@pytest.fixture
def db(mongo_client):
    database = mongo_client["ctm_test"]
    yield database
    for name in database.list_collection_names():
        database[name].drop()

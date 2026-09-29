"""Stand-ins for the pymongo objects the pipeline talks to.

Deliberately mirror what the real classes *forbid* as well as what they allow:
``pymongo.database.Database`` is not iterable, so ``"trial" in db`` raises there
and must raise here. A double built from a plain dict would accept it and hide a
crash that only appears against a real server.
"""
from bson import ObjectId


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        self._docs = sorted(self._docs, key=lambda d: d[key], reverse=direction < 0)
        return self

    def limit(self, n):
        return iter(self._docs[:n])

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.replaced = []
        self.was_dropped = False

    def find(self, query=None, projection=None):
        return FakeCursor(self.docs)

    def find_one(self, query):
        return next((d for d in self.docs if d["_id"] == query["_id"]), None)

    def count_documents(self, query):
        return len(self.docs)

    def replace_one(self, query, doc, upsert=False):
        self.replaced.append(doc)
        self.docs = [d for d in self.docs if d["_id"] != query["_id"]] + [doc]

    def drop(self):
        self.docs = []
        self.was_dropped = True


class FakeDatabase:
    def __init__(self, collections=None):
        self._collections = collections or {}
        self.dropped = []

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())

    def list_collection_names(self):
        return list(self._collections)

    def drop_collection(self, name):
        self[name].drop()
        self.dropped.append(name)

    def __iter__(self):
        raise TypeError("'Database' object is not iterable")


class FakeClient:
    def __init__(self, databases=None):
        self._databases = databases or {}

    def __getitem__(self, name):
        return self._databases.setdefault(name, FakeDatabase())


def oid_at(when):
    return ObjectId.from_datetime(when)


def docs_written_at(when, n=3):
    """`n` documents whose ObjectIds carry `when` as their creation time —
    which is exactly what the watermark reads."""
    return [{"_id": oid_at(when)} for _ in range(n)]

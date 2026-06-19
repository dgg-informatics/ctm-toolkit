"""MatchMiner patient (query) schema — placeholder.

MatchMiner expects a patient document that includes clinical attributes and
genomic alterations in its own format. Fields here should be populated once
the MatchMiner API / data contract is confirmed.

Reference: https://matchminer.gitbook.io (or internal docs)
"""
from pydantic import BaseModel


class MatchMinerPatient(BaseModel):
    # TODO: define fields from MatchMiner patient query spec
    pass

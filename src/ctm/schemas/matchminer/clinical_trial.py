"""MatchMiner clinical trial (reference) schema — placeholder.

MatchMiner stores clinical trials in a CTML (Clinical Trial Markup Language)
format. Fields here should be populated once the CTML spec is confirmed.

Reference: https://matchminer.gitbook.io (or internal docs)
"""
from pydantic import BaseModel


class MatchMinerClinicalTrial(BaseModel):
    # TODO: define fields from MatchMiner CTML spec
    pass

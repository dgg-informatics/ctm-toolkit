# Raw clinical trial input shape.
#
# JSON form (MatchMiner export):
# {
#   "nct_id": str,              # e.g. "NCT02477839"
#   "protocol_no": str,         # e.g. "NCT02477839"
#   "title": str,               # trial title (not always present in raw export)
#   "trial_summary_status": str,# e.g. "open"
#   "match_level": str,         # e.g. "step"
#   "reason_type": str,         # e.g. "genomic"
#   "cancer_type_match": str,   # e.g. "specific"
#   "match_type": str,          # e.g. "gene"
#   ...                         # additional MatchMiner bookkeeping fields (ignored)
# }
#
# Word doc form: unstructured eligibility criteria — not yet handled.

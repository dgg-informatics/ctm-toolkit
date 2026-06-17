# Raw patient general input shape.
#
# JSON form:
# {
#   "mrn": str,
#   "gender": str,                                    # e.g. "Female"
#   "vital_status": str,                              # e.g. "alive"
#   "oncotree_primary_diagnosis_name": str,           # e.g. "Non-Small Cell Lung Cancer"
#   "tumor_mutational_burden_per_megabase": float,    # e.g. 4.1
#   "report_date": str,                               # ISO date or ""
#   "clinical_id": {"$oid": str},                     # Mongo ObjectId
#   ...
# }

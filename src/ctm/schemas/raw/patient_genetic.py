# Raw patient genetic input shape.
#
# JSON form (genomic alteration record):
# {
#   "sample_id": str,
#   "mrn": str,
#   "true_hugo_symbol": str,              # e.g. "EGFR"
#   "true_protein_change": str,           # e.g. "p.L858G"
#   "true_cdna_change": str,              # e.g. "c.2572T>G"
#   "true_variant_classification": str,   # e.g. "Missense_Mutation"
#   "variant_category": str,              # e.g. "MUTATION"
#   "allele_fraction": float,             # e.g. 0.29
#   "tier": int,                          # 1-4
#   "chromosome": str,                    # e.g. "7"
#   "position": int,
#   "reference_allele": str,
#   "wildtype": bool,
#   "cnv_call": str | null,
#   "mmr_status": str | null,
#   ...
# }

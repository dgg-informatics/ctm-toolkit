"""Assemble a self-contained match database and hand it to matchengine.

`ctm-mm match-prep` copies the current master trials and the latest patient
clinical/genomic into a dated ``<date>_match`` database, under matchengine's default
collection names (``trial`` / ``clinical`` / ``genomic``), so vanilla matchengine
matches against one frozen, reproducible snapshot — no fork changes needed.

With ``--run`` it invokes matchengine, passing a ``SECRETS_JSON`` synthesized from
ctm's own ``.env`` so the two tools share one connection config (no separate secrets
file). This module holds the two pure pieces — secrets synthesis and the command —
so they can be tested without Mongo or a subprocess.
"""

# matchengine's default patient collection names in the patient database, i.e. what
# `ctm-mm load` refreshes each run.
DEFAULT_CLINICAL_COLLECTION = "latest_clinical"
DEFAULT_GENOMIC_COLLECTION = "latest_genomic"


def synthesize_secrets(config: dict, db_name: str) -> dict:
    """The dict matchengine's ``DefaultDBSecrets`` reads, built from ctm's ``.env``
    config and pointed at ``db_name``.

    A ``MONGO_URI`` is decomposed into the host/port/credentials matchengine wants
    (it builds its own URI from parts and has no way to accept a full one); a bare
    host/port passes straight through. Credentials fill both the read-only and
    read-write slots — matchengine reads with the former and writes ``trial_match``
    with the latter.
    """
    if config.get("uri"):
        from pymongo import uri_parser

        parsed = uri_parser.parse_uri(config["uri"])
        host, port = parsed["nodelist"][0]
        secrets = {"MONGO_HOST": host, "MONGO_PORT": port, "MONGO_DBNAME": db_name}
        user, password = parsed.get("username"), parsed.get("password")
        if user:
            secrets.update({
                "MONGO_USERNAME": user, "MONGO_PASSWORD": password,
                "MONGO_RO_USERNAME": user, "MONGO_RO_PASSWORD": password,
            })
        auth_source = (parsed.get("options") or {}).get("authSource")
        if auth_source:
            secrets["MONGO_AUTH_SOURCE"] = auth_source
        return secrets

    return {
        "MONGO_HOST": config["host"],
        "MONGO_PORT": config["port"],
        "MONGO_DBNAME": db_name,
    }


def matchengine_command(match_db: str) -> list[str]:
    """The matchengine invocation for the assembled db. Matchengine's defaults cover
    config-path and plugin-dir; only the database differs run to run."""
    return ["matchengine", "match", "--db", match_db]

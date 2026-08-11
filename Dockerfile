FROM python:3.11-slim

# WeasyPrint needs Pango and friends; git is needed to install matchengine,
# which is published only as a GitHub fork (not on PyPI).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libffi-dev \
    libjpeg-dev \
    libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# matchengine changes far less often than this repo — install it first so the
# slow git clone stays cached when only ctm source changes.
RUN pip install --no-cache-dir "git+https://github.com/wintermutant/matchengine-V2"

# Copy only what the build backend needs, so `pip install` is not re-run every
# time a data file or script changes. Report templates, static CSS, and the
# gene/variant reference data all live under src/ctm/ and ship in the wheel.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# [all] = report (WeasyPrint) + preview (livereload) + llm (openai, dotenv);
# [dev] adds pytest. This is a dev-server image, so it carries everything.
RUN pip install --no-cache-dir ".[all,dev]"

# Remaining project files: intake templates, helper scripts, and the suite so
# `pytest` can be run inside the container.
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

# LLM response caches default to ~/.cache/ctm. Mount a volume here (see
# docker-compose.yml) so curation runs are not re-billed on every restart.
ENV XDG_CACHE_HOME=/cache
VOLUME ["/cache"]

CMD ["bash"]

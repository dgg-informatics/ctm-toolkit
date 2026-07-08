FROM python:3.11-slim

# WeasyPrint needs Pango and friends; git needed to install matchengine from GitHub
RUN apt-get update && apt-get install -y \
    git \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libffi-dev \
    libjpeg-dev \
    libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
RUN pip install --no-cache-dir \
    "ctm-toolkit[report]" \
    "git+https://github.com/wintermutant/matchengine-V2"

# Copy project files (data, templates, static, scripts)
COPY . .

CMD ["bash"]

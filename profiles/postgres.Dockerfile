ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: PostgreSQL 17 server+client binaries on PATH (no service), pgvector, FastAPI/uvicorn/SQLAlchemy/psycopg/httpx venv

# Distro PostgreSQL (>= 16). The server is NOT started or enabled: tests run their own throwaway
# instance in a temp dir (initdb + pg_ctl). create_main_cluster=false stops the package from
# making a default cluster at install time.
RUN mkdir -p /etc/postgresql-common \
    && printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql postgresql-client postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*

# The distro keeps initdb, pg_ctl and postgres out of PATH (/usr/lib/postgresql/<v>/bin); link them
# next to psql so the unprivileged node user finds them.
RUN pgbin="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)" \
    && for b in initdb pg_ctl postgres psql; do ln -sf "$pgbin/$b" "/usr/local/bin/$b"; done \
    && postgres --version && initdb --version && pg_ctl --version && psql --version \
    && ls /usr/share/postgresql/*/extension/vector.control

# Core Python packages for the kata, preinstalled so agents never need a blocked `pip install`.
# Own venv under /opt (works regardless of $HOME): run /opt/pg-venv/bin/python, or put /opt/pg-venv/bin first on PATH.
# Pinned versions: set an ARG empty for newest.
ARG FASTAPI_VERSION=
ARG UVICORN_VERSION=
ARG SQLALCHEMY_VERSION=
ARG PSYCOPG_VERSION=
ARG HTTPX_VERSION=
RUN uv venv -q /opt/pg-venv \
    && uv pip install -q --python /opt/pg-venv/bin/python \
        "fastapi${FASTAPI_VERSION:+==$FASTAPI_VERSION}" \
        "uvicorn${UVICORN_VERSION:+==$UVICORN_VERSION}" \
        "sqlalchemy${SQLALCHEMY_VERSION:+==$SQLALCHEMY_VERSION}" \
        "psycopg[binary]${PSYCOPG_VERSION:+==$PSYCOPG_VERSION}" \
        "httpx${HTTPX_VERSION:+==$HTTPX_VERSION}" \
    && chmod -R a+rX /opt/pg-venv \
    && /opt/pg-venv/bin/python -c "import fastapi, uvicorn, sqlalchemy, psycopg, httpx"

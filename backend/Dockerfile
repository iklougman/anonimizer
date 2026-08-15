FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .

# Build time, never runtime (design spec §6, ADR-0001). The German spaCy model and
# the ORDO / Krankenhausverzeichnis reference data are baked into the image so the
# running container needs no network access to detect or risk-score anything, and
# nothing under app/privacy_gateway/ ever imports a network-capable module.
RUN python -m spacy download de_core_news_lg
RUN python scripts/fetch_reference_data.py

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

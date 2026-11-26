FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config
COPY demo_data ./demo_data
RUN mkdir -p /app/data && useradd --uid 10001 --create-home ledgerline && chown -R ledgerline /app
USER ledgerline
EXPOSE 8000
CMD ["uvicorn", "accounting_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]

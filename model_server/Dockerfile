FROM python:3.11-slim

# No torch, no transformers — lean serving only.
RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system -e .

COPY . .

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]

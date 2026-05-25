FROM python:3.11-slim

RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system -e .

COPY . .

EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]

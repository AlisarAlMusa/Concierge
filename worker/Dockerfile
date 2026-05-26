FROM python:3.11-slim

RUN pip install uv

WORKDIR /app

COPY pyproject.toml .
RUN uv pip install --system -e .

COPY . .

CMD ["python", "-m", "app.main"]

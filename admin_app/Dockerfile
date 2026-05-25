FROM python:3.11-slim

RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system -e .

COPY . .

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]

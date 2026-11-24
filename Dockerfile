FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# Pass the key at runtime: docker run -e ANTHROPIC_API_KEY=... -p 8501:8501 spendsense
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]

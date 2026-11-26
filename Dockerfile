FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[demo]"
COPY app.py ./
COPY space ./space
COPY docs ./docs
RUN useradd -m sopguard && chown -R sopguard /app
USER sopguard
EXPOSE 8080 7860
# default: supervision API + OpenAI-compatible proxy. Override with `python app.py` for the Gradio demo.
CMD ["sopguard", "serve", "--host", "0.0.0.0", "--port", "8080"]

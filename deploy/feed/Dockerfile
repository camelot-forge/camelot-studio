# The feed service (`F §D3`): the same image serves locally and online, with the
# public URL and the game address coming from the environment.
FROM python:3.13-slim

WORKDIR /app
COPY core/camelot/__init__.py core/camelot/feed.py core/camelot/feedserve.py /app/camelot/

ENV PYTHONUNBUFFERED=1 \
    CAMELOT_FEED_DIR=/feed \
    CAMELOT_FEED_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

ENTRYPOINT ["python", "-m", "camelot.feedserve"]

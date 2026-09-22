FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY worker.py ./

# named volumes inherit this ownership on first mount → nobody can persist seen.json
RUN mkdir /data && chown nobody:nogroup /data
VOLUME /data

USER nobody
CMD ["python", "worker.py"]

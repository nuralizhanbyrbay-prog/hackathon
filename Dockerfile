FROM python:3.11.11-slim@sha256:081075da77b2b55c23c088251026fb69a7b2bf92471e491ff5fd75c192fd38e5
WORKDIR /app
COPY . /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home runner && chown -R runner:runner /app
USER runner
CMD ["python", "-m", "auditlab.verify"]

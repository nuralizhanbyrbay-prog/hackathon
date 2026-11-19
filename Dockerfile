FROM python:3.11.11-slim
WORKDIR /app
COPY . /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home runner && chown -R runner:runner /app
USER runner
CMD ["python", "-m", "auditlab.verify"]

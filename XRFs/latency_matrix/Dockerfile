FROM python:3.12-slim
WORKDIR /app
COPY xrf_latency_matrix.py .
RUN pip install flask requests
CMD ["python", "xrf_latency_matrix.py"]
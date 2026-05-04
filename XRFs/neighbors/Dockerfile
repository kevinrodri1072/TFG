FROM python:3.12-slim
WORKDIR /app
COPY xrf_neighbors.py .
RUN pip install flask requests
CMD ["python", "xrf_neighbors.py"]
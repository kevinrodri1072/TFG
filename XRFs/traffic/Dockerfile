FROM python:3.12-slim
WORKDIR /app
COPY xrf_traffic.py .
RUN pip install flask requests
CMD ["python", "xrf_traffic.py"]
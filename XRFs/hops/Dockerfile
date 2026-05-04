FROM python:3.12-slim
WORKDIR /app
COPY xrf_hops.py .
RUN pip install flask requests
CMD ["python", "xrf_hops.py"]
FROM python:3-slim

ARG BUILD_ARCH

LABEL io.hass.type="app" \
    io.hass.version="1.0.0" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /pi-reporter

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pi-reporter.py .

CMD ["python3", "-u", "pi-reporter.py"]

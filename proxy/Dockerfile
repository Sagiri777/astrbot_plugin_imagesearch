FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11-utils x11vnc novnc websockify fluxbox \
    && rm -rf /var/lib/apt/lists/*
COPY proxy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy /app/proxy
ENV SOUTUBOT_BROWSER_DATA=/data/browser SOUTUBOT_HEADLESS=true
EXPOSE 8787 6080
CMD ["python", "-m", "uvicorn", "proxy.app:app", "--host", "0.0.0.0", "--port", "8787"]

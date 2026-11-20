FROM python:3.12-slim
WORKDIR /app
RUN useradd --uid 10001 --create-home bot
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py ./
USER bot
CMD ["python", "bot.py"]

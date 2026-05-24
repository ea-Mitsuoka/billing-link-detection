FROM python:3.12-slim

# 必要に応じて OS パッケージを追加（現状は CA 証明書のみで十分）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存パッケージのインストール（レイヤキャッシュを効かせるため main.py より先に COPY）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Cloud Run Jobs はエントリポイント実行で完了するため CMD で起動
CMD ["python", "main.py"]

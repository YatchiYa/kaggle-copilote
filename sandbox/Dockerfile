# Throwaway image for experiment scripts. Runs with --network none and no secrets mounted.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pandas numpy scipy scikit-learn lightgbm xgboost catboost optuna category_encoders statsmodels pyarrow
# CPU PyTorch for neural-network models (ensemble diversity); no GPU on this host.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
WORKDIR /out

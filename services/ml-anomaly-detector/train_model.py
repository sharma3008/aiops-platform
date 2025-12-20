import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
import joblib

# Generate synthetic normal data
cpu = np.random.uniform(10, 70, 2000)
memory = np.random.uniform(20, 80, 2000)
latency = np.random.uniform(5, 150, 2000)

data = pd.DataFrame({
    "cpu_avg_5": cpu,
    "memory_avg_5": memory,
    "latency_avg_5": latency
})

model = IsolationForest(contamination=0.05, random_state=42)
model.fit(data)

joblib.dump(model, "model.pkl")
print("Model saved as model.pkl")

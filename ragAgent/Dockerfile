# Dockerfile para la interfaz de Streamlit
FROM python:3.11-slim

# Evita que Python genere archivos .pyc y asegura que los logs se vean en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalamos curl para el Healthcheck
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiamos e instalamos dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todo el contenido del proyecto
COPY . .

# Exponemos el puerto de Streamlit
EXPOSE 8501

# Variables de entorno por defecto (App Runner las puede sobrescribir)
ENV RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:911268715109:runtime/ragAgent_MyAgent-1twvtn9Ewz"
ENV REGION="us-east-1"

# Healthcheck para que AWS sepa que el contenedor está vivo
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

# Comando para ejecutar Streamlit
ENTRYPOINT ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]

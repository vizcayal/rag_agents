FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Install dependencies first for better Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# AgentCore Runtime expects the app on port 8080
EXPOSE 8080

# Run the entrypoint — BedrockAgentCoreApp binds to 0.0.0.0:8080
ENTRYPOINT ["python", "main.py"]

# Dockerfile for Haven CLI and TUI
# 
# Build:
#   docker build -t haven-cli .
#
# Run CLI:
#   docker run -it --rm haven-cli haven --help
#
# Run TUI:
#   docker run -it --rm haven-cli haven-tui
#
# With volume for config:
#   docker run -it --rm -v ~/.config/haven:/root/.config/haven haven-cli haven-tui

FROM python:3.14-slim

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY pyproject.toml README.md ./
COPY wheels/ ./wheels/

# Install the package with TUI support
RUN pip install --no-cache-dir -e ".[tui]"

# Copy the application code
COPY haven_cli/ ./haven_cli/
COPY haven_tui/ ./haven_tui/

# Reinstall to include the copied source
RUN pip install --no-cache-dir -e ".[tui]"

# Create config directory
RUN mkdir -p /root/.config/haven

# Set the default entrypoint
ENTRYPOINT ["haven"]

# Default command shows help
CMD ["--help"]

#!/bin/bash
echo "Setting up MTG Proxy Forge..."

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r mtg_proxy_forge/requirements.txt

# Run the server
echo "Starting MTG Proxy Forge..."
echo "Go to http://127.0.0.1:8000 in your browser."

cd mtg_proxy_forge/backend
uvicorn main:app --reload

#!/bin/bash

# Start FastAPI + Discord Bot together
gunicorn main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT

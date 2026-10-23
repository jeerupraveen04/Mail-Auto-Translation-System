#!/usr/bin/env python3
"""Dev entrypoint: uvicorn app.main:app (backend) with static frontend."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, app_dir="backend", reload=False)

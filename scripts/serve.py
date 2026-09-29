#!/usr/bin/env python3
"""Launch uvicorn on 0.0.0.0:8080."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("gmp.api.app:app", host="0.0.0.0", port=8080, workers=1)

"""Alternative to the /demo/openai endpoint: run the demo build from your
laptop against the deployed service.

Usage:  python scripts/run_demo.py https://<your-railway-domain>
"""
import sys

import httpx

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"
resp = httpx.post(f"{base}/demo/openai", timeout=120)
resp.raise_for_status()
print(resp.json())

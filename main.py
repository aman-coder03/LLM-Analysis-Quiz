# main.py
import asyncio
import base64
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

import httpx
import pandas as pd
import pdfplumber
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

SERVER_EMAIL = os.getenv("SERVER_EMAIL", "you@example.com")
SERVER_SECRET = os.getenv("SERVER_SECRET", "change-me")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "25"))
SOLVER_DEADLINE = float(os.getenv("SOLVER_DEADLINE", "170"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("quiz-solver")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["*"]
)

class QuizRequest(BaseModel):
    email: str
    secret: str
    url: str
    class Config:
        extra = "allow"

@dataclass
class SolveContext:
    email: str
    secret: str
    start_ts: float
    deadline_s: float

# ---------------------------
# DNS-over-HTTPS resolver
# ---------------------------
CLOUDFLARE_DOH = "https://cloudflare-dns.com/dns-query"

async def resolve_domain(hostname: str) -> Optional[str]:
    """Resolve hostname to an IP using Cloudflare DNS-over-HTTPS (DoH)."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"accept": "application/dns-json"}) as client:
            r = await client.get(CLOUDFLARE_DOH, params={"name": hostname, "type": "A"})
            r.raise_for_status()
            j = r.json()
            answers = j.get("Answer") or []
            # pick first A record
            for ans in answers:
                data = ans.get("data")
                if data and re.match(r"^\d+\.\d+\.\d+\.\d+$", str(data)):
                    return data
    except Exception as e:
        logger.debug(f"DoH resolution failed for {hostname}: {e}")
    return None

# ---------------------------
# Playwright fetch (uses DoH-resolved IP)
# ---------------------------
async def fetch_rendered_html(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise RuntimeError("Invalid URL: no hostname")

    ip = await resolve_domain(hostname)
    # If DoH did not return an IP, fall back to direct hostname navigation (may fail on Railway)
    nav_url = url
    use_ip = False
    if ip:
        use_ip = True
        nav_url = url.replace(hostname, ip)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--single-process",
                "--disable-gpu"
            ],
        )

        # Ignore HTTPS errors so navigation to IP with Host header works
        context = await browser.new_context(accept_downloads=True, ignore_https_errors=True)

        # If we will navigate to an IP, set Host header so server gets correct host
        if use_ip:
            await context.set_extra_http_headers({"Host": hostname})

        page = await context.new_page()

        try:
            await page.goto(nav_url, wait_until="networkidle", timeout=int(REQUEST_TIMEOUT * 1000))
            await page.wait_for_timeout(500)
            html = await page.content()
        except Exception as e:
            logger.error(f"Playwright navigation error for {url}: {e}")
            await browser.close()
            raise
        await browser.close()
        return html

# ---------------------------
# HTTP download with DoH fallback
# ---------------------------
async def download_asset(url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
    # Try normal request first
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.debug(f"Direct download failed for {url}: {e}")

    # DNS-over-HTTPS fallback
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise RuntimeError("Invalid URL for download")

    ip = await resolve_domain(hostname)
    if not ip:
        raise RuntimeError(f"DNS resolution failed for {hostname}")

    # Replace hostname with IP and set Host header
    url_ip = url.replace(hostname, ip)
    hdrs = headers.copy() if headers else {}
    hdrs.update({"Host": hostname})

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=False) as client:
        r = await client.get(url_ip, headers=hdrs)
        r.raise_for_status()
        return r.content

# ---------------------------
# Quiz parsing helpers
# ---------------------------
def extract_quiz_json(html: str) -> Optional[dict]:
    m = re.search(r"atob\s*\(\s*`([^`]*)`", html, re.DOTALL)
    if m:
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", "ignore").strip()
            return json.loads(decoded)
        except:
            pass
    p = re.search(r"<pre.*?>(.*?)</pre>", html, re.DOTALL | re.IGNORECASE)
    if p:
        try:
            return json.loads(p.group(1).strip())
        except:
            pass
    return None

def extract_submit_url(text: str) -> Optional[str]:
    m = re.search(r"https?://[^\s\"']*(submit[^\"'\s<]*)", text, re.I)
    return m.group(0) if m else None

def extract_download_url(text: str) -> Optional[str]:
    m = re.search(r"https?://[^\s\"']+\.(csv|xlsx|xls|json|pdf)", text, re.I)
    return m.group(0) if m else None

def sum_value_column_from_pdf(pdf_bytes: bytes, page_index: int = 1) -> Optional[float]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if page_index >= len(pdf.pages):
            return None
        page = pdf.pages[page_index]
        tables = page.extract_tables() or []
        for tbl in tables:
            df = pd.DataFrame(tbl)
            header_row = df.iloc[0].astype(str).str.strip().str.lower().tolist()
            df = df[1:]
            df.columns = header_row
            for col in df.columns:
                if col in ("value", "values"):
                    vals = pd.to_numeric(df[col], errors="coerce").dropna()
                    return float(vals.sum())
    return None

async def submit_answer(submit_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        r = await client.post(submit_url, json=payload)
        r.raise_for_status()
        try:
            return r.json()
        except:
            return {"raw": r.text}

# ---------------------------
# Solver logic
# ---------------------------
async def solve_once(email: str, secret: str, quiz_url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    html = await fetch_rendered_html(quiz_url)
    quiz_json = extract_quiz_json(html)
    text = re.sub(r"<[^>]+>", " ", html)

    submit_url = extract_submit_url(html) or extract_submit_url(text)
    download_url = extract_download_url(text)

    answer = None

    if quiz_json and "answer" in quiz_json:
        answer = quiz_json["answer"]

    if download_url and download_url.lower().endswith(".pdf"):
        try:
            pdf_bytes = await download_asset(download_url)
            s = sum_value_column_from_pdf(pdf_bytes)
            if s is not None:
                answer = s
        except Exception as e:
            logger.error(e)

    if answer is None and download_url and re.search(r"\.(csv|xlsx|xls)$", download_url, re.I):
        try:
            data = await download_asset(download_url)
            if download_url.lower().endswith(".csv"):
                df = pd.read_csv(io.BytesIO(data))
            else:
                df = pd.read_excel(io.BytesIO(data))
            cols = [c for c in df.columns if str(c).strip().lower() in {"value", "values"}]
            if cols:
                answer = float(pd.to_numeric(df[cols[0]], errors="coerce").dropna().sum())
        except Exception as e:
            logger.error(e)

    if not submit_url:
        raise RuntimeError("Submit URL not found in quiz page.")

    payload = {
        "email": email,
        "secret": secret,
        "url": quiz_url,
        "answer": answer if answer is not None else "UNABLE_TO_DETERMINE"
    }

    result = await submit_answer(submit_url, payload)
    next_url = result.get("url") if isinstance(result, dict) else None
    return result, next_url

async def solve_until_done(ctx: SolveContext, first_url: str):
    deadline_at = ctx.start_ts + ctx.deadline_s
    current_url = first_url
    last_result = None

    while time.time() < deadline_at and current_url:
        try:
            last_result, next_url = await solve_once(ctx.email, ctx.secret, current_url)
            logger.info({"url": current_url, "result": last_result})
            if last_result and last_result.get("correct") and not next_url:
                break
            current_url = next_url
            if not current_url:
                break
        except Exception as e:
            logger.error(f"Solve error for {current_url}: {e}")
            break

@app.post("/quiz")
async def quiz_endpoint(request: Request, background: BackgroundTasks):
    try:
        data = await request.json()
    except:
        raise HTTPException(400, "Invalid JSON")

    try:
        q = QuizRequest(**data)
    except Exception as e:
        raise HTTPException(400, f"Invalid payload: {e}")

    if q.secret != SERVER_SECRET:
        raise HTTPException(403, "Invalid secret")

    resp = {"ok": True}
    ctx = SolveContext(email=q.email or SERVER_EMAIL, secret=q.secret, start_ts=time.time(), deadline_s=SOLVER_DEADLINE)
    background.add_task(solve_until_done, ctx, q.url)
    return JSONResponse(resp)

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT")))

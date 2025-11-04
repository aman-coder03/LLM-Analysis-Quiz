# LLM-Analysis-Quiz


FastAPI endpoint that:
- Verifies `secret` (403 on mismatch)
- Validates JSON (400 on invalid)
- Renders quiz pages with Playwright (JS/DOM)
- Solves common data tasks (PDF/CSV/etc.)
- Submits the answer to the page-provided `submit` URL
- Supports chain URLs within the same 3-minute window

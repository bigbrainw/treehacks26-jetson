"""
PDF context pipeline: page content → agent analyzes stuck points → short help summary.

Background processing: when user is on a PDF page, we have the actual text.
Use agent to identify likely stuck points and generate page-specific help.
"""

from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None


STUCK_ANALYSIS_PROMPT = """You analyze academic/technical content to identify what might confuse or block a reader.

Given the EXACT page content below, produce a SHORT (2-4 sentences) help summary.
- Identify the 1-2 most likely stuck points (dense concepts, jargon, unclear logic)
- Explain them briefly in plain language
- No questions. Only deliver the explanation.
- Keep under 150 words total.

Page content:
---
{page_content}
---

Output ONLY the help summary text, no JSON, no preamble."""


def analyze_page_for_stuck_points(
    page_content: str,
    doc_name: str,
    page_num: int,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[str]:
    """
    Use agent to analyze page content and produce a short help summary.
    Returns the summary text or None.
    """
    if not page_content or len(page_content.strip()) < 20:
        return None
    if not Anthropic or not api_key:
        return None

    client = Anthropic(api_key=api_key)

    prompt = STUCK_ANALYSIS_PROMPT.format(
        page_content=page_content[:3000]  # cap to avoid overflow
    )

    try:
        resp = client.messages.create(
            model=model or "claude-3-5-sonnet-20241022",
            max_tokens=512,
            system="You produce concise, helpful explanations. No fluff.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = (resp.content[0].text or "").strip()
        return text if text else None
    except Exception:
        return None


def build_pdf_prepared_resources(
    page_content: Optional[str],
    doc_name: str,
    page_num: int,
    reading_section: Optional[str],
    web_related: Optional[str],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Build prepared_resources for PDF context: agent analysis + web search.
    """
    parts = []

    if page_content and api_key:
        analysis = analyze_page_for_stuck_points(
            page_content, doc_name, page_num, api_key, model
        )
        if analysis:
            parts.append(f"Page-specific help (from analysis of page {page_num}):")
            parts.append(analysis)
            parts.append("")

    if web_related:
        parts.append(web_related)

    return "\n".join(parts).strip() if parts else ""

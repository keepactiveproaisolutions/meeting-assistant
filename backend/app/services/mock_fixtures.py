"""LLM_MOCK=true stub for zero-cost UI testing. Clearly labelled; contains no personal or prepared answers."""


def pick(full_user_prompt: str) -> str:
    question = full_user_prompt.rsplit("## Question to answer now", 1)[-1].strip()[:300]
    return (
        "**[MOCK MODE — not a real answer]** LLM_MOCK is enabled, so no model was called for: "
        f"“{question}”.\n\n"
        "### Technical details\n"
        "- This stub exercises Markdown, code and diagram rendering only.\n\n"
        "```sql\nSELECT 1 AS mock_mode;\n```\n\n"
        "```mermaid\nflowchart LR\n  A[\"Client\"] --> B[\"Backend (mock)\"] --> C[\"UI\"]\n```\n"
    )

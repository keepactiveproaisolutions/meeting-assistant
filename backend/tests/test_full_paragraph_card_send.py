import asyncio
import os
import sys
from pathlib import Path

# Ensure mock LLM mode for testing
os.environ["LLM_MOCK"] = "true"

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal
from app.core.dev_auth import ensure_dev_identity
from app.models import AIAnswer, Meeting
from app.rag.pipeline import build_context
from app.services.meeting_service import snapshot

TEST_PARAGRAPH = (
    "Beep Beep Tell me something Loose turned up... What are the variables in the list? "
    "What are the variables in the Python? Can we level it up? "
    "Need to bless Lambda. Not to the Lambda, can I?"
)

async def test_full_paragraph_reaches_model_input():
    print("=== REGRESSION TEST: FULL PARAGRAPH MODEL-INPUT VERIFICATION ===")
    
    async with SessionLocal() as db:
        dev_user, dev_ws = await ensure_dev_identity(db)
        
        # Create a test meeting
        meeting = Meeting(
            workspace_id=dev_ws.id,
            created_by=dev_user.id,
            title="Regression Test Full Paragraph",
            code="TESTFULL1",
            status="live"
        )
        db.add(meeting)
        await db.flush()
        
        # Build RAG context with the exact user paragraph
        ctx = await build_context(
            db=db,
            meeting=meeting,
            question=TEST_PARAGRAPH,
            selected_text=None,
            user_id=dev_user.id
        )
        
        messages = ctx.messages
        system_prompt = messages[0]["content"]
        user_prompt = messages[-1]["content"]  # the current question is always the last message

        # 1. Verify user prompt contains the complete input paragraph without truncating Lambda
        assert "## Question to answer now\n" + TEST_PARAGRAPH in user_prompt, \
            "User prompt must contain the exact, entire paragraph!"

        assert "Need to bless Lambda. Not to the Lambda, can I?" in user_prompt, \
            "Lambda portion of the paragraph must not be truncated!"

        assert user_prompt.rstrip().endswith(TEST_PARAGRAPH.rstrip()), \
            "The current question must be the very end of the final message!"

        # 2. Verify system prompt instructions use general multi-question & context rules (no example-specific rules)
        assert "Consider the ENTIRE \"Question to answer now\" paragraph" in system_prompt, \
            "System prompt must instruct model to consider the entire paragraph!"

        assert "Answer EVERY question in the card" in system_prompt, \
            "System prompt must instruct model to answer every question!"

        assert "never skip one" in system_prompt, \
            "System prompt must instruct model not to skip a question!"

        assert "Use the full context of the paragraph" in system_prompt, \
            "System prompt must instruct model to use full context to interpret terms!"

        assert "Do not force an answer to unclear or garbled speech" in system_prompt, \
            "System prompt must instruct model not to force answers to unclear speech!"

        assert "AWS Lambda" not in system_prompt, \
            "System prompt must NOT contain example-specific AWS Lambda rules!"

        # 3. Verify complete paragraph is persisted in AIAnswer and returned by snapshot()
        ans = AIAnswer(
            meeting_id=meeting.id,
            asked_by=dev_user.id,
            question_text=TEST_PARAGRAPH,
            answer_text="Mock answer for verification.",
            model="mock",
            status="completed",
        )
        db.add(ans)
        await db.flush()

        snap = await snapshot(db, meeting)
        assert len(snap.answers) == 1
        assert snap.answers[0].question_text == TEST_PARAGRAPH, \
            "Answer history snapshot must preserve the complete submitted paragraph!"

        await db.rollback()

        print("[CHECK 1] User message preserves full paragraph: verified.")
        print("[CHECK 2] End of paragraph ('Need to bless Lambda. Not to the Lambda, can I?') intact: verified.")
        print("[CHECK 3] System prompt general multi-question & context rules (no AWS Lambda rule): verified.")
        print("[CHECK 4] AIAnswer DB persistence & snapshot() preserve complete paragraph: verified.")
        print("\nALL FULL-PARAGRAPH REGRESSION CHECKS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_full_paragraph_reaches_model_input())

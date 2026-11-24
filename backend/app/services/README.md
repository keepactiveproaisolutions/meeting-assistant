# services

Business logic: `openai_service` (the only OpenAI caller), `document_service` (extract, chunk, embed, store), `meeting_service` (meeting codes, transcript ingestion, snapshots), `answer_service` (RAG + streamed answers), `question_detector`.


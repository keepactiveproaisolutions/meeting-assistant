# API design

Base URL `http://localhost:8000`. REST prefix `/api/v1`. Auth: `Authorization: Bearer <jwt>`. Interactive docs at `/docs`.

## REST
| Method & path | Auth | Description |
|---|---|---|
| POST `/auth/register` | – | `{email,password,full_name,workspace_name?}` → token + user; creates a default workspace |
| POST `/auth/login` | – | `{email,password}` → token + user |
| GET `/auth/me` | user | Current user |
| GET/POST `/workspaces` | user | List / create workspaces |
| POST `/workspaces/{id}/members` | owner/admin | Add existing user by email |
| GET/POST `/workspaces/{id}/documents` | member | List / upload (multipart `file`; PDF, DOCX, TXT, MD). Returns 202; status goes `pending → processing → ready/failed` |
| DELETE `/workspaces/{id}/documents/{doc}` | member | Delete document and chunks |
| GET/POST `/workspaces/{id}/meetings` | member | List / create meeting (returns `code`, `join_url`) |
| POST `/meetings/join` | user | `{code}` → meeting; grants participant access |
| GET `/meetings/{id}` | member/participant | Snapshot: meeting, segments, questions, answers+sources |
| POST `/meetings/{id}/audio` | member | Multipart `file` (WAV), `chunk_index`, `offset_ms`, `duration_ms` |
| POST `/meetings/{id}/ask` | member/participant | `{question, selected_text?, question_id?}` → 202 `{answer_id}`; answer streams over WS |
| POST `/meetings/{id}/end` | member | End meeting |

## WebSocket `/ws/meetings/{meeting_id}?token=<jwt>`
Envelope: `{"type","meeting_id","data","ts"}`. Closed with 1008 on bad token / no access.

Server → client:
| type | data |
|---|---|
| `session.snapshot` | `{meeting, segments[], questions[], answers[]}` sent on connect (use for reconnect recovery) |
| `session.presence` | `{connections}` |
| `transcript.segment` | `{id, chunk_index, start_offset_ms, end_offset_ms, text, is_question, created_at}` |
| `question.detected` | `{id, segment_id, text, confidence}` |
| `answer.started` | `{answer_id, question_id, question_text, context_text}` |
| `answer.sources` | `{answer_id, sources:[{id, source_type, label, snippet, offset_ms, score}]}` |
| `answer.delta` | `{answer_id, delta}` |
| `answer.completed` | `{answer_id, answer_text}` |
| `answer.failed` | `{answer_id, message}` |
| `meeting.ended` | `{}` |
| `pong`, `error` | – |

Client → server: `{"type":"ping"}`, `{"type":"ask","data":{"question","selected_text"}}`.

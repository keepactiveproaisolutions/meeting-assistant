# api

REST routers: `auth` (register/login/me), `workspaces`, `documents` (upload/list/delete), `meetings` (create, join by code, snapshot, audio ingest, ask, end) and `ws` (WebSocket `/ws/meetings/{id}`). Routers stay thin and delegate to `services/`.


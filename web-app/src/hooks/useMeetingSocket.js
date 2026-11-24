import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, meetingSocketUrl } from "../services/api";

const initial = {
  meeting: null,
  segments: [],
  utterances: [],
  questionBySegment: {},
  answers: [],
  ended: false,
};

function reducer(state, { type, data }) {
  switch (type) {
    case "session.snapshot":
    case "session.init": {
      const local = Object.fromEntries(state.answers.map((a) => [a.id, a]));
      const merged = (data.answers || []).map((a) => {
        const mine = local[a.id];
        // Never let a snapshot shorten an answer that is still streaming here.
        if (mine && a.status === "streaming" && (mine.answer_text || "").length > (a.answer_text || "").length
            && (mine.answer_text || "").startsWith(a.answer_text || "")) {
          return { ...mine, ...a, answer_text: mine.answer_text };
        }
        return { ...a, diagrams: a.diagrams || [], code_blocks: a.code_blocks || [] };
      });
      return {
        ...state,
        needsResync: false,
        meeting: data.meeting || state.meeting,
        segments: data.segments || [],
        utterances: data.utterances || [],
        questionBySegment: Object.fromEntries(
          (data.questions || []).map((q) => [q.segment_id, q])
        ),
        answers: merged,
        // Live mode provisional text still being spoken (replaced by final segments, never duplicated).
        partials: Object.fromEntries((data.partials || []).map((p) => [p.item_id, p])),
        ended: data.meeting?.status === "ended",
      };
    }
    case "transcript.partial": {
      const partials = { ...(state.partials || {}) };
      if (data.final || !data.text) delete partials[data.item_id];
      else partials[data.item_id] = data;
      return { ...state, partials };
    }
    case "transcript.utterance": {
      const exists = state.utterances.some((u) => u.id === data.id);
      const updated = exists
        ? state.utterances.map((u) => (u.id === data.id ? data : u))
        : [...state.utterances, data];
      // Finalized text replaces provisional words for the same audio in the SAME render (never shown twice).
      const partials = Object.fromEntries(Object.entries(state.partials || {})
        .filter(([, p]) => p.start_offset_ms >= (data.end_offset_ms ?? data.start_offset_ms)));
      return {
        ...state,
        partials,
        utterances: updated.sort((a, b) => a.start_offset_ms - b.start_offset_ms),
      };
    }
    case "transcript.segment":
      if (state.segments.some((s) => s.id === data.id)) return state;
      return {
        ...state,
        segments: [...state.segments, data].sort(
          (a, b) => a.start_offset_ms - b.start_offset_ms
        ),
      };
    case "question.detected":
      return {
        ...state,
        questionBySegment: { ...state.questionBySegment, [data.segment_id]: data },
      };
    case "answer.started":
      if (state.answers.some((a) => a.id === data.answer_id)) return state;
      return {
        ...state,
        answers: [
          ...state.answers,
          {
            id: data.answer_id,
            question_text: data.question_text,
            answer_text: "",
            status: "streaming",
            sources: [],
            diagrams: [],
            code_blocks: [],
          },
        ],
      };
    case "answer.sources":
      return patch(state, data.answer_id, () => ({ sources: data.sources }));
    case "answer.delta": {
      // Each delta carries its character offset: skip text we already have, and resync if we missed some.
      const cur = state.answers.find((a) => a.id === data.answer_id);
      if (!cur) return { ...state, needsResync: true };
      const have = (cur.answer_text || "").length;
      const off = typeof data.offset === "number" ? data.offset : have;
      if (off + data.delta.length <= have) return state; // duplicate
      if (off > have) return { ...state, needsResync: true }; // gap: fetch persisted state
      return patch(state, data.answer_id, (a) => ({
        answer_text: (a.answer_text || "") + data.delta.slice(have - off),
      }));
    }
    case "answer.diagram":
      return patch(state, data.answer_id, (a) => {
        const existing = a.diagrams || [];
        const d = data.diagram;
        if (!d) return {};
        const filtered = existing.filter((x) => x.index !== d.index);
        return { diagrams: [...filtered, d].sort((x, y) => x.index - y.index) };
      });
    case "answer.completed":
      return patch(state, data.answer_id, (a) => ({
        answer_text: data.answer_text ?? a.answer_text,
        spoken_markdown: data.spoken_markdown ?? a.spoken_markdown,
        spoken_clean: data.spoken_clean ?? a.spoken_clean,
        technical_markdown: data.technical_markdown ?? a.technical_markdown,
        how_to_draw_markdown: data.how_to_draw_markdown ?? a.how_to_draw_markdown,
        diagrams: data.diagrams || a.diagrams || [],
        code_blocks: data.code_blocks || a.code_blocks || [],
        design_state: data.design_state ?? a.design_state,
        first_word_ms: data.first_word_ms ?? a.first_word_ms,
        total_ms: data.total_ms ?? a.total_ms,
        status: "completed",
      }));
    case "answer.failed":
      return patch(state, data.answer_id, () => ({ status: "failed" }));
    case "meeting.ended":
      return { ...state, ended: true };
    default:
      return state;
  }
}

const patch = (state, id, fn) => ({
  ...state,
  answers: state.answers.map((a) => (a.id === id ? { ...a, ...fn(a) } : a)),
});

/** Connects to the meeting room, keeps state in sync, auto-reconnects (snapshot is re-sent on connect). */
export function useMeetingSocket(meetingId) {
  const [state, dispatch] = useReducer(reducer, initial);
  const [connection, setConnection] = useState("connecting");
  const [presence, setPresence] = useState(1);
  const [socketError, setSocketError] = useState("");
  const wsRef = useRef(null);
  const [reconnectTick, setReconnectTick] = useState(0);

  const fetchSnapshotFallback = useCallback(() => {
    if (!meetingId) return;
    api
      .getMeeting(meetingId)
      .then((snap) => {
        if (snap) {
          dispatch({ type: "session.snapshot", data: snap });
          setSocketError("");
        }
      })
      .catch((err) => {
        setSocketError(err.message || "Unable to load meeting snapshot.");
      });
  }, [meetingId]);

  useEffect(() => {
    fetchSnapshotFallback();
  }, [fetchSnapshotFallback, reconnectTick]);

  useEffect(() => {
    let closed = false;
    let retry = 1000;
    let timer;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(meetingSocketUrl(meetingId));
      wsRef.current = ws;
      ws.onopen = () => {
        setConnection("connected");
        setSocketError("");
        retry = 1000;
      };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (closed || (msg.meeting_id && msg.meeting_id !== meetingId)) return; // never mix meetings
        if (msg.type === "session.presence") setPresence(msg.data.connections);
        else dispatch({ type: msg.type, data: msg.data });
      };
      ws.onerror = () => {
        if (!closed) {
          setSocketError(
            "Live WebSocket connection interrupted. Retrying automatically..."
          );
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setConnection("reconnecting");
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [meetingId, reconnectTick]);

  // A gap in an answer stream (missed deltas) -> reload persisted state instead of showing broken text.
  useEffect(() => {
    if (state.needsResync) fetchSnapshotFallback();
  }, [state.needsResync, fetchSnapshotFallback]);

  // Phone locked / app switched: when the page becomes visible again, reload persisted state.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") fetchSnapshotFallback();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [fetchSnapshotFallback]);

  const retryConnection = useCallback(() => {
    setConnection("connecting");
    setSocketError("");
    setReconnectTick((n) => n + 1);
  }, []);

  return {
    ...state,
    connection,
    presence,
    socketError,
    retryConnection,
  };
}

"use client";

import { useEffect, useRef, useCallback, useState } from "react";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

interface UseWSOptions<T> {
  path: string;
  onMessage: (data: T) => void;
}

export function useWebSocket<T>({ path, onMessage }: UseWSOptions<T>) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  const retryCountRef = useRef(0);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    const url = `${WS_BASE}${path}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retryCountRef.current = 0; // Reset on successful connection
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        onMessageRef.current(msg);
      } catch { /* ignore bad JSON */ }
    };

    ws.onclose = () => {
      setConnected(false);
      // First retry: 100ms, then 200ms, 400ms, 800ms, 1.6s, 3.2s, max 15s
      const delay = Math.min(100 * Math.pow(2, retryCountRef.current), 15000);
      retryCountRef.current += 1;
      setTimeout(() => {
        if (wsRef.current === ws) connect();
      }, delay);
    };

    ws.onerror = () => ws.close();
  }, [path]);

  useEffect(() => {
    connect();
    return () => {
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [connect]);

  return { connected };
}

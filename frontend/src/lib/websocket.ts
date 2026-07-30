"use client";

import { useGuardioStore } from "@/store/useGuardioStore";
import type {
  GuardioEvent,
  PacketEvent,
  AlertEvent,
  AttackEvent,
  NodeUpdateEvent,
  ThreatLevelEvent,
  DefenseActionEvent,
  DroppedEvent,
  HoneypotEvent,
  InfraTelemetryEvent,
  StateEvent,
  ArcDatum,
  NodeData,
  ThreatStatus,
} from "@/types/events";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "wss://YOUR-BACKEND-DOMAIN.vercel.app/ws";

const RECONNECT_BASE = 1000;
const RECONNECT_MAX = 16000;

let socket: WebSocket | null = null;
let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = RECONNECT_BASE;
let stopped = false;

function arcColor(color: string): string {
  const map: Record<string, string> = {
    blue: "#00C2FF",
    red: "#FF6B6B",
    orange: "#FF9500",
    yellow: "#FFCC66",
    purple: "#A78BFA",
    gray: "#4B5563",
    green: "#7CFFB2",
  };
  return map[color] ?? "#00C2FF";
}

function handleEvent(raw: GuardioEvent) {
  const store = useGuardioStore.getState();

  switch (raw.type) {
    case "state": {
      const ev = raw as StateEvent;
      if (ev.topology) {
        store.setTopology(ev.topology as Record<string, NodeData>);
      }
      if (typeof ev.running === "boolean") store.setRunning(ev.running);
      if (ev.threat_level !== undefined && ev.threat_status) {
        store.setThreatLevel(ev.threat_level, ev.threat_status as ThreatStatus);
      }
      if (typeof ev.clients === "number") store.setClientCount(ev.clients);
      break;
    }

    case "packet": {
      const ev = raw as PacketEvent;
      store.addPacket(ev);
      if (ev.color !== "blue") {
        const arc: ArcDatum = {
          id: `${ev.ts}-${ev.src}-${ev.dst}`,
          srcLat: ev.src_lat,
          srcLng: ev.src_lng,
          dstLat: ev.dst_lat,
          dstLng: ev.dst_lng,
          color: arcColor(ev.color),
          protocol: ev.protocol,
          ts: Date.now(),
        };
        store.addArc(arc);
      }
      break;
    }

    case "attack": {
      const ev = raw as AttackEvent;
      store.addAttackEvent(ev);
      if (ev.stage === "start") {
        store.addIncident({
          id: Math.random().toString(36).slice(2),
          ts: ev.ts,
          type: "attack",
          severity: "high",
          title: `Attack started: ${ev.name.toUpperCase()}`,
          detail: String((ev.details as Record<string, unknown>).phase ?? ""),
          raw: ev.details as Record<string, unknown>,
        });
      } else if (ev.stage === "update") {
        const d = ev.details as Record<string, unknown>;
        const phase = String(d.phase ?? "");
        if (["flood", "saturated", "encrypting", "exfiltration", "ransom_demand"].includes(phase)) {
          store.addIncident({
            id: Math.random().toString(36).slice(2),
            ts: ev.ts,
            type: "attack_phase",
            severity: phase === "saturated" || phase === "ransom_demand" ? "critical" : "high",
            title: `${ev.name.toUpperCase()} — ${phase.replace(/_/g, " ")}`,
            detail: String(d.message ?? ""),
            raw: d,
          });
        }
      }
      break;
    }

    case "node_update": {
      const ev = raw as NodeUpdateEvent;
      store.updateNode(ev.node, {
        state: ev.state,
        load: ev.load,
        lat: ev.lat,
        lng: ev.lng,
      });
      if (["compromised", "encrypted", "offline"].includes(ev.state)) {
        store.addIncident({
          id: Math.random().toString(36).slice(2),
          ts: ev.ts,
          type: "node_state",
          severity: ev.state === "encrypted" ? "critical" : ev.state === "compromised" ? "high" : "medium",
          title: `${ev.name} → ${ev.state.toUpperCase()}`,
          detail: `Load: ${Math.round(ev.load * 100)}% · Zone: ${ev.zone}`,
          raw: { node: ev.node, state: ev.state, load: ev.load },
        });
      }
      break;
    }

    case "threat_level": {
      const ev = raw as ThreatLevelEvent;
      store.setThreatLevel(ev.level, ev.status);
      break;
    }

    case "alert": {
      const ev = raw as AlertEvent;
      store.addAlert(ev);
      store.addIncident({
        id: ev.alert_id,
        ts: ev.ts,
        type: "alert",
        severity: ev.level,
        title: `IDS Alert [${ev.level.toUpperCase()}]: ${ev.reason}`,
        detail: `${ev.src} → ${ev.dst} · ${ev.protocol} · score ${ev.score}`,
        raw: ev as unknown as Record<string, unknown>,
      });
      if (ev.level === "critical") {
        const arc: ArcDatum = {
          id: `alert-${ev.alert_id}`,
          srcLat: ev.src_lat,
          srcLng: ev.src_lng,
          dstLat: ev.dst_lat,
          dstLng: ev.dst_lng,
          color: "#FF6B6B",
          protocol: ev.protocol,
          ts: Date.now(),
        };
        store.addArc(arc);
      }
      break;
    }

    case "defense_action": {
      const ev = raw as DefenseActionEvent;
      store.addDefenseAction(ev);
      store.addIncident({
        id: Math.random().toString(36).slice(2),
        ts: ev.ts,
        type: "defense",
        severity: "low",
        title: `Defense: ${ev.action.replace(/_/g, " ")}`,
        detail: String((ev.details as Record<string, unknown>).message ?? ev.action),
        raw: ev as unknown as Record<string, unknown>,
      });
      break;
    }

    case "dropped": {
      const ev = raw as DroppedEvent;
      store.incrementDropped();
      if (ev.reason === "firewall") {
        store.addIncident({
          id: Math.random().toString(36).slice(2),
          ts: ev.ts,
          type: "dropped",
          severity: "low",
          title: `Packet dropped: ${ev.reason}`,
          detail: `${ev.src} → ${ev.dst}`,
          raw: ev as unknown as Record<string, unknown>,
        });
      }
      break;
    }

    case "honeypot": {
      const ev = raw as HoneypotEvent;
      store.addIncident({
        id: Math.random().toString(36).slice(2),
        ts: ev.ts,
        type: "honeypot",
        severity: "medium",
        title: `Honeypot triggered`,
        detail: `${ev.src} → ${ev.dst}`,
        raw: ev as unknown as Record<string, unknown>,
      });
      break;
    }

    case "infra_telemetry": {
      const ev = raw as InfraTelemetryEvent;
      for (const n of ev.nodes) {
        store.updateNode(n.node, { load: n.load, state: n.state });
      }
      break;
    }

    case "ping":
    case "pong":
      break;
  }
}

export function connectWebSocket() {
  if (socket?.readyState === WebSocket.OPEN) return;
  stopped = false;

  const store = useGuardioStore.getState();

  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    store.setConnected(true);
    reconnectDelay = RECONNECT_BASE;
  };

  socket.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data) as GuardioEvent;
      handleEvent(data);
    } catch {
      // non-JSON frame, ignore
    }
  };

  socket.onclose = () => {
    store.setConnected(false);
    socket = null;
    if (!stopped) scheduleReconnect();
  };

  socket.onerror = () => {
    socket?.close();
  };
}

function scheduleReconnect() {
  reconnectTimeout = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
    connectWebSocket();
  }, reconnectDelay);
}

export function disconnectWebSocket() {
  stopped = true;
  if (reconnectTimeout) clearTimeout(reconnectTimeout);
  socket?.close();
  socket = null;
}

export function sendPing() {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send("ping");
  }
}

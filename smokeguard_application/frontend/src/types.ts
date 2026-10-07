// ---------------------------------------------------------------------------
// WebSocket message types (server → client)
// ---------------------------------------------------------------------------

export interface WsWelcome {
  type: 'welcome';
  source: string;
  num_subcarriers: number;
  started_at: number;
}

export interface CsiFrame {
  type: 'csi';
  t: number;
  seq: number;
  mac: string;
  rssi: number;
  channel: number;
  bandwidth: number;
  mcs: number;
  noise_floor: number;
  ant: number;
  length: number;
  subcarrier_count: number;
  i: number[];
  q: number[];
  metadata: SubcarrierMetadata | null;
}

export interface SubcarrierMetadata {
  // Data runs in array order (ESP-IDF FFT-bin order: index 0 is the DC null,
  // then the subcarrier numbers wrap to negatives) — not by sign.
  data_start_idx: number;
  data_end_idx: number;
  dc_null_idx: number;
  data_upper_start: number;
  data_upper_end: number;
  total_pairs: number;
  /** First negative subcarrier: sc = idx < wrap ? idx : idx - total_pairs */
  subcarrier_wrap_idx: number;
}

export interface WsStatus {
  type: 'status';
  mqtt: 'connected' | 'disconnected' | 'reconnecting';
  packets: number;
  dropped_lines: number;
  last_record_at: number | null;
  error: string | null;
  receiver_online?: boolean | null;
  ntp_synced?: boolean | null;
}

export interface SmokeSample {
  type: 'smoke';
  t: number;
  pm1_0: number;
  pm2_5: number;
  pm10: number;
  cnt0_3: number;
  cnt0_5: number;
  cnt1_0: number;
  cnt2_5: number;
  cnt5_0: number;
  cnt10: number;
  // BME680 block — omitted by the backend when legacy firmware sends
  // 11-column lines (exclude_none), so these are optional
  temp_c?: number;
  pressure_hpa?: number;
  humidity_pct?: number;
  gas_kohm?: number;
  altitude_m?: number;
  rssi: number;
}

export interface WsAlert {
  type: 'alert';
  id: number;
  detected_at: number;
}

export type WsMessage = WsWelcome | CsiFrame | SmokeSample | WsStatus | WsAlert | { type: 'pong' };

// ---------------------------------------------------------------------------
// Detection API types (REST /api/detection/*)
// ---------------------------------------------------------------------------

export interface DetectionConfig {
  enabled: boolean;
}

export interface DetectionEventSummary {
  id: number;
  detected_at: number;
  csi_frames: number;
  smoke_samples: number;
}

export interface DetectionEventsResponse {
  year: number;
  month: number;
  count: number;
  events: DetectionEventSummary[];
}

export interface DetectionCsiFrame {
  t: number;
  amp: number[];
}

export interface DetectionCsiData {
  subcarrier_count: number;
  active_indices: number[];
  frames: DetectionCsiFrame[];
}

export interface DetectionSmokeSample {
  timestamp_real: number;
  pm1_0: number;
  pm2_5: number;
  pm10: number;
  cnt0_3: number;
  cnt0_5: number;
  cnt1_0: number;
  cnt2_5: number;
  cnt5_0: number;
  cnt10: number;
  temp_c: number | null;
  pressure_hpa: number | null;
  humidity_pct: number | null;
  gas_kohm: number | null;
  altitude_m: number | null;
  rssi: number;
}

export interface DetectionEventDetail {
  id: number;
  detected_at: number;
  csi_frames: number;
  smoke_samples: number;
  csi: DetectionCsiData;
  smoke: { samples: DetectionSmokeSample[] };
}

// ---------------------------------------------------------------------------
// Application state
// ---------------------------------------------------------------------------

export interface MetricsSnapshot {
  rssi: number;
  noiseFloor: number;
  packetCount: number;
  packetsPerSecond: number;
  seq: number;
}

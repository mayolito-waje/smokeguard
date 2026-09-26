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
  data_start_idx: number;
  data_end_idx: number;
  dc_null_idx: number;
  data_upper_start: number;
  data_upper_end: number;
  total_pairs: number;
  subcarrier_index_offset: number;
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

export type WsMessage = WsWelcome | CsiFrame | SmokeSample | WsStatus | { type: 'pong' };

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

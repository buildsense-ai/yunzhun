export interface BoardRef {
  provider: string;
  bucket: string;
  key: string;
}

export interface BoardCard {
  message_id: number;
  account_id: number;
  subject: string;
  from: string;
  date: string | null;
  category: string | null;
  storage_delivery: number | null;
  refs: BoardRef[];
  pull: { done: number; failed: number; bytes: number };
}

export interface BoardColumn {
  key: string;
  title: string;
  hint?: string;
  disabled?: boolean;
  count: number;
  cards: BoardCard[];
}

export interface BoardResponse {
  columns: BoardColumn[];
  stats: {
    messages: number;
    delivery: number;
    files_done: number;
    files_failed: number;
    bytes: number;
  };
}

export interface PullRecord {
  id: number;
  remote_key: string;
  local_path: string;
  size: number;
  status: string;
  error: string | null;
  pulled_at: string;
}

export function useApi() {
  const cfg = useRuntimeConfig().public;
  return $fetch.create({
    baseURL: cfg.apiBase as string,
    headers: { "X-API-Key": cfg.apiKey as string },
  });
}

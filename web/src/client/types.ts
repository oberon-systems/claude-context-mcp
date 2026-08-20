export type Project = {
  name: string;
  type: string;
  root_path: string;
  indexed_at: string | null;
  stale_seconds: number | null;
  nodes: number;
  edges: number;
  files: number;
  plans: number;
};

export type ProjectDetail = Project & {
  types: { type: string; count: number }[];
  relations: { relation_type: string; count: number }[];
  manual_summaries: number;
  summarised: number;
  hashed_files: number;
  embeddings: number;
};

export type DropReport = {
  name: string;
  root_path: string;
  indexed_at: string | null;
  nodes: number;
  edges: number;
  hashes: number;
  embeddings: number;
  plans: number;
  suggestions: number;
  summaries: number;
  dropped: boolean;
};

export type NodeRow = {
  id: string;
  name: string;
  type: string;
  file_path: string | null;
  summary: string | null;
};

export type NodeDetail = NodeRow & {
  metadata: Record<string, unknown>;
  created_at: string;
  content: string | null;
  content_length: number;
  content_truncated: boolean;
};

export type Neighbor = {
  node_id: string;
  relation_type: string;
  direction: "incoming" | "outgoing";
  type: string | null;
  file_path: string | null;
  summary: string | null;
};

export type FileRow = {
  id: string;
  file_path: string | null;
  summary: string | null;
  entities: number;
  hash: string | null;
  hash_updated_at: string | null;
};

export type PlanRow = {
  id: string;
  project: string | null;
  title: string;
  status: string;
  type: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  content_length: number;
};

export type Plan = Omit<PlanRow, "content_length"> & { content: string };

export type PlanFacets = {
  projects: string[];
  statuses: string[];
  types: string[];
  global_plans: number;
};

export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type SuggestionRow = {
  id: string;
  title: string;
  summary: string | null;
  about: string | null;
  kind: string | null;
  lever: string | null;
  status: string;
  hits: number;
  first_seen: string | null;
  last_seen: string | null;
  created_at: string;
  detail_length: number;
};

export type Suggestion = Omit<SuggestionRow, "detail_length"> & {
  detail: string;
};

export type SuggestionFacets = {
  abouts: string[];
  statuses: string[];
  kinds: string[];
  global_suggestions: number;
};

export type IndexJob = {
  id: number;
  project: string;
  status: "running" | "done" | "failed";
  files: number | null;
  with_node: number | null;
  entities: number | null;
  edges: number | null;
  failures: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type ProjectSource = {
  alias: string;
  root_path: string;
  // Where the last index run read this directory's selection from: "file" for
  // a .ctxkeep still in the tree, "directory" / "project" / "global" for a
  // stored row, "default" for the built-in set. Null until first indexed.
  keep_source: string | null;
  ignore_source: string | null;
};

// One level of the selection: a directory, the project, or the global
// default. Either document may be null, which is that level declining to
// speak for it and letting the level above answer.
export type SettingsLevel = {
  ctxkeep: string | null;
  ctxignore: string | null;
  updated_at: string | null;
};

export type SettingsSource = ProjectSource & SettingsLevel;

export type ProjectSettings = {
  sources: SettingsSource[];
  project: SettingsLevel | null;
  global: SettingsLevel | null;
};

// What a scan of one directory proposes, and what that proposal would select.
export type ScanResult = {
  project: string;
  alias: string;
  ctxkeep: string;
  ctxignore: string;
  report: string;
};

export type FileType = {
  extension: string;
  count: number;
};

export type Project = {
  name: string;
  type: string;
  root_path: string;
  // What the project reads. One entry with an empty alias is a project
  // mounted whole; several named ones are the slices it was assembled from,
  // and each alias opens every node id that directory produced.
  sources: ProjectSource[];
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

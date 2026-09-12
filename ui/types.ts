export type RuntimeState = 'online' | 'stopped' | 'errored' | 'unknown';
export type ControlMode = 'client' | 'database_manager' | 'unknown';
export type StartMode = 'client' | 'database_manager';
export type OperationKind = 'lifecycle' | 'selected' | 'update_all';

export interface OdooClient {
  name: string;
  release: string | null;
  environment: string | null;
  local_url: string | null;
}

export interface OdooIdentity {
  client: string;
  release: string;
  environment: string;
  config_identity: string | null;
  database: string | null;
  http_port: number | null;
  longpolling_port: number | null;
}

export type SafeOdooIdentity = Omit<OdooIdentity, 'config_identity'>;

export interface OdooStatus {
  state: RuntimeState;
  pid: number | null;
  process_name: string | null;
  pm2_id: number | null;
  startup_mode: string | null;
}

export interface OdooControl {
  control_mode: ControlMode;
  lifecycle_eligible: boolean;
  reason: string | null;
}

export interface OdooModule {
  name: string;
  version: string | null;
  installed: boolean;
  installable: boolean | null;
  update_available: boolean;
  dependencies: string[];
}

export interface OdooDatabase {
  name: string;
  exists: boolean;
}

export interface OdooSnapshotError {
  code: string;
}

export interface OdooSnapshotClient extends OdooClient {
  registry_identity: string;
}

export interface OdooSelectedSnapshot {
  registry_identity: string;
  identity: SafeOdooIdentity | null;
  status: OdooStatus | null;
  control: OdooControl | null;
  modules: OdooModule[] | null;
  databases: OdooDatabase[] | null;
}

export interface OdooSnapshotEnvelope {
  protocol_version: 1;
  process_instance_id: string;
  registry_epoch: number;
  registry_identity: string;
  releases: { version: string }[];
  releases_error: OdooSnapshotError | null;
  clients?: OdooSnapshotClient[];
  client?: string;
  snapshot?: OdooSelectedSnapshot;
  errors: Record<string, Record<string, OdooSnapshotError>>;
}

export interface OdooLogEntry {
  timestamp: string | null;
  pid: number | null;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | null;
  database: string | null;
  logger: string | null;
  message: string;
}

export interface OdooLogPage {
  entries: OdooLogEntry[];
  next_cursor: string | null;
  previous_cursor?: string | null;
  has_more: boolean;
  cursor_reset?: boolean;
  has_previous?: boolean;
  stale?: boolean;
}

export interface OdooReadModel {
  clients: OdooClient[];
  releases?: { version: string }[];
  identity?: SafeOdooIdentity;
  status?: OdooStatus;
  control?: OdooControl;
  modules?: OdooModule[];
  databases?: OdooDatabase[];
  logs?: OdooLogPage;
}

export interface OdooMutationPrecondition {
  registry_identity: string;
  process_instance_id: string;
  registry_epoch: number;
}

export interface OdooApi {
  getSnapshot?: (client?: string, signal?: AbortSignal) => Promise<OdooSnapshotEnvelope>;
  listClients(signal?: AbortSignal): Promise<{ clients: OdooClient[] }>;
  listReleases(signal?: AbortSignal): Promise<{ releases: { version: string }[] }>;
  getIdentity(client: string, signal?: AbortSignal): Promise<{ instance: OdooIdentity }>;
  getStatus(client: string, signal?: AbortSignal): Promise<{ status: OdooStatus }>;
  getControl(client: string, signal?: AbortSignal): Promise<{ control: OdooControl }>;
  getModules(client: string, signal?: AbortSignal): Promise<{ client: string; modules: OdooModule[] }>;
  getDatabases(client: string, signal?: AbortSignal): Promise<{ client: string; databases: OdooDatabase[] }>;
  getLogs(client: string, options?: { cursor?: string | null; direction?: 'older' | 'newer'; limit?: number; signal?: AbortSignal }): Promise<OdooLogPage>;
  start(client: string, confirmation: string, mode?: StartMode, signal?: AbortSignal, precondition?: OdooMutationPrecondition): Promise<unknown>;
  stop(client: string, confirmation: string, signal?: AbortSignal, precondition?: OdooMutationPrecondition): Promise<unknown>;
  restart(client: string, confirmation: string, selector?: { modules: string[] } | { update_all: true }, mode?: StartMode, signal?: AbortSignal, precondition?: OdooMutationPrecondition): Promise<unknown>;
}

export interface OdooWorkspaceData extends OdooReadModel {
  selectedClient?: string;
}

export interface PluginManifest {
  id: string;
  name: string;
  description: string;
  version: string;
  enabled: boolean;
  routePath: string;
  navItem: { to: string; label: string; icon: string; order?: number };
  endpoints: { method: 'GET' | 'POST' | 'PUT' | 'DELETE'; path: string; handler: string; authRequired: boolean }[];
}

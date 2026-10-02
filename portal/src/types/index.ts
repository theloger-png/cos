export type NodeStatus = 'online' | 'offline' | 'error'
export type VMStatus = 'running' | 'stopped' | 'starting' | 'stopping' | 'paused' | 'migrating' | 'error'
export type NetworkStatus = 'active' | 'inactive' | 'error'

export interface Node {
  id: string
  hostname: string
  ip_address: string
  status: NodeStatus
  cpu_total: number
  cpu_used: number
  ram_total_mb: number
  ram_used_mb: number
  disk_total_gb: number
  disk_used_gb: number
  last_heartbeat: string | null
  created_at: string
  updated_at: string
}

export interface VM {
  id: string
  name: string
  status: VMStatus
  node_id: string | null
  node_hostname?: string
  template_id: string | null
  cpu_cores: number
  ram_mb: number
  disk_gb: number
  tenant_id: string | null
  created_at: string
  updated_at: string
}

export interface VMCreateRequest {
  name: string
  template_id?: string | null
  node_id?: string | null
  cpu_cores: number
  ram_mb: number
  disk_gb: number
  tenant_id?: string | null
  network_id?: string | null
  ip_cidr?: string
  gateway?: string
}

export interface VMCreateResponse extends VM {
  cloud_init_user: string | null
  cloud_init_password: string | null
}

export interface VMPasswordReset {
  user: string
  password: string
}

export interface Template {
  id: string
  name: string
  os_type: string
  image_path: string
  image_url: string | null
  cpu_cores: number
  ram_mb: number
  disk_gb: number
  description: string | null
  cloud_init_user: string
  created_at: string
  updated_at: string
}

export interface TemplateCreateRequest {
  name: string
  os_type: string
  image_path?: string
  image_url?: string
  cpu_cores: number
  ram_mb: number
  disk_gb: number
  description?: string
  cloud_init_user?: string
}

export interface NodeFetchResult {
  node_id: string
  hostname: string
  success: boolean
  error: string | null
}

export interface FetchImageResponse {
  image_path: string
  results: NodeFetchResult[]
}

export interface Network {
  id: string
  name: string
  vlan_id: number | null
  cidr: string | null
  gateway: string | null
  status: NetworkStatus
  tenant_id: string | null
  created_at: string
  updated_at: string
}

export interface NetworkCreateRequest {
  name: string
  vlan_id?: number
  cidr?: string
  gateway?: string
  tenant_id?: string
}

export interface Tenant {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface TenantCreateRequest {
  name: string
  email: string
  description?: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export interface DiskInfo {
  target: string
  size_gb: number
  path: string
  device: string  // "disk" | "cdrom"
}

export interface NICInfo {
  target: string
  mac: string
  bridge: string
  vlan_id: number | null
  ip_addresses: string[]
  network_id: string | null
  network_name: string | null
}

export interface NICFailure {
  target: string
  reason: string
}

export interface VMHardwareConfig {
  vcpu: number
  memory_mb: number
  disks: DiskInfo[]
  nics: NICInfo[]
  nic_failures: NICFailure[]
}

export interface VMHardwareChanges {
  vcpu?: number | null
  memory_mb?: number | null
  add_disks: { size_gb: number }[]
  add_nics: { network_id: string; ip_cidr?: string; gateway?: string }[]
  remove_nics: { target: string }[]
}

export interface ConsoleTicketResponse {
  ticket: string
  expires_in: number
}

export type OperationStatus = 'running' | 'success' | 'failed'

export interface Operation {
  id: string
  user: string
  action: string
  description: string
  target_type: string | null
  target_id: string | null
  target_name: string | null
  status: OperationStatus
  /** null = running with no measurable progress (indeterminate bar). */
  progress: number | null
  message: string | null
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface OperationFilters {
  status?: OperationStatus
  user?: string
  /** Action prefix, e.g. "vm" or "vm.create". */
  action?: string
}

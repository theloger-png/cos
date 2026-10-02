import type {
  VM,
  VMCreateRequest,
  VMCreateResponse,
  VMHardwareConfig,
  VMHardwareChanges,
  ConsoleTicketResponse,
  VMPasswordReset,
} from '@/types'
import client from './client'

export async function getVMs(): Promise<VM[]> {
  const { data } = await client.get<VM[]>('/api/v1/vms')
  return data
}

export async function getVM(id: string): Promise<VM> {
  const { data } = await client.get<VM>(`/api/v1/vms/${id}`)
  return data
}

export async function createVM(payload: VMCreateRequest): Promise<VMCreateResponse> {
  const { data } = await client.post<VMCreateResponse>('/api/v1/vms', payload)
  return data
}

export async function startVM(id: string): Promise<VM> {
  const { data } = await client.post<VM>(`/api/v1/vms/${id}/start`)
  return data
}

export async function stopVM(id: string): Promise<VM> {
  const { data } = await client.post<VM>(`/api/v1/vms/${id}/stop`)
  return data
}

export async function forceStopVM(id: string): Promise<VM> {
  const { data } = await client.post<VM>(`/api/v1/vms/${id}/force-stop`)
  return data
}

export async function rebootVM(id: string): Promise<VM> {
  const { data } = await client.post<VM>(`/api/v1/vms/${id}/reboot`)
  return data
}

export async function resetVMPassword(id: string): Promise<VMPasswordReset> {
  const { data } = await client.post<VMPasswordReset>(`/api/v1/vms/${id}/reset-password`)
  return data
}

export async function deleteVM(id: string): Promise<void> {
  await client.delete(`/api/v1/vms/${id}`)
}

export async function migrateVM(id: string, targetNodeId: string): Promise<VM> {
  const { data } = await client.post<VM>(`/api/v1/vms/${id}/migrate`, { target_node_id: targetNodeId })
  return data
}

export async function getVMHardware(id: string): Promise<VMHardwareConfig> {
  const { data } = await client.get<VMHardwareConfig>(`/api/v1/vms/${id}/hardware`)
  return data
}

export async function applyVMHardware(id: string, changes: VMHardwareChanges): Promise<VMHardwareConfig> {
  const { data } = await client.put<VMHardwareConfig>(`/api/v1/vms/${id}/hardware`, changes)
  return data
}

/**
 * Request a one-time, 30-second console ticket for a running VM. The
 * WebSocket URL itself is built separately (see VMConsole.tsx) from
 * window.location, not from this client's baseURL - the console connects
 * through nginx's same-origin /api/ WebSocket proxy, not directly at the
 * controller.
 */
export async function getConsoleTicket(id: string): Promise<ConsoleTicketResponse> {
  const { data } = await client.post<ConsoleTicketResponse>(`/api/v1/vms/${id}/console-ticket`)
  return data
}

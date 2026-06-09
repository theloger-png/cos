import { Server, Monitor, MemoryStick, Cpu } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusBadge } from '@/components/StatusBadge'
import { useNodes } from '@/hooks/useNodes'
import { useVMs } from '@/hooks/useVMs'
import { formatDate } from '@/utils/format'

const mockTimelineData = Array.from({ length: 12 }, (_, i) => ({
  time: `${String(i * 2).padStart(2, '0')}:00`,
  nodes: Math.floor(Math.random() * 3) + 1,
}))

function StatCard({
  title,
  value,
  sub,
  icon: Icon,
}: {
  title: string
  value: string
  sub: string
  icon: React.ElementType
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-[var(--muted-foreground)]">{title}</CardTitle>
        <Icon className="h-4 w-4 text-[var(--muted-foreground)]" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-[var(--muted-foreground)] mt-1">{sub}</p>
      </CardContent>
    </Card>
  )
}

export function Dashboard() {
  const { data: nodes = [] } = useNodes()
  const { data: vms = [] } = useVMs()

  const onlineNodes = nodes.filter((n) => n.status === 'online').length
  const runningVMs = vms.filter((v) => v.status === 'running').length

  const totalRam = nodes.reduce((s, n) => s + n.ram_total_mb, 0)
  const usedRam = nodes.reduce((s, n) => s + n.ram_used_mb, 0)
  const ramPct = totalRam > 0 ? Math.round((usedRam / totalRam) * 100) : 0

  const totalCpu = nodes.reduce((s, n) => s + n.cpu_total, 0)
  const usedCpu = nodes.reduce((s, n) => s + n.cpu_used, 0)
  const cpuPct = totalCpu > 0 ? Math.round((usedCpu / totalCpu) * 100) : 0

  const recentVMs = [...vms].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5)

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold">Dashboard</h2>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard
          title="Total Nodes"
          value={`${onlineNodes}/${nodes.length}`}
          sub="online / total"
          icon={Server}
        />
        <StatCard
          title="Virtual Machines"
          value={`${runningVMs}/${vms.length}`}
          sub="running / total"
          icon={Monitor}
        />
        <StatCard
          title="RAM Used"
          value={`${ramPct}%`}
          sub={`${Math.round(usedRam / 1024)} / ${Math.round(totalRam / 1024)} GB`}
          icon={MemoryStick}
        />
        <StatCard
          title="CPU Used"
          value={`${cpuPct}%`}
          sub={`${usedCpu} / ${totalCpu} cores`}
          icon={Cpu}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Nodes Online (24h)</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={mockTimelineData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="time" stroke="var(--muted-foreground)" tick={{ fontSize: 11 }} />
              <YAxis stroke="var(--muted-foreground)" tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip
                contentStyle={{
                  background: 'var(--popover)',
                  border: '1px solid var(--border)',
                  borderRadius: 6,
                  color: 'var(--popover-foreground)',
                }}
              />
              <Line type="monotone" dataKey="nodes" stroke="#3b82f6" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Recent Virtual Machines</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recentVMs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-[var(--muted-foreground)] py-8">
                    No VMs yet
                  </TableCell>
                </TableRow>
              ) : (
                recentVMs.map((vm) => (
                  <TableRow key={vm.id}>
                    <TableCell className="font-medium">{vm.name}</TableCell>
                    <TableCell><StatusBadge status={vm.status} /></TableCell>
                    <TableCell>{vm.cpu_cores} cores</TableCell>
                    <TableCell>{vm.ram_mb >= 1024 ? `${vm.ram_mb / 1024} GB` : `${vm.ram_mb} MB`}</TableCell>
                    <TableCell className="text-[var(--muted-foreground)]">
                      {formatDate(vm.created_at)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

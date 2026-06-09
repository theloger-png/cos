import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import { Dashboard } from '@/pages/Dashboard'
import { Nodes } from '@/pages/Nodes'
import { NodeDetail } from '@/pages/NodeDetail'
import { VMs } from '@/pages/VMs'
import { VMCreate } from '@/pages/VMCreate'
import { Templates } from '@/pages/Templates'
import { Networks } from '@/pages/Networks'
import { Tenants } from '@/pages/Tenants'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/nodes" element={<Nodes />} />
            <Route path="/nodes/:id" element={<NodeDetail />} />
            <Route path="/vms" element={<VMs />} />
            <Route path="/vms/create" element={<VMCreate />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/networks" element={<Networks />} />
            <Route path="/tenants" element={<Tenants />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App

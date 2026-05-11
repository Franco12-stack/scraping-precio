import Link from 'next/link';
import { prisma } from '@/lib/prisma';
import { getSession } from '@/lib/session';
import AdminDeliveryList from '@/components/AdminDeliveryList';

export const dynamic = 'force-dynamic';

export default async function AdminPage() {
  const session = await getSession();
  const today = new Date().toISOString().split('T')[0];

  const [deliveries, drivers] = await Promise.all([
    prisma.delivery.findMany({
      where: { date: today },
      include: { driver: { select: { id: true, name: true } } },
      orderBy: [{ orderIndex: 'asc' }, { createdAt: 'asc' }],
    }),
    prisma.user.findMany({
      where: { role: 'DRIVER' },
      select: { id: true, name: true },
    }),
  ]);

  const stats = {
    total: deliveries.length,
    pending: deliveries.filter(d => d.status === 'PENDING' || d.status === 'ASSIGNED').length,
    transit: deliveries.filter(d => d.status === 'IN_TRANSIT').length,
    delivered: deliveries.filter(d => d.status === 'DELIVERED').length,
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Panel Admin</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {new Date().toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' })}
          </p>
        </div>
        <Link href="/admin/deliveries/new" className="btn-primary text-sm">
          + Nuevo envío
        </Link>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Total', value: stats.total, color: 'text-gray-700 bg-gray-50' },
          { label: 'Pendientes', value: stats.pending, color: 'text-blue-700 bg-blue-50' },
          { label: 'En tránsito', value: stats.transit, color: 'text-yellow-700 bg-yellow-50' },
          { label: 'Entregados', value: stats.delivered, color: 'text-green-700 bg-green-50' },
        ].map(s => (
          <div key={s.label} className={`rounded-xl p-4 ${s.color}`}>
            <p className="text-2xl font-bold">{s.value}</p>
            <p className="text-xs font-medium mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      <AdminDeliveryList
        deliveries={JSON.parse(JSON.stringify(deliveries))}
        drivers={drivers}
      />
    </div>
  );
}

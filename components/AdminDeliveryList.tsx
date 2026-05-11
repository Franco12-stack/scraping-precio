'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

interface Driver { id: string; name: string }
interface Delivery {
  id: string;
  customerName: string;
  customerPhone: string;
  customerAddress: string;
  status: string;
  notes: string | null;
  orderIndex: number;
  driverId: string | null;
  driver: { id: string; name: string } | null;
  notifiedEnCamino: boolean;
  notifiedCerca: boolean;
}

const STATUS_LABELS: Record<string, string> = {
  PENDING: 'Sin asignar',
  ASSIGNED: 'Asignado',
  IN_TRANSIT: 'En tránsito',
  DELIVERED: 'Entregado',
  FAILED: 'Fallido',
};
const STATUS_BADGE: Record<string, string> = {
  PENDING: 'badge-pending',
  ASSIGNED: 'badge-assigned',
  IN_TRANSIT: 'badge-transit',
  DELIVERED: 'badge-delivered',
  FAILED: 'badge-failed',
};

export default function AdminDeliveryList({
  deliveries: initial,
  drivers,
}: {
  deliveries: Delivery[];
  drivers: Driver[];
}) {
  const router = useRouter();
  const [deliveries, setDeliveries] = useState(initial);
  const [filter, setFilter] = useState('ALL');

  async function assignDriver(deliveryId: string, driverId: string) {
    const res = await fetch(`/api/deliveries/${deliveryId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ driverId: driverId || null, status: driverId ? 'ASSIGNED' : 'PENDING' }),
    });
    if (res.ok) {
      router.refresh();
      const updated = await res.json();
      setDeliveries(prev => prev.map(d => (d.id === deliveryId ? { ...d, ...updated } : d)));
    }
  }

  async function deleteDelivery(deliveryId: string) {
    if (!confirm('¿Eliminar este envío?')) return;
    await fetch(`/api/deliveries/${deliveryId}`, { method: 'DELETE' });
    setDeliveries(prev => prev.filter(d => d.id !== deliveryId));
  }

  const filtered = filter === 'ALL' ? deliveries : deliveries.filter(d => d.status === filter);

  return (
    <div>
      {/* Filtros */}
      <div className="flex gap-2 overflow-x-auto pb-1 mb-4">
        {['ALL', 'PENDING', 'ASSIGNED', 'IN_TRANSIT', 'DELIVERED', 'FAILED'].map(s => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`text-xs font-medium px-3 py-1.5 rounded-full whitespace-nowrap transition-colors ${
              filter === s ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            {s === 'ALL' ? 'Todos' : STATUS_LABELS[s]}
          </button>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <p className="text-4xl mb-2">📦</p>
          <p>No hay envíos para mostrar</p>
        </div>
      )}

      <div className="space-y-3">
        {filtered.map(d => (
          <div key={d.id} className="card">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3 flex-1 min-w-0">
                <div className="flex-shrink-0 w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-sm font-bold flex items-center justify-center">
                  {d.orderIndex || '—'}
                </div>
                <div className="min-w-0">
                  <p className="font-semibold text-gray-900 truncate">{d.customerName}</p>
                  <p className="text-sm text-gray-500 truncate">{d.customerAddress}</p>
                  <p className="text-sm text-gray-400">{d.customerPhone}</p>
                  {d.notes && <p className="text-xs text-gray-400 mt-1 italic">{d.notes}</p>}
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    <span className={STATUS_BADGE[d.status]}>{STATUS_LABELS[d.status]}</span>
                    {d.notifiedEnCamino && <span className="badge-assigned">📱 Notificado</span>}
                    {d.notifiedCerca && <span className="badge-delivered">📍 Cerca</span>}
                  </div>
                </div>
              </div>
              <button
                onClick={() => deleteDelivery(d.id)}
                className="text-gray-300 hover:text-red-500 transition-colors flex-shrink-0 p-1"
                title="Eliminar"
              >
                ✕
              </button>
            </div>

            <div className="mt-3 pt-3 border-t border-gray-50">
              <label className="block text-xs text-gray-500 mb-1">Chofer</label>
              <select
                className="input text-sm"
                value={d.driverId ?? ''}
                onChange={e => assignDriver(d.id, e.target.value)}
              >
                <option value="">Sin asignar</option>
                {drivers.map(dr => (
                  <option key={dr.id} value={dr.id}>{dr.name}</option>
                ))}
              </select>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

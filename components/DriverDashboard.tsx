'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';

const DeliveryMap = dynamic(() => import('./DeliveryMap'), {
  ssr: false,
  loading: () => (
    <div className="h-72 bg-gray-100 rounded-xl flex items-center justify-center">
      <p className="text-gray-400 text-sm">Cargando mapa...</p>
    </div>
  ),
});

interface Delivery {
  id: string;
  customerName: string;
  customerPhone: string;
  customerAddress: string;
  lat: number | null;
  lng: number | null;
  status: string;
  notes: string | null;
  orderIndex: number;
  notifiedEnCamino: boolean;
  notifiedCerca: boolean;
}

const STATUS_LABELS: Record<string, string> = {
  PENDING: 'Sin asignar',
  ASSIGNED: 'Pendiente',
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

export default function DriverDashboard({
  driverName,
  deliveries: initial,
}: {
  driverName: string;
  deliveries: Delivery[];
}) {
  const router = useRouter();
  const [deliveries, setDeliveries] = useState(initial);
  const [tab, setTab] = useState<'lista' | 'mapa'>('lista');
  const [driverPos, setDriverPos] = useState<{ lat: number; lng: number } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [gpsError, setGpsError] = useState('');
  const watchId = useRef<number | null>(null);

  // GPS tracking: watch position and send to server every 30s
  useEffect(() => {
    if (!navigator.geolocation) {
      setGpsError('GPS no disponible en este dispositivo');
      return;
    }

    let lastSend = 0;

    watchId.current = navigator.geolocation.watchPosition(
      pos => {
        const { latitude: lat, longitude: lng } = pos.coords;
        setDriverPos({ lat, lng });
        const now = Date.now();
        if (now - lastSend > 30000) {
          lastSend = now;
          fetch('/api/location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat, lng }),
          }).catch(() => {});
        }
      },
      err => {
        if (err.code === GeolocationPositionError.PERMISSION_DENIED) {
          setGpsError('Permiso de ubicación denegado. Activalo en configuración del navegador.');
        }
      },
      { enableHighAccuracy: true, maximumAge: 10000 },
    );

    return () => {
      if (watchId.current != null) navigator.geolocation.clearWatch(watchId.current);
    };
  }, []);

  const updateStatus = useCallback(async (id: string, status: string) => {
    const res = await fetch(`/api/deliveries/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (res.ok) {
      const updated = await res.json();
      setDeliveries(prev => prev.map(d => (d.id === id ? { ...d, ...updated } : d)));
    }
  }, []);

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    router.push('/login');
    router.refresh();
  }

  const pending = deliveries.filter(d => d.status !== 'DELIVERED' && d.status !== 'FAILED');
  const done = deliveries.filter(d => d.status === 'DELIVERED' || d.status === 'FAILED');

  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      {/* Header */}
      <header className="bg-blue-700 text-white px-4 py-3 flex items-center justify-between sticky top-0 z-20">
        <div>
          <p className="font-bold text-lg leading-tight">🚚 {driverName}</p>
          <p className="text-blue-200 text-xs">
            {driverPos ? `📍 GPS activo` : gpsError ? '⚠️ Sin GPS' : '📡 Esperando GPS...'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm bg-blue-600 px-2 py-1 rounded-full">
            {pending.length} pendientes
          </span>
          <button onClick={handleLogout} className="text-blue-200 hover:text-white text-sm">
            Salir
          </button>
        </div>
      </header>

      {gpsError && (
        <div className="bg-yellow-50 border-b border-yellow-200 px-4 py-2 text-xs text-yellow-800">
          ⚠️ {gpsError}
        </div>
      )}

      {/* Tabs */}
      <div className="flex bg-white border-b border-gray-200 sticky top-[57px] z-10">
        {(['lista', 'mapa'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-3 text-sm font-medium transition-colors ${
              tab === t
                ? 'text-blue-700 border-b-2 border-blue-700'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'lista' ? '📋 Envíos' : '🗺️ Mapa'}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-auto">
        {/* MAP TAB */}
        {tab === 'mapa' && (
          <div className="p-4">
            <DeliveryMap deliveries={deliveries} driverPos={driverPos} selectedId={selectedId} />
            <p className="text-xs text-gray-400 text-center mt-2">
              Tocá un marcador para ver los detalles del envío
            </p>
          </div>
        )}

        {/* LIST TAB */}
        {tab === 'lista' && (
          <div className="p-4 space-y-4">
            {deliveries.length === 0 && (
              <div className="text-center py-16 text-gray-400">
                <p className="text-5xl mb-3">🎉</p>
                <p className="font-medium">No tenés envíos asignados para hoy</p>
              </div>
            )}

            {/* Envíos pendientes */}
            {pending.length > 0 && (
              <section>
                <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Pendientes ({pending.length})
                </h2>
                <div className="space-y-3">
                  {pending.map(d => (
                    <DeliveryCard
                      key={d.id}
                      delivery={d}
                      onStatusChange={updateStatus}
                      onSelect={() => { setSelectedId(d.id); setTab('mapa'); }}
                    />
                  ))}
                </div>
              </section>
            )}

            {/* Completados */}
            {done.length > 0 && (
              <section>
                <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Completados ({done.length})
                </h2>
                <div className="space-y-3">
                  {done.map(d => (
                    <DeliveryCard
                      key={d.id}
                      delivery={d}
                      onStatusChange={updateStatus}
                      onSelect={() => { setSelectedId(d.id); setTab('mapa'); }}
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function DeliveryCard({
  delivery: d,
  onStatusChange,
  onSelect,
}: {
  delivery: Delivery;
  onStatusChange: (id: string, status: string) => void;
  onSelect: () => void;
}) {
  const isDone = d.status === 'DELIVERED' || d.status === 'FAILED';

  function openMaps() {
    const query = encodeURIComponent(d.customerAddress);
    window.open(`https://www.google.com/maps/search/?api=1&query=${query}`, '_blank');
  }

  function callPhone() {
    window.location.href = `tel:${d.customerPhone}`;
  }

  return (
    <div className={`card ${isDone ? 'opacity-60' : ''}`}>
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-start gap-2 min-w-0">
          <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">
            {d.orderIndex || '#'}
          </span>
          <div className="min-w-0">
            <p className="font-semibold text-gray-900">{d.customerName}</p>
            <p className="text-sm text-gray-500 truncate">{d.customerAddress}</p>
          </div>
        </div>
        <span className={`${STATUS_BADGE[d.status]} flex-shrink-0`}>
          {STATUS_LABELS[d.status]}
        </span>
      </div>

      {d.notes && (
        <p className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded mb-3 border border-amber-100">
          📝 {d.notes}
        </p>
      )}

      {(d.notifiedEnCamino || d.notifiedCerca) && (
        <div className="flex gap-1.5 mb-3">
          {d.notifiedEnCamino && <span className="badge-assigned">✓ WA enviado</span>}
          {d.notifiedCerca && <span className="badge-delivered">📍 Aviso de cercanía</span>}
        </div>
      )}

      {/* Acciones */}
      <div className="flex gap-2 flex-wrap">
        <button onClick={callPhone} className="flex-1 btn-secondary text-xs py-2 min-w-0">
          📞 Llamar
        </button>
        <button onClick={openMaps} className="flex-1 btn-secondary text-xs py-2 min-w-0">
          🗺️ Navegar
        </button>
        <button onClick={onSelect} className="flex-1 btn-secondary text-xs py-2 min-w-0">
          📌 Ver en mapa
        </button>
      </div>

      {!isDone && (
        <div className="flex gap-2 mt-2">
          {d.status !== 'IN_TRANSIT' && (
            <button
              onClick={() => onStatusChange(d.id, 'IN_TRANSIT')}
              className="flex-1 btn-primary text-xs py-2"
            >
              🚀 Salir hacia acá
            </button>
          )}
          <button
            onClick={() => onStatusChange(d.id, 'DELIVERED')}
            className="flex-1 bg-green-600 text-white font-semibold text-xs py-2 rounded-lg hover:bg-green-700 transition-colors"
          >
            ✅ Entregar
          </button>
          <button
            onClick={() => onStatusChange(d.id, 'FAILED')}
            className="bg-red-50 text-red-600 font-semibold text-xs py-2 px-3 rounded-lg hover:bg-red-100 transition-colors"
            title="No se pudo entregar"
          >
            ✗
          </button>
        </div>
      )}
    </div>
  );
}

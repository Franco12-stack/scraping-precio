'use client';

import { useState, FormEvent } from 'react';
import { useRouter } from 'next/navigation';

interface Driver { id: string; name: string }

export default function NewDeliveryForm({ drivers }: { drivers: Driver[] }) {
  const router = useRouter();
  const [form, setForm] = useState({
    customerName: '',
    customerPhone: '',
    customerAddress: '',
    notes: '',
    driverId: '',
    orderIndex: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function set(key: string, val: string) {
    setForm(prev => ({ ...prev, [key]: val }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/deliveries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          orderIndex: form.orderIndex ? parseInt(form.orderIndex) : 0,
          driverId: form.driverId || undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error ?? 'Error al crear envío');
        return;
      }
      router.push('/admin');
      router.refresh();
    } catch {
      setError('Error de conexión');
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Nombre del cliente *</label>
          <input className="input" required value={form.customerName} onChange={e => set('customerName', e.target.value)} placeholder="Ej: María López" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Teléfono (WhatsApp) *</label>
          <input className="input" required value={form.customerPhone} onChange={e => set('customerPhone', e.target.value)} placeholder="+54911..." type="tel" />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Dirección de entrega *</label>
        <input className="input" required value={form.customerAddress} onChange={e => set('customerAddress', e.target.value)} placeholder="Av. Corrientes 1234, Buenos Aires" />
        <p className="text-xs text-gray-400 mt-1">Se geocodifica automáticamente con OpenStreetMap</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Chofer asignado</label>
          <select className="input" value={form.driverId} onChange={e => set('driverId', e.target.value)}>
            <option value="">Sin asignar</option>
            {drivers.map(d => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Orden de visita</label>
          <input className="input" type="number" min="0" value={form.orderIndex} onChange={e => set('orderIndex', e.target.value)} placeholder="1, 2, 3..." />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Notas</label>
        <textarea className="input resize-none" rows={3} value={form.notes} onChange={e => set('notes', e.target.value)} placeholder="Dejar en portería, llamar antes, etc." />
      </div>

      {error && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>}

      <div className="flex gap-3 pt-2">
        <button type="button" className="btn-secondary flex-1" onClick={() => router.back()}>
          Cancelar
        </button>
        <button type="submit" className="btn-primary flex-1" disabled={loading}>
          {loading ? 'Guardando...' : 'Crear envío'}
        </button>
      </div>
    </form>
  );
}

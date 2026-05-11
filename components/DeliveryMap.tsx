'use client';

import { useEffect, useRef } from 'react';
import type { Map, Marker, Polyline } from 'leaflet';

interface Delivery {
  id: string;
  customerName: string;
  customerAddress: string;
  lat: number | null;
  lng: number | null;
  status: string;
  orderIndex: number;
}

interface Props {
  deliveries: Delivery[];
  driverPos: { lat: number; lng: number } | null;
  selectedId: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  PENDING: '#6b7280',
  ASSIGNED: '#3b82f6',
  IN_TRANSIT: '#f59e0b',
  DELIVERED: '#22c55e',
  FAILED: '#ef4444',
};

export default function DeliveryMap({ deliveries, driverPos, selectedId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const markersRef = useRef<Record<string, Marker>>({});
  const driverMarkerRef = useRef<Marker | null>(null);
  const polylineRef = useRef<Polyline | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    // Default center: Buenos Aires
    const defaultCenter: [number, number] = [-34.6037, -58.3816];
    const firstWithCoords = deliveries.find(d => d.lat != null && d.lng != null);
    const center: [number, number] = firstWithCoords
      ? [firstWithCoords.lat!, firstWithCoords.lng!]
      : defaultCenter;

    // Dynamic import to avoid SSR issues
    import('leaflet').then(L => {
      // Fix default icon paths
      delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
        iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
        shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
      });

      if (!containerRef.current) return;
      const map = L.map(containerRef.current).setView(center, 13);
      mapRef.current = map;

      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19,
      }).addTo(map);

      // Add delivery markers
      const coords: [number, number][] = [];
      deliveries.forEach(d => {
        if (d.lat == null || d.lng == null) return;
        const color = STATUS_COLORS[d.status] ?? '#6b7280';
        const icon = L.divIcon({
          html: `<div style="
            background:${color};
            color:white;
            width:28px;height:28px;
            border-radius:50%;
            display:flex;align-items:center;justify-content:center;
            font-weight:bold;font-size:13px;
            border:2px solid white;
            box-shadow:0 2px 4px rgba(0,0,0,0.3);
          ">${d.orderIndex || '?'}</div>`,
          className: '',
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        });

        const marker = L.marker([d.lat, d.lng], { icon })
          .addTo(map)
          .bindPopup(`
            <div style="min-width:160px;">
              <p style="font-weight:600;margin:0 0 4px">#${d.orderIndex} ${d.customerName}</p>
              <p style="font-size:12px;color:#6b7280;margin:0 0 6px">${d.customerAddress}</p>
              <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(d.customerAddress)}"
                 target="_blank" style="font-size:12px;color:#2563eb;">Ver en Google Maps →</a>
            </div>
          `);

        markersRef.current[d.id] = marker;
        coords.push([d.lat, d.lng]);
      });

      // Draw route polyline connecting delivery points in order
      if (coords.length > 1) {
        polylineRef.current = L.polyline(coords, {
          color: '#3b82f6',
          weight: 3,
          opacity: 0.6,
          dashArray: '8, 6',
        }).addTo(map);
      }

      // Fit map to all markers
      if (coords.length > 0) {
        map.fitBounds(coords, { padding: [40, 40] });
      }
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        markersRef.current = {};
        driverMarkerRef.current = null;
        polylineRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update driver position marker
  useEffect(() => {
    if (!mapRef.current || !driverPos) return;
    import('leaflet').then(L => {
      const map = mapRef.current!;
      const truckIcon = L.divIcon({
        html: `<div style="
          background:#1d4ed8;
          width:36px;height:36px;
          border-radius:50%;
          display:flex;align-items:center;justify-content:center;
          font-size:18px;
          border:3px solid white;
          box-shadow:0 3px 8px rgba(0,0,0,0.4);
        ">🚚</div>`,
        className: '',
        iconSize: [36, 36],
        iconAnchor: [18, 18],
      });

      if (driverMarkerRef.current) {
        driverMarkerRef.current.setLatLng([driverPos.lat, driverPos.lng]);
      } else {
        driverMarkerRef.current = L.marker([driverPos.lat, driverPos.lng], { icon: truckIcon, zIndexOffset: 1000 })
          .addTo(map)
          .bindPopup('Tu ubicación actual');
      }
    });
  }, [driverPos]);

  // Pan to selected delivery
  useEffect(() => {
    if (!mapRef.current || !selectedId) return;
    const marker = markersRef.current[selectedId];
    if (marker) {
      mapRef.current.setView(marker.getLatLng(), 15, { animate: true });
      marker.openPopup();
    }
  }, [selectedId]);

  return (
    <>
      <link
        rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        crossOrigin=""
      />
      <div
        ref={containerRef}
        className="w-full rounded-xl overflow-hidden border border-gray-200"
        style={{ height: 'calc(100vh - 200px)', minHeight: '300px' }}
      />
    </>
  );
}

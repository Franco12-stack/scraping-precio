import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/prisma';
import { getSession } from '@/lib/session';
import { haversineMeters } from '@/lib/geo';
import { sendWhatsApp } from '@/lib/whatsapp';

const PROXIMITY_RADIUS = parseInt(process.env.PROXIMITY_RADIUS_METERS ?? '500', 10);

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session.userId || session.role !== 'DRIVER') {
    return NextResponse.json({ error: 'No autorizado' }, { status: 401 });
  }

  const { lat, lng } = await req.json();
  if (typeof lat !== 'number' || typeof lng !== 'number') {
    return NextResponse.json({ error: 'Coordenadas inválidas' }, { status: 400 });
  }

  // Actualizar ubicación del chofer
  await prisma.driverLocation.upsert({
    where: { driverId: session.userId },
    update: { lat, lng },
    create: { driverId: session.userId, lat, lng },
  });

  // Buscar envíos IN_TRANSIT del chofer sin notificación de proximidad
  const today = new Date().toISOString().split('T')[0];
  const pendingDeliveries = await prisma.delivery.findMany({
    where: {
      driverId: session.userId,
      date: today,
      status: 'IN_TRANSIT',
      notifiedCerca: false,
      lat: { not: null },
      lng: { not: null },
    },
  });

  const notified: string[] = [];
  for (const delivery of pendingDeliveries) {
    if (delivery.lat == null || delivery.lng == null) continue;
    const meters = haversineMeters(lat, lng, delivery.lat, delivery.lng);
    if (meters <= PROXIMITY_RADIUS) {
      const msg = `Hola ${delivery.customerName}! 📍 El chofer está a menos de ${PROXIMITY_RADIUS}m de tu domicilio. Prepárate para recibir tu pedido!`;
      const sent = await sendWhatsApp(delivery.customerPhone, msg);
      if (sent) {
        await prisma.delivery.update({
          where: { id: delivery.id },
          data: { notifiedCerca: true },
        });
        notified.push(delivery.id);
      }
    }
  }

  return NextResponse.json({ ok: true, notified });
}

export async function GET() {
  const session = await getSession();
  if (!session.userId || session.role !== 'ADMIN') {
    return NextResponse.json({ error: 'No autorizado' }, { status: 401 });
  }

  const locations = await prisma.driverLocation.findMany({
    include: { driver: { select: { id: true, name: true } } },
  });
  return NextResponse.json(locations);
}

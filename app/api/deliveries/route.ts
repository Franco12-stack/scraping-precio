import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/prisma';
import { getSession } from '@/lib/session';
import { geocodeAddress } from '@/lib/geo';

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session.userId) return NextResponse.json({ error: 'No autorizado' }, { status: 401 });

  const { searchParams } = new URL(req.url);
  const date = searchParams.get('date') ?? new Date().toISOString().split('T')[0];
  const driverId = searchParams.get('driverId');

  const where: Record<string, unknown> = { date };
  if (session.role === 'DRIVER') {
    where.driverId = session.userId;
  } else if (driverId) {
    where.driverId = driverId;
  }

  const deliveries = await prisma.delivery.findMany({
    where,
    include: { driver: { select: { id: true, name: true } } },
    orderBy: [{ orderIndex: 'asc' }, { createdAt: 'asc' }],
  });

  return NextResponse.json(deliveries);
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session.userId) return NextResponse.json({ error: 'No autorizado' }, { status: 401 });

  const body = await req.json();
  const { customerName, customerPhone, customerAddress, notes, driverId, date, orderIndex } = body;

  if (!customerName || !customerPhone || !customerAddress) {
    return NextResponse.json({ error: 'Datos del cliente requeridos' }, { status: 400 });
  }

  const today = date ?? new Date().toISOString().split('T')[0];

  // Geocodificar dirección
  const coords = await geocodeAddress(customerAddress);

  const delivery = await prisma.delivery.create({
    data: {
      customerName,
      customerPhone,
      customerAddress,
      lat: coords?.lat ?? null,
      lng: coords?.lng ?? null,
      notes: notes ?? null,
      driverId: driverId ?? (session.role === 'DRIVER' ? session.userId : null),
      status: driverId || session.role === 'DRIVER' ? 'ASSIGNED' : 'PENDING',
      date: today,
      orderIndex: orderIndex ?? 0,
    },
  });

  return NextResponse.json(delivery, { status: 201 });
}

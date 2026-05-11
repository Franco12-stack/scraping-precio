import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/prisma';
import { getSession } from '@/lib/session';
import { sendWhatsApp } from '@/lib/whatsapp';

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const session = await getSession();
  if (!session.userId) return NextResponse.json({ error: 'No autorizado' }, { status: 401 });

  const delivery = await prisma.delivery.findUnique({
    where: { id: params.id },
    include: { driver: { select: { id: true, name: true } } },
  });

  if (!delivery) return NextResponse.json({ error: 'No encontrado' }, { status: 404 });
  if (session.role === 'DRIVER' && delivery.driverId !== session.userId) {
    return NextResponse.json({ error: 'No autorizado' }, { status: 403 });
  }

  return NextResponse.json(delivery);
}

export async function PATCH(req: NextRequest, { params }: { params: { id: string } }) {
  const session = await getSession();
  if (!session.userId) return NextResponse.json({ error: 'No autorizado' }, { status: 401 });

  const delivery = await prisma.delivery.findUnique({ where: { id: params.id } });
  if (!delivery) return NextResponse.json({ error: 'No encontrado' }, { status: 404 });

  if (session.role === 'DRIVER' && delivery.driverId !== session.userId) {
    return NextResponse.json({ error: 'No autorizado' }, { status: 403 });
  }

  const body = await req.json();
  const prevStatus = delivery.status;
  const updated = await prisma.delivery.update({
    where: { id: params.id },
    data: {
      ...(body.status !== undefined && { status: body.status }),
      ...(body.driverId !== undefined && { driverId: body.driverId }),
      ...(body.notes !== undefined && { notes: body.notes }),
      ...(body.orderIndex !== undefined && { orderIndex: body.orderIndex }),
    },
  });

  // Notificación WhatsApp cuando el chofer sale hacia este destino
  if (
    prevStatus !== 'IN_TRANSIT' &&
    body.status === 'IN_TRANSIT' &&
    !updated.notifiedEnCamino
  ) {
    const msg = `Hola ${updated.customerName}! 🚚 Tu pedido está en camino. El chofer ya salió hacia tu domicilio: ${updated.customerAddress}`;
    const sent = await sendWhatsApp(updated.customerPhone, msg);
    if (sent) {
      await prisma.delivery.update({
        where: { id: params.id },
        data: { notifiedEnCamino: true },
      });
    }
  }

  return NextResponse.json(updated);
}

export async function DELETE(_req: NextRequest, { params }: { params: { id: string } }) {
  const session = await getSession();
  if (!session.userId || session.role !== 'ADMIN') {
    return NextResponse.json({ error: 'No autorizado' }, { status: 403 });
  }

  await prisma.delivery.delete({ where: { id: params.id } });
  return NextResponse.json({ ok: true });
}

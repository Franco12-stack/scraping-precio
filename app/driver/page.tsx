import { prisma } from '@/lib/prisma';
import { getSession } from '@/lib/session';
import DriverDashboard from '@/components/DriverDashboard';

export const dynamic = 'force-dynamic';

export default async function DriverPage() {
  const session = await getSession();
  const today = new Date().toISOString().split('T')[0];

  const deliveries = await prisma.delivery.findMany({
    where: { driverId: session.userId, date: today },
    orderBy: [{ orderIndex: 'asc' }, { createdAt: 'asc' }],
  });

  return (
    <DriverDashboard
      driverName={session.name}
      deliveries={JSON.parse(JSON.stringify(deliveries))}
    />
  );
}

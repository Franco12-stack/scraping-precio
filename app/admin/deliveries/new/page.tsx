import { prisma } from '@/lib/prisma';
import NewDeliveryForm from '@/components/NewDeliveryForm';

export default async function NewDeliveryPage() {
  const drivers = await prisma.user.findMany({
    where: { role: 'DRIVER' },
    select: { id: true, name: true },
    orderBy: { name: 'asc' },
  });

  return (
    <div className="max-w-lg mx-auto">
      <h1 className="text-xl font-bold text-gray-900 mb-6">Nuevo envío</h1>
      <NewDeliveryForm drivers={drivers} />
    </div>
  );
}

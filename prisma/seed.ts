import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';

const prisma = new PrismaClient();

async function main() {
  const adminPassword = await bcrypt.hash('admin123', 10);
  const driverPassword = await bcrypt.hash('chofer123', 10);

  const admin = await prisma.user.upsert({
    where: { email: 'admin@empresa.com' },
    update: {},
    create: {
      name: 'Administrador',
      email: 'admin@empresa.com',
      password: adminPassword,
      role: 'ADMIN',
      phone: '+5491100000000',
    },
  });

  const driver = await prisma.user.upsert({
    where: { email: 'chofer@empresa.com' },
    update: {},
    create: {
      name: 'Carlos García',
      email: 'chofer@empresa.com',
      password: driverPassword,
      role: 'DRIVER',
      phone: '+5491111111111',
    },
  });

  const today = new Date().toISOString().split('T')[0];

  const sampleDeliveries = [
    {
      driverId: driver.id,
      customerName: 'María López',
      customerPhone: '+5491122334455',
      customerAddress: 'Av. Corrientes 1234, Buenos Aires',
      lat: -34.6037,
      lng: -58.3816,
      status: 'ASSIGNED',
      date: today,
      orderIndex: 1,
      notes: 'Dejar en portería si no hay nadie',
    },
    {
      driverId: driver.id,
      customerName: 'Juan Pérez',
      customerPhone: '+5491166778899',
      customerAddress: 'Av. Santa Fe 2500, Buenos Aires',
      lat: -34.5958,
      lng: -58.3969,
      status: 'ASSIGNED',
      date: today,
      orderIndex: 2,
      notes: null,
    },
    {
      driverId: driver.id,
      customerName: 'Ana Martínez',
      customerPhone: '+5491155443322',
      customerAddress: 'Av. Cabildo 3100, Buenos Aires',
      lat: -34.5631,
      lng: -58.4578,
      status: 'ASSIGNED',
      date: today,
      orderIndex: 3,
      notes: 'Llamar antes de llegar',
    },
  ];

  for (const d of sampleDeliveries) {
    await prisma.delivery.create({ data: d });
  }

  console.log('Seed completado:');
  console.log('  Admin:  admin@empresa.com / admin123');
  console.log('  Chofer: chofer@empresa.com / chofer123');
}

main()
  .catch(console.error)
  .finally(() => prisma.$disconnect());

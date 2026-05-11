import { redirect } from 'next/navigation';
import { getSession } from '@/lib/session';
import AdminNav from '@/components/AdminNav';

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession();
  if (!session.userId) redirect('/login');
  if (session.role !== 'ADMIN') redirect('/driver');

  return (
    <div className="min-h-screen flex flex-col">
      <AdminNav userName={session.name} />
      <main className="flex-1 max-w-5xl mx-auto w-full px-4 py-6">{children}</main>
    </div>
  );
}

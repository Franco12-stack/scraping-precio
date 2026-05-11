import { redirect } from 'next/navigation';
import { getSession } from '@/lib/session';

export default async function DriverLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession();
  if (!session.userId) redirect('/login');
  if (session.role !== 'DRIVER') redirect('/admin');
  return <>{children}</>;
}

'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

export default function AdminNav({ userName }: { userName: string }) {
  const router = useRouter();

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    router.push('/login');
    router.refresh();
  }

  return (
    <header className="bg-white border-b border-gray-200 px-4 py-3 sticky top-0 z-10">
      <div className="max-w-5xl mx-auto flex items-center justify-between">
        <Link href="/admin" className="flex items-center gap-2 font-bold text-blue-700">
          <span className="text-xl">🚚</span>
          <span>LogiAdmin</span>
        </Link>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-500 hidden sm:block">{userName}</span>
          <button onClick={handleLogout} className="text-sm text-gray-500 hover:text-red-600 transition-colors">
            Salir
          </button>
        </div>
      </div>
    </header>
  );
}

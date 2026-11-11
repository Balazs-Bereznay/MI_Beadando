import { Suspense } from 'react';
import Observatory from '@/components/observatory';
export default function Page(){return <Suspense fallback={<p className="boot">Opening the observatory…</p>}><Observatory view="dashboard" /></Suspense>}
